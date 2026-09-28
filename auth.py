"""Autenticação por e-mail, persistida no mesmo Neon que já guarda os dados de
headcount (mesma `database_url` de `.streamlit/secrets.toml`, tabela própria
`app_users` — não mexe nas tabelas de dados). Espelha o fluxo do painel
Turnover Comercial (`.../Indicadores Parceiros/Turnover Comercial/src/auth.py`
e `src/auth_ui.py`), mas via psycopg2 puro em vez de sqlalchemy/st.connection,
pra não introduzir uma dependência nova só pra isso.

O acesso é liberado pelo DO inserindo o e-mail na tabela `app_users` (sem
senha — ver scripts/grant_access.py). No primeiro login a própria pessoa
define sua senha; nos acessos seguintes, ela só precisa digitar a senha.
Quem não tem o e-mail cadastrado não passa da primeira tela.

Trava por tentativas: depois de MAX_FAILED_ATTEMPTS senhas erradas seguidas,
a conta fica bloqueada por LOCKOUT_MINUTES — mitiga força bruta sem precisar
de nenhum serviço externo (rate-limit por e-mail, no próprio Postgres).
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Callable

import bcrypt
import pandas as pd
import psycopg2
import psycopg2.extras
import streamlit as st

SUPPORT_EMAIL = "rian.jesus@pacaembu.com"

# Nome do app + resumo mostrados na pill da tela de login (mesmo padrão do
# painel Turnover Comercial — ver _login_shell). Ajustar aqui pra reusar esse
# layout em outro painel.
APP_TITLE = "Headcount Pacaembu"
APP_SUMMARY = "Consulta de headcount, alocação de mão de obra e evolução por período da Pacaembu"

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

_LOGO_PATH = Path(__file__).parent / "assets" / "icone-headcount-transparente.png"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS app_users (
    email TEXT PRIMARY KEY,
    name TEXT,
    password_hash TEXT,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _database_url() -> str | None:
    """Mesma connection string usada pra ler os dados de headcount (ver app.py)."""
    try:
        return st.secrets["neon"]["database_url"]
    except Exception:
        return os.getenv("NEON_DATABASE_URL")


def _connect():
    return psycopg2.connect(_database_url())


@st.cache_resource
def init_db() -> None:
    # Só cria se não existir: o usuário do app (app_headcount) não tem permissão
    # de CREATE no schema — e o Postgres exige essa permissão mesmo num
    # "CREATE TABLE IF NOT EXISTS" de tabela já existente (migração 002, Neon).
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.app_users')")
        if cur.fetchone()[0] is None:
            cur.execute(CREATE_TABLE_SQL)
        conn.commit()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def normalize_email(email: str) -> str:
    return email.strip().lower()


def get_user(email: str) -> dict | None:
    with _connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT email, name, password_hash, failed_attempts, locked_until FROM app_users WHERE email = %s",
            (normalize_email(email),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def needs_password_setup(user: dict) -> bool:
    """True quando o e-mail tem acesso liberado mas ainda não definiu uma senha."""
    return not user.get("password_hash")


def is_locked(user: dict) -> bool:
    locked_until = user.get("locked_until")
    if locked_until is None:
        return False
    return locked_until > pd.Timestamp.now(tz="UTC")


def lock_remaining_minutes(user: dict) -> int:
    """Minutos restantes de bloqueio (arredondado pra cima) — só chamar se is_locked(user)."""
    remaining = (user["locked_until"] - pd.Timestamp.now(tz="UTC")).total_seconds()
    return max(1, int(-(-remaining // 60)))


def _register_failed_attempt(email: str) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE app_users
            SET failed_attempts = failed_attempts + 1,
                locked_until = CASE
                    WHEN failed_attempts + 1 >= %(max_attempts)s THEN now() + (%(lockout_minutes)s * interval '1 minute')
                    ELSE locked_until
                END,
                updated_at = now()
            WHERE email = %(email)s
            """,
            {"email": normalize_email(email), "max_attempts": MAX_FAILED_ATTEMPTS, "lockout_minutes": LOCKOUT_MINUTES},
        )
        conn.commit()


