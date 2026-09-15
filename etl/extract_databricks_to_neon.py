"""Extrai o snapshot agregado de headcount do Databricks e grava no Neon.

Job batch, pensado para rodar semanalmente via Windows Task Scheduler (ver README.md
na raiz do projeto). Não roda como parte do processo do Streamlit — é um processo
separado, com suas próprias credenciais (etl/.env, nunca commitado).

Pipeline provisório: Databricks (fonte, `rh.gold.fato_funcionario_ativo`) -> Neon
(camada restrita, lida pelo painel) enquanto o acesso direto do Streamlit ao
Databricks estiver fora de alcance. Ver CONTEXT.md para a decisão completa.

`rh.gold.fato_funcionario_ativo` só guarda o estado ATUAL (uma linha por
funcionário ativo na última carga, sem histórico). Por isso cada execução deste
script grava um snapshot novo (a data de hoje) em vez de substituir a tabela
inteira — o histórico no Neon vai se acumulando a cada rodada semanal.

A leitura do Databricks usa OAuth user-to-machine (auth_type="databricks-oauth"):
abre o navegador para login na primeira vez, e reusa/renova o token depois — não
depende de PAT nem de Service Principal (M2M), que estavam fora de alcance.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

import mapping

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"extract_{datetime.now(timezone.utc):%Y%m%d}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("extract_databricks_to_neon")


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Variável de ambiente obrigatória ausente: {name}")
    return value


def read_from_databricks() -> pd.DataFrame:
    """Lê e já agrega o headcount atual de `rh.gold.fato_funcionario_ativo`.

    A agregação acontece no Databricks (GROUP BY na própria query) — nenhuma linha
    individual (com PII) sai do warehouse, só os totais por combinação de dimensões.
    Diretoria e área vêm do CC_MAPPING (não do nome_diretoria/nome_centro_custo do
    gold, que ficam desatualizados — ver comentário do CC_MAPPING acima).
    """

    hostname = _require_env("DATABRICKS_SERVER_HOSTNAME")
    http_path = _require_env("DATABRICKS_HTTP_PATH")

    # id_funcionario e descricao_posicao só entram pra resolver SPECIAL_MAPPINGS (por
    # cargo/pessoa dentro de um CC específico) — caem fora do dataframe antes de
    # qualquer gravação no Neon, junto com cc/funcao_cargo/estado/descricao_posicao.
    # descricao_local é a chave usada pra cruzar com o relatório oficial de locais
    # (rh.silver.oracle_hcm_pit_est_00006_locais_relatorio) em mapping.LOCAL_MAPPING.
    query = """
        SELECT
            data_referencia AS snapshot_date,
            TRY_CAST(centro_de_custo AS BIGINT) AS cc,
            funcao_cargo,
            descricao_posicao,
            id_funcionario,
            estado,
            descricao_local,
            categoria_atribuicao,
            COUNT(*) AS headcount
        FROM rh.gold.fato_funcionario_ativo
        GROUP BY 1, 2, funcao_cargo, descricao_posicao, id_funcionario, estado, descricao_local, categoria_atribuicao
    """
    with mapping.connect_databricks(hostname, http_path) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            values = cursor.fetchall()
            columns = [column[0] for column in cursor.description]
    data = pd.DataFrame(values, columns=columns)
    data["snapshot_date"] = pd.to_datetime(data["snapshot_date"]).dt.date

    cc_as_key = data["cc"].apply(lambda v: str(int(v)) if pd.notna(v) else None)
    resolved = [
        mapping.resolve_diretoria_area(cc, pos, pid)
        for cc, pos, pid in zip(cc_as_key, data["descricao_posicao"], data["id_funcionario"])
    ]
    data["diretoria"] = [r[0] for r in resolved]
    data["area"] = [r[1] for r in resolved]
    data["job_level"] = data["funcao_cargo"].map(mapping.resolve_job_level)
    data["job_function"] = data["funcao_cargo"].map(mapping.resolve_job_function)
    data["state"] = data["estado"].map(mapping.resolve_state)
    city_resolved = data["descricao_local"].map(mapping.resolve_city)
    data["city"] = [r[0] for r in city_resolved]
    data["city_lat"] = [r[1] for r in city_resolved]
    data["city_lon"] = [r[2] for r in city_resolved]
    data["assignment_category"] = data["categoria_atribuicao"].map(mapping.resolve_assignment_category)
    data = data.drop(
        columns=["cc", "funcao_cargo", "descricao_posicao", "id_funcionario", "estado", "descricao_local", "categoria_atribuicao"]
    )

    # Uma linha por combinação de dimensões já foi garantida pelo GROUP BY acima,
    # mas o remapeamento (cc->diretoria/área, funcao_cargo->nível, uf->estado,
    # local->cidade) pode colidir grupos diferentes — soma o headcount de novo.
    # city_lat/city_lon são 1:1 com city (não fazem parte da chave), então o
    # first() de cada grupo é sempre o mesmo valor.
    data = (
        data.groupby(
            ["snapshot_date", "diretoria", "area", "job_level", "job_function", "state", "city", "assignment_category"],
            as_index=False,
        )
        .agg(headcount=("headcount", "sum"), city_lat=("city_lat", "first"), city_lon=("city_lon", "first"))
    )
    return data


def read_extra_metrics_from_databricks() -> tuple[object, int, int]:
    """Conta centros de custo distintos por prefixo — "Obras Ativas" (CC começa
    com 49) e "Lojas Ativas" (CC começa com 48), pedido do usuário em 2026-09-11.
    Direto no código bruto do centro de custo (não a "área" mapeada), porque o
    prefixo numérico é que carrega esse significado (49xxx = obra, 48xxx = loja/
    ponto de venda) — confirmado batendo com o mapeamento manual (CC_MAPPING)."""

    hostname = _require_env("DATABRICKS_SERVER_HOSTNAME")
    http_path = _require_env("DATABRICKS_HTTP_PATH")

    query = """
        SELECT
            data_referencia AS snapshot_date,
            COUNT(DISTINCT CASE WHEN centro_de_custo LIKE '49%' THEN centro_de_custo END) AS obras_ativas,
            COUNT(DISTINCT CASE WHEN centro_de_custo LIKE '48%' THEN centro_de_custo END) AS lojas_ativas
        FROM rh.gold.fato_funcionario_ativo
        GROUP BY data_referencia
    """
    with mapping.connect_databricks(hostname, http_path) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            row = cursor.fetchone()
    return row.snapshot_date, int(row.obras_ativas), int(row.lojas_ativas)


def write_extra_metrics_to_neon(snapshot_date, obras_ativas: int, lojas_ativas: int) -> None:
    import psycopg2

    database_url = _require_env("NEON_DATABASE_URL")
    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO headcount_extra_metrics (snapshot_date, obras_ativas, lojas_ativas)
                VALUES (%s, %s, %s)
                ON CONFLICT (snapshot_date)
                DO UPDATE SET obras_ativas = EXCLUDED.obras_ativas, lojas_ativas = EXCLUDED.lojas_ativas, loaded_at = now()
                """,
                (snapshot_date, obras_ativas, lojas_ativas),
            )
        connection.commit()


