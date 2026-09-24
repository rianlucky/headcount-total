"""Reconstrói o histórico de headcount — COM quebra real por diretoria/área/
estado/cargo — direto do mirror interno no Neon, sem precisar consultar o
Databricks de novo.

Usa duas tabelas já espelhadas por `mirror_fatos_to_neon.py`:
- `core.fato_funcionario`: data_admissao/data_desligamento por pessoa — quem
  estava ativo em cada mês histórico.
- `core.fato_funcionario_evol_cargos`: dimensão tipo-2 (intervalo de validade
  `data_de`/`data_ate`) de centro de custo/cargo por pessoa, cobrindo desde 2006
  — dá pra saber o CC/cargo de qualquer pessoa em qualquer data histórica, não só
  nos poucos meses com snapshot diário do bronze (ver `backfill_headcount_history.py`,
  que esta versão substitui como fonte de verdade do histórico).

Saída pro painel público é a mesma de sempre (`headcount_publico`: snapshot_date,
diretoria, area, job_level, job_function, state, city, city_lat, city_lon,
headcount) — só muda COMO é calculada (mais precisa agora), não o que é
publicado. Decisão do usuário em 2026-09-11: "para o dashboard vamos apenas
colocar os dados que já utilizamos".

Limitação aceita: `estado`/`city` usam o valor mais recente conhecido da pessoa
(não há histórico de mudança de estado/posto de trabalho rastreado em nenhuma
das tabelas espelhadas). `job_level`/`job_function` são aproximados a partir da
primeira palavra de `descricao_cargo` (ex.: "Diretor de Negócios" -> "Diretor"),
já que `fato_funcionario_evol_cargos` não guarda `funcao_cargo` diretamente —
mesmo vocabulário de `mapping.JOB_LEVEL_MAP`.
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
log = logging.getLogger("backfill_from_neon_history")

START_MONTH_END = "2024-09-30"
END_MONTH_END = "2026-08-31"


def _connect():
    import psycopg2

    return psycopg2.connect(os.environ["NEON_DATABASE_URL"])


def load_people(connection) -> pd.DataFrame:
    query = """
        SELECT id_funcionario, data_admissao, data_desligamento, estado, descricao_local, categoria_atribuicao
        FROM core.fato_funcionario
    """
    people = pd.read_sql(query, connection)
    people["id_funcionario"] = people["id_funcionario"].astype(str)
    people["data_admissao"] = pd.to_datetime(people["data_admissao"])
    people["data_desligamento"] = pd.to_datetime(people["data_desligamento"])
    return people


def load_cargo_history(connection) -> pd.DataFrame:
    query = """
        SELECT id_funcionario, data_de, data_ate, descricao_cargo, centro_de_custo_atual
        FROM core.fato_funcionario_evol_cargos
    """
    history = pd.read_sql(query, connection)
    history["id_funcionario"] = history["id_funcionario"].astype(str)
    history["data_de"] = pd.to_datetime(history["data_de"])
    history["data_ate"] = pd.to_datetime(history["data_ate"])
    return history


def build_snapshots(people: pd.DataFrame, cargo_history: pd.DataFrame, month_ends: list[str]) -> pd.DataFrame:
    frames = []
    for month_end in month_ends:
        ref = pd.Timestamp(month_end)
        active = people[
            (people["data_admissao"] <= ref)
            & (people["data_desligamento"].isna() | (people["data_desligamento"] > ref))
        ].copy()
        active["snapshot_date"] = ref.date()
        frames.append(active)
    snapshots = pd.concat(frames, ignore_index=True)

    merged = snapshots.merge(cargo_history, on="id_funcionario", how="left")
    ref_ts = pd.to_datetime(merged["snapshot_date"])
    in_interval = (merged["data_de"] <= ref_ts) & (merged["data_ate"].isna() | (merged["data_ate"] >= ref_ts))
    merged = merged[merged["data_de"].isna() | in_interval]
    merged = merged.sort_values("data_de").drop_duplicates(subset=["id_funcionario", "snapshot_date"], keep="last")

    # Gente ativa sem nenhum intervalo de cargo/CC batendo (data anterior ao
    # primeiro registro de evolução da pessoa) ainda conta no total — só cai em
    # "Não informado" na quebra por dimensão, igual a um CC fora do mapeamento.
    full = snapshots.merge(
        merged[["id_funcionario", "snapshot_date", "descricao_cargo", "centro_de_custo_atual"]],
        on=["id_funcionario", "snapshot_date"],
        how="left",
    )
    return full


def resolve_dimensions(data: pd.DataFrame) -> pd.DataFrame:
    resolved = [
        mapping.resolve_diretoria_area(cc, pos, pid)
        for cc, pos, pid in zip(data["centro_de_custo_atual"], data["descricao_cargo"], data["id_funcionario"])
    ]
    data["diretoria"] = [r[0] for r in resolved]
    data["area"] = [r[1] for r in resolved]
    primeira_palavra = data["descricao_cargo"].fillna("").str.split().str[0]
    data["job_level"] = primeira_palavra.map(mapping.resolve_job_level)
    # Mesma aproximação de primeira-palavra usada pro job_level acima — o mirror
    # interno não guarda funcao_cargo bruto, só descricao_cargo (ver docstring do
    # módulo). Sem cargo no intervalo (NaN, não string vazia — .str[0] de uma
    # lista vazia) cai em NAO_INFORMADO via resolve_job_function.
    data["job_function"] = primeira_palavra.where(primeira_palavra.notna(), None).map(mapping.resolve_job_function)
    data["state"] = data["estado"].map(mapping.resolve_state)
    # Mesma limitação aceita do estado acima: usa o local mais recente conhecido
    # da pessoa (não há histórico de mudança de posto de trabalho rastreado).
    city_resolved = data["descricao_local"].map(mapping.resolve_city)
    data["city"] = [r[0] for r in city_resolved]
    data["city_lat"] = [r[1] for r in city_resolved]
    data["city_lon"] = [r[2] for r in city_resolved]
    # categoria_atribuicao não muda com frequência — mesma limitação aceita de
    # usar o valor mais recente conhecido da pessoa.
    data["assignment_category"] = data["categoria_atribuicao"].map(mapping.resolve_assignment_category)
    return data


def write_to_neon(connection, data: pd.DataFrame, month_ends: list[str]) -> None:
    from psycopg2.extras import execute_values

    rows = list(
        data[
            [
                "snapshot_date", "diretoria", "area", "job_level", "job_function", "state", "city",
                "city_lat", "city_lon", "assignment_category", "headcount",
            ]
        ].itertuples(index=False, name=None)
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM org.headcount_publico WHERE snapshot_date = ANY(%s::date[])",
            (month_ends,),
        )
        execute_values(
            cursor,
            """
            INSERT INTO org.headcount_publico
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
    month_ends = pd.date_range(START_MONTH_END, END_MONTH_END, freq="ME").strftime("%Y-%m-%d").tolist()

    with _connect() as connection:
        log.info("Lendo pessoas e histórico de cargo/CC do mirror interno (Neon)")
        people = load_people(connection)
        cargo_history = load_cargo_history(connection)

        log.info("Reconstruindo %d meses (%s a %s)", len(month_ends), month_ends[0], month_ends[-1])
        snapshots = build_snapshots(people, cargo_history, month_ends)
        snapshots = resolve_dimensions(snapshots)

        summary = (
            snapshots.groupby(
                [
                    "snapshot_date", "diretoria", "area", "job_level", "job_function", "state", "city",
                    "assignment_category",
                ],
                as_index=False,
            )
            .agg(headcount=("id_funcionario", "size"), city_lat=("city_lat", "first"), city_lon=("city_lon", "first"))
        )

        write_to_neon(connection, summary, month_ends)
        log.info("Gravadas %d linhas (quebra real por diretoria/área/estado/cargo, %d meses)", len(summary), len(month_ends))

        total_por_mes = summary.groupby("snapshot_date")["headcount"].sum()
        for data, total in total_por_mes.items():
            log.info("  %s: %d colaboradores", data, total)

    log.info("Backfill (via mirror interno) gravado no Neon com sucesso")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