def _reset_failed_attempts(email: str) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE app_users SET failed_attempts = 0, locked_until = NULL, updated_at = now() WHERE email = %s",
            (normalize_email(email),),
        )
        conn.commit()


def verify_login(email: str, password: str) -> dict | None:
    user = get_user(email)
    if not user or not user.get("password_hash"):
        return None
    if is_locked(user):
        return None
    if check_password(password, user["password_hash"]):
        _reset_failed_attempts(email)
        return user
    _register_failed_attempt(email)
    return None


def set_initial_password(email: str, password: str) -> dict | None:
    email = normalize_email(email)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE app_users SET password_hash = %s, updated_at = now() WHERE email = %s",
            (hash_password(password), email),
        )
        conn.commit()
    return get_user(email)


# ── UI: mesmo fluxo em duas telas do Turnover Comercial (cartão dividido,
# marca à esquerda / formulário à direita) — cores trocadas pro navy da marca.


@st.cache_data
def _logo_b64() -> str:
    return base64.b64encode(_LOGO_PATH.read_bytes()).decode()


def _login_shell(title: str, subtitle: str, render_form: Callable[[], None]) -> None:
    with st.container(key="login_page"):
        _, mid, _ = st.columns([1, 2.3, 1])
        with mid:
            with st.container(border=True, key="login_card"):
                left, right = st.columns([1, 1.25])
                with left:
                    with st.container(key="login_left"):
                        st.html(
                            '<div class="login-logo-pill">'
                            f'<img src="data:image/png;base64,{_logo_b64()}" />'
                            f'<span class="login-pill-title">{APP_TITLE}</span>'
                            "</div>"
                            f'<p class="login-brand-sub">{APP_SUMMARY}</p>'
                        )
                with right:
                    with st.container(key="login_right"):
                        st.html(f'<div class="login-form-title">{title}</div><p class="login-form-sub">{subtitle}</p>')
                        render_form()


def _screen_email() -> None:
    def form() -> None:
        email = st.text_input("E-mail", label_visibility="collapsed", placeholder="seu.email@pacaembu.com")
        if st.button("Continuar", width="stretch"):
            normalized = normalize_email(email)
            if not normalized or "@" not in normalized:
                st.error("Informe um e-mail válido.")
            else:
                st.session_state["auth_email"] = normalized
                st.rerun()

    _login_shell("Entrar", "Digite seu e-mail corporativo para acessar o painel.", form)


def _screen_connection_error() -> None:
    def form() -> None:
        st.error("Não foi possível conectar ao banco de dados agora. Isso costuma ser passageiro — tente novamente em alguns segundos.")
        if st.button("Tentar novamente", width="stretch"):
            st.rerun()

    _login_shell("Erro temporário de conexão", "Não conseguimos falar com o banco de dados agora.", form)


def _screen_no_access(email: str) -> None:
    def form() -> None:
        st.warning(f"O e-mail **{email}** ainda não tem acesso a este painel. Solicite a inclusão para **{SUPPORT_EMAIL}**.")
        if st.button("Tentar outro e-mail", width="stretch"):
            st.session_state["auth_email"] = None
            st.rerun()

    _login_shell("Acesso não encontrado", "Esse e-mail ainda não está liberado.", form)


def _screen_set_password(user: dict) -> None:
    def form() -> None:
        password = st.text_input("Senha", type="password", placeholder="Crie uma senha (mín. 8 caracteres)")
        confirm = st.text_input("Confirmar senha", type="password", placeholder="Digite a senha de novo")
        if st.button("Criar senha e entrar", width="stretch"):
            if len(password) < 8:
                st.error("A senha precisa ter pelo menos 8 caracteres.")
            elif password != confirm:
                st.error("As senhas não coincidem.")
            else:
                st.session_state["auth_user"] = set_initial_password(user["email"], password)
                st.rerun()

    name = user.get("name") or user["email"]
    _login_shell(f"Olá, {name}", "Este é seu primeiro acesso — crie uma senha.", form)