def write_to_neon(data: pd.DataFrame) -> None:
    """Grava o(s) snapshot(s) do dia no Neon — substitui por completo as linhas
    dessa(s) snapshot_date antes de inserir (delete + insert), em vez de só upsert.

    Cada execução já traz a agregação COMPLETA e correta pra aquele dia (GROUP BY
    fresco em read_from_databricks(), não incremental). Um upsert puro (sem delete)
    deixava sobrar linhas órfãs sempre que a lógica de mapeamento mudava entre uma
    execução e outra: uma combinação diretoria/área/cargo/estado que existia antes
    e deixou de existir na nova agregação nunca tinha match no ON CONFLICT, então
    ficava esquecida na tabela — foi exatamente o que aconteceu com a correção de
    CAPEX/Presidência em 2026-09-11 (12 pessoas contadas em dobro até essa limpeza).
    Snapshots de outras datas (histórico de semanas anteriores) não são afetados.
    """
    import psycopg2
    from psycopg2.extras import execute_values

    database_url = _require_env("NEON_DATABASE_URL")

    rows = list(
        data[
            [
                "snapshot_date", "diretoria", "area", "job_level", "job_function", "state", "city",
                "city_lat", "city_lon", "assignment_category", "headcount",
            ]
        ].itertuples(index=False, name=None)
    )
    snapshot_dates = data["snapshot_date"].unique().tolist()

    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM headcount_publico WHERE snapshot_date = ANY(%s::date[])",
                (snapshot_dates,),
            )
            execute_values(
                cursor,
                """
                INSERT INTO headcount_publico
                    (snapshot_date, diretoria, area, job_level, job_function, state, city,
                     city_lat, city_lon, assignment_category, headcount)
                VALUES %s
                ON CONFLICT (snapshot_date, diretoria, area, job_level, job_function, state, city, assignment_category)
                DO UPDATE SET
                    headcount = EXCLUDED.headcount,
                    city_lat = EXCLUDED.city_lat,
                    city_lon = EXCLUDED.city_lon,
                    loaded_at = now()
                """,
                rows,
            )
        connection.commit()


def main() -> int:
    log.info("Iniciando extração Databricks -> Neon")
    try:
        data = read_from_databricks()
    except Exception:
        log.exception("Falha ao ler do Databricks")
        return 1
    log.info("Lidas/agregadas %d linhas do Databricks (snapshot %s)", len(data), data["snapshot_date"].max() if len(data) else "?")

    try:
        write_to_neon(data)
    except Exception:
        log.exception("Falha ao gravar no Neon")
        return 1
    log.info("Gravadas %d linhas no Neon com sucesso", len(data))

    try:
        snapshot_date, obras_ativas, lojas_ativas = read_extra_metrics_from_databricks()
        write_extra_metrics_to_neon(snapshot_date, obras_ativas, lojas_ativas)
        log.info("Métricas extras gravadas: %d obras ativas, %d lojas ativas", obras_ativas, lojas_ativas)
    except Exception:
        log.exception("Falha ao gravar métricas extras (obras/lojas ativas) — não interrompe o job principal")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
