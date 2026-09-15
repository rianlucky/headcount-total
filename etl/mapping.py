"""Resolução compartilhada de diretoria/área/cargo/estado a partir do dado bruto do RH.

Usado tanto pelo job semanal (`extract_databricks_to_neon.py`, snapshot atual via
`rh.gold.fato_funcionario_ativo`) quanto pelo backfill histórico
(`backfill_headcount_history.py`, snapshots passados via `rh.bronze.oracle_hcm_pit_*`)
— a mesma correção de CC_MAPPING/SPECIAL_MAPPINGS precisa valer nos dois, senão o
histórico e o presente divergem na mesma diretoria/área.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

NAO_INFORMADO = "Não informado"

# CC -> {diretoria, area}, mapeamento manual fornecido pelo usuário em 2026-09-11
# (mesmo racional do "Mapeamento CC Manual" do modelo de dados do Genie): o join
# automático centro_de_custo -> nome_diretoria do gold fica desatualizado depois
# de reorganizações. Cobre 100% dos CCs em uso hoje — se um CC novo aparecer sem
# entrada aqui, cai em "Não informado" em vez de quebrar.
CC_MAPPING = json.loads((ROOT / "cc_mapping.json").read_text(encoding="utf-8"))

# Correção pontual por CC: alguns centros de custo (ex.: 47700 "Presidência Executiva",
# 47276 "CAPEX - Sistemas de Tecnologia") concentram gente de várias diretorias reais
# sob um único CC administrativo — o mapeamento por CC sozinho não dá conta. Definido
# com o usuário em 2026-09-11 a partir de funcao_cargo/descricao_posicao/id_funcionario.
# "by_position" resolve pelo título do cargo (só dentro do CC listado); "by_person"
# resolve por id_funcionario quando dois titulares têm o mesmo cargo mas vão para
# diretorias diferentes (ex.: dois "Diretor de Negócios", um pra cada unidade). Nunca
# sai nome de ninguém daqui pro Neon/painel — só id_funcionario é usado como chave.
SPECIAL_MAPPINGS = json.loads((ROOT / "special_mappings.json").read_text(encoding="utf-8"))

# descricao_local (ex.: "OBRA_SA_PACAEMBU_SAO_CARLOS_49277") -> {cidade, lat, lon}.
# Cruzado com o relatório oficial de locais do Oracle HCM
# (rh.silver.oracle_hcm_pit_est_00006_locais_relatorio, campo `cidade`) pela
# própria `descricao_local` — pedido do usuário em 2026-09-14, depois que a
# primeira tentativa (extrair a cidade só do texto do código, sem esse
# relatório) errou casos como "PACAEMBU_SAO_CARLOS" -> "Pacaembu" em vez de
# "São Carlos". O relatório não tem `estado` preenchido pra ~65% dos locais;
# nesses casos usa o estado real de quem trabalha lá (fato_funcionario_ativo)
# pra achar o município certo no IBGE, e só sem estado nenhum aceita o nome se
# for único no Brasil inteiro (ex.: "Bauru"). 143/143 casaram em 2026-09-14.
LOCAL_MAPPING = json.loads((ROOT / "local_mapping.json").read_text(encoding="utf-8"))

# Mapeamento de função de cargo (funcao_cargo) para os 5 níveis públicos oficiais.
# Confirmado com o time de People Analytics em 2026-09-11.
JOB_LEVEL_MAP = {
    "Diretor": "Direção",
    "Presidente": "Direção",
    "Conselheiro": "Direção",
    "Gerente": "Gerência",
    "Executivo": "Gerência",
    "Coordenador": "Coordenação",
    "Especialista": "Especialista",
    "Engenheiro": "Especialista",
    "Advogado": "Especialista",
}
JOB_LEVEL_DEFAULT = "Operacional"  # todo o resto (Analista, Auxiliar, Supervisor, Técnico, Estagiário, ...)

UF_TO_STATE = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
    "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco", "PI": "Piauí",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul",
    "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
    "SE": "Sergipe", "TO": "Tocantins",
}


def resolve_diretoria_area(cc: str | None, descricao_posicao: str | None, id_funcionario) -> tuple[str, str]:
    """Resolve diretoria/área de uma pessoa: SPECIAL_MAPPINGS (por pessoa, depois por
    cargo) tem prioridade sobre o default do CC_MAPPING."""
    special = SPECIAL_MAPPINGS.get(cc) if cc is not None else None
    if special:
        by_person = special.get("by_person", {}).get(str(id_funcionario))
        if by_person:
            return by_person["diretoria"], by_person["area"]
        position = (descricao_posicao or "").rsplit(" - ", 1)[0].strip()
        by_position = special.get("by_position", {}).get(position)
        if by_position:
            return by_position["diretoria"], by_position["area"]
    default = CC_MAPPING.get(cc) if cc is not None else None
    if isinstance(default, dict):
        return default["diretoria"], default["area"]
    return NAO_INFORMADO, NAO_INFORMADO


def resolve_job_level(funcao_cargo: str | None) -> str:
    return JOB_LEVEL_MAP.get(funcao_cargo, JOB_LEVEL_DEFAULT)


def resolve_job_function(funcao_cargo: str | None) -> str:
    """Função de cargo bruta (ex.: "Diretor", "Analista", "Estagiário"), sem
    agrupar nos 5 níveis públicos — pedido do usuário em 2026-09-14 pra exibir
    "Nível de cargo" com essa granularidade em vez do JOB_LEVEL_MAP."""
    return funcao_cargo if funcao_cargo else NAO_INFORMADO


def resolve_state(uf: str | None) -> str:
    return UF_TO_STATE.get(uf, NAO_INFORMADO)


def resolve_assignment_category(categoria_atribuicao: str | None) -> str:
    """Categoria de atribuição bruta (Obras/Corporativo/Comercial) — já vem
    limpa do gold, sem CC/vocabulário pra mapear. Pedido do usuário em
    2026-09-14 como filtro (+ gráfico de pizza) no painel público."""
    return categoria_atribuicao if categoria_atribuicao else NAO_INFORMADO


def resolve_city(descricao_local: str | None) -> tuple[str, float | None, float | None]:
    """Cidade (+coordenadas) do posto de trabalho a partir de `descricao_local`
    (mesma chave usada pra cruzar com o relatório oficial de locais). Sem
    entrada em LOCAL_MAPPING (descricao_local novo, ainda não visto no
    relatório) cai em NAO_INFORMADO sem coordenada."""
    entry = LOCAL_MAPPING.get(descricao_local) if descricao_local is not None else None
    if entry:
        return entry["cidade"], entry["lat"], entry["lon"]
    return NAO_INFORMADO, None, None


def connect_databricks(hostname: str, http_path: str):
    """Conexão OAuth user-to-machine com o refresh token persistido localmente
    (`etl/.oauth_token_cache.json`, nunca commitado — mesmo tratamento do `.env`).
    Sem isso, cada execução do script abria uma aba nova pra login; com o token
    persistido, só pede login de novo quando o refresh token expira de fato."""
    from databricks import sql
    from databricks.sql.experimental.oauth_persistence import DevOnlyFilePersistence

    token_cache_path = ROOT / ".oauth_token_cache.json"
    return sql.connect(
        server_hostname=hostname,
        http_path=http_path,
        auth_type="databricks-oauth",
        experimental_oauth_persistence=DevOnlyFilePersistence(str(token_cache_path)),
    )
