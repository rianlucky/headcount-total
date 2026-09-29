"""Login por e-mail — o mesmo dos outros painéis da Central (tabela acesso.app_users no Neon).

O acesso é liberado cadastrando o e-mail em acesso.app_users (ferramenta local de acessos,
_neon/acessos/admin_acessos.py); no primeiro
login a pessoa cria a senha; depois de MAX_TENTATIVAS senhas erradas seguidas a conta fica
bloqueada por BLOQUEIO_MINUTOS. Quem já tem login em outro painel entra com a mesma senha.

O usuário de banco do painel lê e atualiza acesso.app_users pelo grupo de login (migração 012).
E-mail de suporte: [app] email_suporte nos Secrets (fora do código, que é público).
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path
from typing import Callable

import bcrypt
import pandas as pd
import psycopg2
import psycopg2.extras
import streamlit as st

TABELA = "acesso.app_users"  # nome completo: o search_path do usuário do painel é só `core`
MAX_TENTATIVAS = 5
BLOQUEIO_MINUTOS = 15
ICONE = Path(__file__).parent / "assets" / "icone-headcount-transparente.png"
TITULO = "Headcount Total"
SUBTITULO = "Headcount, alocação de mão de obra e evolução por data ou período da Pacaembu Construtora"

# Tela de login padrão da Central (29/09/2026): um cartão só, centralizado, que funciona igual em
# computador, tablet e celular. Topo com formas nas cores da marca + ícone do painel; campos com
# rótulo (fonte 16px: o iPhone não dá zoom), Enter envia (st.form), botões de 46px para toque,
# ação principal em azul e "Voltar"/"Sair" em cinza.
_CSS = """<style>
[data-testid="stMainBlockContainer"] { padding-top: 2rem; }
.st-key-login_page { display: flex; justify-content: center; margin-top: 5vh; }
.st-key-login_card {
    width: min(440px, 100%) !important; margin: 0 auto; background: #FFFFFF; border-radius: 22px;
    box-shadow: 0 18px 50px rgba(6, 77, 102, .16); overflow: hidden; padding: 0 0 24px !important; gap: 0 !important;
}
.login-hero { position: relative; height: 176px; overflow: hidden; background: #FFFFFF; }
.login-hero .forma-amarela { position: absolute; left: -70px; top: -120px; width: 330px; height: 290px; border-radius: 50%;
    background: linear-gradient(160deg, #FFA724 0%, #FAB900 100%); }
.login-hero .forma-azul { position: absolute; right: -90px; top: -150px; width: 330px; height: 330px; border-radius: 50%;
    background: linear-gradient(200deg, #003244 0%, #064D66 70%); }
.login-hero .forma-vermelha { position: absolute; right: 40px; top: 118px; width: 14px; height: 14px; border-radius: 50%; background: #F02727; }
.login-icone { position: absolute; left: 50%; bottom: 6px; transform: translateX(-50%); width: 84px; height: 84px; border-radius: 22px;
    background: #FFFFFF; box-shadow: 0 10px 26px rgba(0, 50, 68, .22); display: flex; align-items: center; justify-content: center; }
.login-icone img { width: 58px; height: 58px; object-fit: contain; display: block; }
.login-marca { text-align: center; padding: 14px 30px 4px; }
.login-marca .nome { font-size: 1.35rem; font-weight: 700; color: #064D66; line-height: 1.25; }
.login-marca .sub { font-size: .83rem; color: #6B7280; line-height: 1.45; margin-top: 4px; }
.login-passo { padding: 18px 30px 4px; }
.login-passo .titulo { font-size: 1.02rem; font-weight: 650; color: #1F2937; }
.login-passo .sub { font-size: .84rem; color: #6B7280; margin-top: 2px; line-height: 1.45; overflow-wrap: anywhere; }
.st-key-login_form { padding: 6px 30px 0; }
.st-key-login_form [data-testid="stForm"] { border: none; padding: 0; }
.st-key-login_form label p { font-size: .82rem !important; font-weight: 600; color: #374151; }
.st-key-login_form input { font-size: 16px !important; min-height: 44px; }
.st-key-login_form [data-baseweb="input"] { border-radius: 10px; }
.st-key-login_form button { min-height: 46px; border-radius: 10px !important; font-weight: 600 !important; }
.st-key-login_form button[kind^="primary"] { background: #064D66 !important; border: none !important; }
.st-key-login_form button[kind^="primary"]:hover { background: #003244 !important; }
.st-key-login_form button[kind^="primary"] p { color: #FFFFFF !important; font-weight: 600; }
.st-key-login_form button[kind^="secondary"] { background: #F3F4F6 !important; border: 1px solid #E5E7EB !important; }
.st-key-login_form button[kind^="secondary"] p { color: #4B5563 !important; font-weight: 600; }
.st-key-login_form button[kind^="secondary"]:hover { background: #E5E7EB !important; }
.login-rodape { text-align: center; font-size: .72rem; color: #9CA3AF; padding: 16px 30px 0; letter-spacing: .02em; }
@media (max-width: 640px) {
    .st-key-login_page { margin-top: 0; }
    [data-testid="stMainBlockContainer"] { padding: 3.6rem .75rem 1rem; }
    .st-key-login_card { border-radius: 18px; }
    .login-hero { height: 158px; }
    .login-marca, .login-passo, .st-key-login_form, .login-rodape { padding-left: 20px; padding-right: 20px; }
}
</style>"""


# ----------------------------------------------------------------------------- banco

def _conectar():
    return psycopg2.connect(st.secrets["neon"]["database_url"], connect_timeout=10)


def _email_suporte() -> str:
    try:
        return st.secrets["app"]["email_suporte"]
    except Exception:  # noqa: BLE001
        return "o time de People Analytics"


def normalizar(email: str) -> str:
    return email.strip().lower()


def buscar_usuario(email: str) -> dict | None:
    with _conectar() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT email, name, password_hash, failed_attempts, locked_until FROM {TABELA} WHERE email = %s",
                    (normalizar(email),))
        linha = cur.fetchone()
    return dict(linha) if linha else None


def bloqueado(usuario: dict) -> bool:
    ate = usuario.get("locked_until")
    return ate is not None and ate > pd.Timestamp.now(tz="UTC")


def minutos_restantes(usuario: dict) -> int:
    seg = (usuario["locked_until"] - pd.Timestamp.now(tz="UTC")).total_seconds()
    return max(1, int(-(-seg // 60)))


def _falhou(email: str) -> None:
    with _conectar() as conn, conn.cursor() as cur:
        cur.execute(f"""
            UPDATE {TABELA}
            SET failed_attempts = failed_attempts + 1,
                locked_until = CASE WHEN failed_attempts + 1 >= %(max)s
                                    THEN now() + (%(min)s * interval '1 minute') ELSE locked_until END,
                updated_at = now()
            WHERE email = %(email)s""", {"email": normalizar(email), "max": MAX_TENTATIVAS, "min": BLOQUEIO_MINUTOS})


def _zerar_tentativas(email: str) -> None:
    with _conectar() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {TABELA} SET failed_attempts = 0, locked_until = NULL, updated_at = now() WHERE email = %s",
                    (normalizar(email),))


def verificar(email: str, senha: str) -> dict | None:
    usuario = buscar_usuario(email)
    if not usuario or not usuario.get("password_hash") or bloqueado(usuario):
        return None
    if bcrypt.checkpw(senha.encode(), usuario["password_hash"].encode()):
        _zerar_tentativas(email)
        return usuario
    _falhou(email)
    return None


def criar_senha(email: str, senha: str) -> dict | None:
    with _conectar() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {TABELA} SET password_hash = %s, updated_at = now() WHERE email = %s",
                    (bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode(), normalizar(email)))
    return buscar_usuario(email)


# ----------------------------------------------------------------------------- telas

@lru_cache(maxsize=1)
def _icone_b64() -> str:
    return "data:image/png;base64," + base64.b64encode(ICONE.read_bytes()).decode()


def _cartao(titulo: str, subtitulo: str, formulario: Callable[[], None]) -> None:
    st.html(_CSS)
    with st.container(key="login_page"), st.container(key="login_card"):
        st.html(f"""<div class="login-hero"><div class="forma-amarela"></div><div class="forma-azul"></div>
            <div class="forma-vermelha"></div><div class="login-icone"><img src="{_icone_b64()}" alt="" /></div></div>
            <div class="login-marca"><div class="nome">{TITULO}</div><div class="sub">{SUBTITULO}</div></div>
            <div class="login-passo"><div class="titulo">{titulo}</div><div class="sub">{subtitulo}</div></div>""")
        with st.container(key="login_form"):
            formulario()
        st.html('<div class="login-rodape">Pacaembu Construtora · Central de Gente &amp; Dados</div>')


def _voltar(rotulo: str = "Voltar", key: str = "login_voltar") -> None:
    """Botão cinza que volta para a tela do e-mail."""
    if st.button(rotulo, key=key, width="stretch"):
        st.session_state["auth_user"] = None
        st.session_state["auth_email"] = None
        st.rerun()


def _tela_email() -> None:
    def form() -> None:
        with st.form("login_email", border=False):
            email = st.text_input("E-mail corporativo", placeholder="seu.email@pacaembu.com", autocomplete="email")
            enviar = st.form_submit_button("Continuar", type="primary", width="stretch")
        if enviar:
            if "@" not in normalizar(email):
                st.error("Informe um e-mail válido.")
            else:
                st.session_state["auth_email"] = normalizar(email)
                st.rerun()
    _cartao("Entrar", "Use o seu e-mail da Pacaembu Construtora.", form)


def _tela_erro_conexao() -> None:
    def form() -> None:
        st.error("Não foi possível conectar ao banco de dados agora. Isso costuma ser passageiro — tente de novo em alguns segundos.")
        if st.button("Tentar novamente", type="primary", width="stretch"):
            st.rerun()
        _voltar()
    _cartao("Erro temporário de conexão", "Não conseguimos falar com o banco de dados agora.", form)


def _tela_sem_acesso(email: str) -> None:
    def form() -> None:
        st.warning(f"O e-mail **{email}** ainda não tem acesso. Solicite a inclusão para **{_email_suporte()}**.")
        _voltar()
    _cartao("Acesso não encontrado", "Esse e-mail ainda não está liberado.", form)


def _tela_criar_senha(usuario: dict) -> None:
    def form() -> None:
        with st.form("login_criar_senha", border=False):
            senha = st.text_input("Nova senha", type="password", placeholder="Mínimo de 8 caracteres", autocomplete="new-password")
            conf = st.text_input("Confirmar senha", type="password", placeholder="Digite a senha de novo", autocomplete="new-password")
            enviar = st.form_submit_button("Criar senha e entrar", type="primary", width="stretch")
        if enviar:
            if len(senha) < 8:
                st.error("A senha precisa ter pelo menos 8 caracteres.")
            elif senha != conf:
                st.error("As senhas não coincidem.")
            else:
                st.session_state["auth_user"] = criar_senha(usuario["email"], senha)
                st.rerun()
        _voltar()
    _cartao(f"Olá, {usuario.get('name') or usuario['email']}", "Primeiro acesso: crie a sua senha. Ela vale para todos os painéis.", form)


def _tela_senha(usuario: dict) -> None:
    def form() -> None:
        if bloqueado(usuario):
            st.warning(f"Conta temporariamente bloqueada por tentativas de senha incorreta. Tente de novo em ~{minutos_restantes(usuario)} minuto(s).")
        else:
            with st.form("login_senha", border=False):
                senha = st.text_input("Senha", type="password", placeholder="Digite sua senha", autocomplete="current-password")
                enviar = st.form_submit_button("Entrar", type="primary", width="stretch")
            if enviar:
                ok = verificar(usuario["email"], senha)
                if ok:
                    st.session_state["auth_user"] = ok
                    st.rerun()
                novo = buscar_usuario(usuario["email"])
                st.error(f"Muitas tentativas erradas — conta bloqueada por ~{minutos_restantes(novo)} minuto(s)."
                         if novo and bloqueado(novo) else "Senha incorreta.")
        _voltar()
    _cartao(f"Olá, {usuario.get('name') or usuario['email']}", f"Digite a senha de {usuario['email']}.", form)


def exigir_login() -> None:
    """Para o script até a pessoa estar autenticada. Depois disso, a barra lateral do
    painel_padrao mostra "Olá, {nome}" e o botão Sair (lendo st.session_state["auth_user"])."""
    if st.session_state.get("auth_user") is not None:
        return
    email = st.session_state.get("auth_email")
    if not email:
        _tela_email()
        st.stop()
    try:
        usuario = buscar_usuario(email)
    except Exception:  # noqa: BLE001
        _tela_erro_conexao()
        st.stop()
    if usuario is None:
        _tela_sem_acesso(email)
    elif not usuario.get("password_hash"):
        _tela_criar_senha(usuario)
    else:
        _tela_senha(usuario)
    st.stop()


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
            with _conectar() as conn, conn.cursor() as cur:
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
                if st.button("Tentar novamente", type="primary", width="stretch"):
                    st.rerun()
            else:
                st.warning(f"O usuário **{email}** não tem acesso a este painel. Solicite a inclusão para **{_email_suporte()}**.")
            _voltar("Sair", key="sair_sem_acesso")

        _cartao("Sem acesso a este painel", "Seu login está ativo, mas este painel não está liberado para você.", form)
        st.stop()