def _screen_login(user: dict) -> None:
    def form() -> None:
        if is_locked(user):
            minutos = lock_remaining_minutes(user)
            st.warning(f"Conta temporariamente bloqueada por tentativas de senha incorreta. Tente novamente em ~{minutos} minuto(s).")
            if st.button("Usar outro e-mail", key="switch_email"):
                st.session_state["auth_email"] = None
                st.rerun()
            return

        password = st.text_input("Senha", type="password", label_visibility="collapsed", placeholder="Digite sua senha")
        if st.button("Entrar", width="stretch"):
            verified = verify_login(user["email"], password)
            if verified:
                st.session_state["auth_user"] = verified
                st.rerun()
            else:
                refreshed = get_user(user["email"])
                if refreshed and is_locked(refreshed):
                    st.error(f"Muitas tentativas erradas — conta bloqueada por ~{lock_remaining_minutes(refreshed)} minuto(s).")
                else:
                    st.error("Senha incorreta.")
        if st.button("Usar outro e-mail", key="switch_email"):
            st.session_state["auth_email"] = None
            st.rerun()

    name = user.get("name") or user["email"]
    _login_shell(f"Olá, {name}", "Digite sua senha para entrar.", form)


def require_login() -> None:
    """Bloqueia o restante do script até o usuário estar autenticado."""
    if st.session_state.get("auth_user") is not None:
        return

    email = st.session_state.get("auth_email")
    if not email:
        _screen_email()
        st.stop()

    try:
        user = get_user(email)
    except Exception:
        _screen_connection_error()
        st.stop()

    if user is None:
        _screen_no_access(email)
    elif needs_password_setup(user):
        _screen_set_password(user)
    else:
        _screen_login(user)
    st.stop()


def render_sidebar_account() -> None:
    """Saudação + botão Sair no topo da barra lateral."""
    user = st.session_state.get("auth_user")
    if not user:
        return
    with st.sidebar:
        st.markdown(f"Olá, **{user.get('name') or user['email']}**")
        if st.button("Sair", key="sidebar_logout_btn", width="stretch"):
            st.session_state["auth_user"] = None
            st.session_state["auth_email"] = None
            st.rerun()
        st.divider()


# ----------------------------------------------------------------------------- acesso por painel
# Matriz de acessos (migrações 013/014): depois do login, confere se o e-mail pode abrir ESTE
# painel em acesso.v_permissoes (grupos + exceções), administrada na tela local
# _neon/acessos/admin_acessos.py. Nega se o banco falhar. Consulta uma vez por sessão.

def exigir_acesso_ao_painel(painel: str) -> None:
    usuario = st.session_state.get("auth_user")
    if not usuario:
        return
    email = usuario["email"]
    chave = f"_acesso_{painel}"
    if st.session_state.get(chave) != email:
        try:
            with _connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1 FROM acesso.v_permissoes WHERE email = %s AND painel = %s LIMIT 1", (email, painel))
                pode = cur.fetchone() is not None
        except Exception:  # noqa: BLE001 — sem conseguir conferir, não libera
            pode = None
        if pode:
            st.session_state[chave] = email
            return

        def form() -> None:
            if pode is None:
                st.error("Não foi possível conferir o seu acesso agora. Tente de novo em alguns segundos.")
                if st.button("Tentar novamente", width="stretch"):
                    st.rerun()
            else:
                st.warning(f"O usuário **{email}** não tem acesso a este painel. Solicite a inclusão para **{SUPPORT_EMAIL}**.")
            if st.button("Sair", key="sair_sem_acesso", width="stretch"):
                st.session_state["auth_user"] = None
                st.session_state["auth_email"] = None
                st.rerun()

        _login_shell("Sem acesso a este painel", "Seu login está ativo, mas este painel não está liberado para você.", form)
        st.stop()
