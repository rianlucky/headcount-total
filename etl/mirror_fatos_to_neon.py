"""Espelha as tabelas fato de RH do Databricks pro Neon, schema `core`
(era `interno` até a migração 002 — `interno.*` agora são views de compatibilidade).

Decisão do usuário em 2026-09-11: parar de disputar acesso analítico direto no
Databricks (bloqueado por política de PAT/M2M) e, em vez disso, trazer as 3
tabelas fato pro Neon e tratar governança de acesso dentro do próprio Neon a
partir de agora.

MUITO IMPORTANTE: schema `interno` NUNCA é lido por `app.py` (o painel público
continua lendo só `headcount_publico`, agregado). Essas tabelas só devem ser
consultadas via SQL direto (psql, Neon MCP) ou por outro processo interno —
governança de acesso (views mascaradas, roles com grant restrito etc.) é
tratada fora do escopo deste script.

Mirror completo (truncate + reload) a cada execução.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

import mapping

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger("mirror_fatos_to_neon")

TABLES = [
    "fato_funcionario",
    "fato_funcionario_ativo",
    "fato_funcionario_inativo",
    "fato_funcionario_evol_cargos",
    "fato_movimentacao",
]

_TYPE_MAP = {
    "string": "text",
    "date": "date",
    "double": "double precision",
    "int": "integer",
    "bigint": "bigint",
    "boolean": "boolean",
    "timestamp": "timestamp",
}


def _pg_type(databricks_type: str) -> str:
    return _TYPE_MAP.get(databricks_type, "text")


def describe_table(connection, table: str) -> list[tuple[str, str]]:
    with connection.cursor() as cursor:
        cursor.execute(f"DESCRIBE rh.gold.{table}")
        return [(r.col_name, r.data_type) for r in cursor.fetchall() if not r.col_name.startswith("#")]


def read_table(connection, table: str, columns: list[str]) -> pd.DataFrame:
    """Uma retentativa em caso de reset de conexão (já aconteceu uma vez em
    produção, 2026-09-11, sem causa aparente — provável instabilidade de rede)."""
    col_list = ", ".join(f"`{c}`" for c in columns)
    last_error = None
    for attempt in range(2):
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT {col_list} FROM rh.gold.{table}")
                values = cursor.fetchall()
            return pd.DataFrame(values, columns=columns)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log.warning("Falha lendo %s (tentativa %d/2): %s", table, attempt + 1, exc)
    raise RuntimeError(f"Não consegui ler rh.gold.{table} do Databricks após 2 tentativas") from last_error


def ensure_table(pg_connection, table: str, columns: list[tuple[str, str]]) -> None:
    col_defs = ", ".join(f'"{name}" {_pg_type(dtype)}' for name, dtype in columns)
    with pg_connection.cursor() as cursor:
        # Schema `core` é criado/gerenciado pelas migrações (_neon/migrations), não pelo ETL.
        cursor.execute(f'CREATE TABLE IF NOT EXISTS core."{table}" ({col_defs}, loaded_at timestamptz DEFAULT now())')
    pg_connection.commit()


def replace_table(pg_connection, table: str, data: pd.DataFrame) -> None:
    """Recusa apagar o mirror existente se a leitura veio vazia — mais provável
    ser falha silenciosa do que a tabela real ter zerado."""
    if data.empty:
        raise RuntimeError(f"Leitura de {table} veio vazia — não vou truncar o mirror existente por segurança")

    from psycopg2.extras import execute_values

    columns = list(data.columns)
    col_list = ", ".join(f'"{c}"' for c in columns)
    rows = [tuple(None if pd.isna(v) else v for v in row) for row in data.itertuples(index=False, name=None)]
    with pg_connection.cursor() as cursor:
        cursor.execute(f'TRUNCATE TABLE core."{table}"')
        if rows:
            execute_values(
                cursor, f'INSERT INTO core."{table}" ({col_list}) VALUES %s', rows, page_size=500
            )
    pg_connection.commit()


def rebuild_ativo_por_data(pg_connection) -> int:
    """core.fato_funcionario_ativo = quem está no quadro na data da base, pela regra única de
    headcount da Central (decisão de 28/09/2026): admitido até a data e sem desligamento ou com
    desligamento DEPOIS dela; uma linha por pessoa (a atribuição mais recente).
    A rh.gold.fato_funcionario_ativo do Databricks já tira quem tem desligamento lançado com
    data futura — contava 2 pessoas a menos que os painéis por data (1.518 x 1.520 em 27/09).
    Mesmas colunas da core.fato_funcionario, então sai da própria tabela espelhada."""
    with pg_connection.cursor() as cursor:
        cursor.execute("""SELECT column_name FROM information_schema.columns
                          WHERE table_schema = 'core' AND table_name = 'fato_funcionario_ativo'
                          ORDER BY ordinal_position""")
        cols = ", ".join(f'"{c}"' for (c,) in cursor.fetchall())
        cursor.execute('TRUNCATE TABLE core."fato_funcionario_ativo"')
        cursor.execute(f"""
            INSERT INTO core."fato_funcionario_ativo" ({cols})
            SELECT {cols} FROM (
                SELECT DISTINCT ON (f.id_funcionario) f.*
                FROM core."fato_funcionario" f
                CROSS JOIN (SELECT max(data_referencia) AS d FROM core."fato_funcionario") r
                WHERE f.data_admissao <= r.d AND (f.data_desligamento IS NULL OR f.data_desligamento > r.d)
                ORDER BY f.id_funcionario, f.data_admissao DESC, f.codigo_atribuicao DESC
            ) a""")
        n = cursor.rowcount
    pg_connection.commit()
    return n


def main() -> int:
    import psycopg2

    hostname = os.environ["DATABRICKS_SERVER_HOSTNAME"]
    http_path = os.environ["DATABRICKS_HTTP_PATH"]
    database_url = os.environ["NEON_DATABASE_URL"]

    with mapping.connect_databricks(hostname, http_path) as db_connection:
        with psycopg2.connect(database_url) as pg_connection:
            for table in TABLES:
                if table == "fato_funcionario_ativo":
                    continue  # montada no Neon pela regra de data, logo abaixo
                log.info("Espelhando rh.gold.%s -> core.%s", table, table)
                columns_meta = describe_table(db_connection, table)
                columns = [c for c, _ in columns_meta]
                ensure_table(pg_connection, table, columns_meta)
                data = read_table(db_connection, table, columns)
                replace_table(pg_connection, table, data)
                log.info("  %d linhas, %d colunas gravadas", len(data), len(columns))
            n = rebuild_ativo_por_data(pg_connection)
            log.info("core.fato_funcionario_ativo montada pela regra de data: %d pessoas", n)
    log.info("Mirror de fatos (schema core) gravado no Neon com sucesso")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
