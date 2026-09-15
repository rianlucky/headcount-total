"""Concede (ou remove) acesso ao painel Headcount Total para um e-mail.

Não cadastra senha nenhuma — a própria pessoa cria a senha dela no primeiro
login (ver auth.py). Roda direto contra o mesmo Neon que já guarda os dados
de headcount, lendo `.streamlit/secrets.toml` — não precisa do Streamlit
rodando.

Uso:
    python scripts/grant_access.py
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import psycopg2

SECRETS_PATH = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"

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

GRANT_SQL = """
INSERT INTO app_users (email, name)
VALUES (%s, %s)
ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name, updated_at = now()
"""

REVOKE_SQL = "DELETE FROM app_users WHERE email = %s"


def _database_url() -> str:
    with SECRETS_PATH.open("rb") as f:
        secrets = tomllib.load(f)
    return secrets["neon"]["database_url"]


def grant(conn, email: str, name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        cur.execute(GRANT_SQL, (email.strip().lower(), name.strip()))
    conn.commit()


def revoke(conn, email: str) -> None:
    with conn.cursor() as cur:
        cur.execute(REVOKE_SQL, (email.strip().lower(),))
    conn.commit()


def main() -> None:
    if not SECRETS_PATH.exists():
        print(f"Não encontrei {SECRETS_PATH}.")
        print("Configure [neon] com a database_url antes de rodar este script (veja .streamlit/secrets.toml.example).")
        sys.exit(1)

    conn = psycopg2.connect(_database_url())
    print("Conceder acesso ao painel — deixe o e-mail em branco e aperte Enter para parar.")
    print("(a pessoa define a própria senha no primeiro login; não se cadastra senha aqui)\n")
    try:
        while True:
            entry = input("E-mail (ou 'remover:email@empresa.com' para tirar o acesso): ").strip()
            if not entry:
                break
            if entry.lower().startswith("remover:"):
                alvo = entry.split(":", 1)[1].strip()
                revoke(conn, alvo)
                print(f"-> acesso de {alvo.lower()} removido.\n")
                continue
            name = input("Nome de exibição: ").strip()
            grant(conn, entry, name)
            print(f"-> acesso concedido para {entry.strip().lower()}.\n")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
