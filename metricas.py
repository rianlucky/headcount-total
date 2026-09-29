"""Cálculo dos indicadores do painel Headcount Total — sem Streamlit, para testar e validar.

Entrada: core.v_headcount_pessoas (Neon), uma linha por atribuição (migração 018).
Regras (as mesmas de todos os painéis da Central):
  1. Quadro numa data: admitido até a data e sem desligamento, ou com desligamento depois dela;
     uma linha por pessoa (a atribuição mais recente).
  2. Diretoria e área: mapeamento oficial (core.v_funcionario_diretoria).
  3. Admissões e desligamentos do período: as do painel Turnover — desligamento lançado por
     engano não conta; recontratação no dia seguinte a um desligamento não conta como admissão.
  4. Obras: centros de custo que começam com 49 (empregador SA) e 52 (empregador LTDA) com os
     mesmos 3 últimos dígitos são a MESMA obra — contadas juntas, indicando os dois CCs.
     Lojas: centros de custo que começam com 48.
Nos meses passados, cada pessoa aparece com a diretoria/área/CC da atribuição (como nos painéis
Turnover e Dados Demográficos) — a evolução bate com a desses painéis.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

NAO_INFORMADO = "Não informado"

# função de cargo -> 5 níveis (mesmo mapa do etl/mapping.py, confirmado com People Analytics em 11/09/2026)
NIVEL_POR_FUNCAO = {"Diretor": "Direção", "Presidente": "Direção", "Conselheiro": "Direção", "Gerente": "Gerência",
                    "Executivo": "Gerência", "Coordenador": "Coordenação", "Especialista": "Especialista",
                    "Engenheiro": "Especialista", "Advogado": "Especialista"}
ORDEM_NIVEL = ["Direção", "Gerência", "Coordenação", "Especialista", "Operacional"]
# nível de gerenciamento do cadastro (gerenciamento_nivel_cargo), com as variações juntas (29/09/2026)
NIVEL_GERENCIAMENTO = {"Gerente de Vendas": "Gerente", "Gerente Executivo de Obras": "Gerente Executivo",
                       "Gerente Executivo Estadual de Obras": "Gerente Executivo", "Coordenador de Obras": "Coordenador/Especialista",
                       "Diretor de Obras": "Diretor"}
UF_ESTADO = {"AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia", "CE": "Ceará",
             "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso",
             "MS": "Mato Grosso do Sul", "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
             "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
             "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
             "SE": "Sergipe", "TO": "Tocantins"}


def _cc_texto(cc, nome) -> str:
    cc = "" if cc is None or pd.isna(cc) else str(cc)
    nome = "" if nome is None or pd.isna(nome) else str(nome).strip()
    return f"{cc} · {nome}" if cc and nome else (cc or nome or NAO_INFORMADO)


def obra_chave(cc) -> str | None:
    """49xxx (SA) e 52xxx (LTDA) com os mesmos 3 últimos dígitos = a mesma obra."""
    cc = "" if cc is None or pd.isna(cc) else str(cc)
    return f"obra-{cc[-3:]}" if len(cc) == 5 and cc[:2] in ("49", "52") else None


def preparar(base: pd.DataFrame) -> pd.DataFrame:
    df = base.copy()
    for c in ("data_referencia", "data_admissao", "data_desligamento"):
        df[c] = pd.to_datetime(df[c]).dt.date
    for c in ("latitude", "longitude"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("diretoria", "area", "cidade", "categoria_atribuicao", "cargo"):
        df[c] = df[c].fillna(NAO_INFORMADO)
    df["funcao"] = df["funcao_cargo"].fillna(NAO_INFORMADO)
    df["nivel"] = df["gerenciamento_nivel_cargo"].replace(NIVEL_GERENCIAMENTO).fillna(NAO_INFORMADO)
    df["estado_nome"] = df["estado"].map(UF_ESTADO).fillna(NAO_INFORMADO)
    df["cc_texto"] = [_cc_texto(c, n) for c, n in zip(df["centro_de_custo"], df["nome_centro_custo"])]
    df["obra"] = df["centro_de_custo"].map(obra_chave)
    df["empregador"] = df["centro_de_custo"].astype(str).str[:2].map({"49": "SA", "52": "LTDA"})
    df["loja"] = df["centro_de_custo"].astype(str).str.startswith("48")
    df["desligamento_incorreto"] = df["desligamento_incorreto"].fillna(False).astype(bool)
    df["admissao_real"] = df["admissao_real"].fillna(True).astype(bool)
    # nome da obra: o do CC da SA (49), senão o da LTDA (52)
    nomes = (df[df["obra"].notna()].sort_values("centro_de_custo")
             .drop_duplicates("obra")[["obra", "nome_centro_custo"]].set_index("obra")["nome_centro_custo"])
    nomes = nomes.str.replace(r"\s*[-–]\s*Custos\s+(S\.?A\.?|LTDA\.?)\s*$", "", regex=True, case=False).str.strip()
    df["obra_nome"] = df["obra"].map(nomes).fillna(NAO_INFORMADO)
    return df


def data_referencia(df: pd.DataFrame) -> date:
    return max(df["data_referencia"].dropna())


def inicio_padrao(ref: date) -> date:
    """Período padrão da Central: do dia 1 do mesmo mês do ano anterior até a data da base."""
    return date(ref.year - 1, ref.month, 1)


def ativos_em(df: pd.DataFrame, d: date) -> pd.DataFrame:
    ativo = (df["data_admissao"] <= d) & (df["data_desligamento"].isna() | (df["data_desligamento"] > d))
    return df[ativo].sort_values(["id_funcionario", "data_admissao", "codigo_atribuicao"]).drop_duplicates("id_funcionario", keep="last")


def _fim_mes(d: date) -> date:
    return (d.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def evolucao(df: pd.DataFrame, ini: date, fim: date) -> pd.DataFrame:
    """Headcount no fim de cada mês do período (o último mês vai até `fim`) e a variação."""
    linhas, d = [], ini.replace(day=1)
    while d <= fim:
        corte = min(_fim_mes(d), fim)
        linhas.append({"mes": d, "corte": corte, "headcount": len(ativos_em(df, corte))})
        d = (d + timedelta(days=32)).replace(day=1)
    ev = pd.DataFrame(linhas, columns=["mes", "corte", "headcount"])
    ev["delta"] = ev["headcount"].diff()
    return ev


def eventos(df: pd.DataFrame, ini: date, fim: date) -> tuple[int, int]:
    """Admissões e desligamentos no período (regras do painel Turnover)."""
    adm = int((df["data_admissao"].between(ini, fim) & df["admissao_real"]).sum())
    desl = int((df["data_desligamento"].notna() & df["data_desligamento"].between(ini, fim) & ~df["desligamento_incorreto"]).sum())
    return adm, desl


def ponte(df: pd.DataFrame, ini: date, fim: date) -> dict:
    """Headcount no início -> + admissões -> − desligamentos -> ± outras movimentações -> fim.
    "Outras" fecha a conta: transferências para dentro/fora do filtro, recontratações e desligamentos
    lançados por engano."""
    inicio = len(ativos_em(df, ini - timedelta(days=1)))
    final = len(ativos_em(df, fim))
    adm, desl = eventos(df, ini, fim)
    return {"inicio": inicio, "admissoes": adm, "desligamentos": desl, "outras": final - inicio - adm + desl, "fim": final}


def variacao_por(df: pd.DataFrame, coluna: str, ini: date, fim: date) -> pd.DataFrame:
    """Headcount no início e no fim do período por grupo e a variação (quem mais cresceu/caiu)."""
    a = ativos_em(df, ini - timedelta(days=1)).groupby(coluna).size().rename("inicio")
    b = ativos_em(df, fim).groupby(coluna).size().rename("fim")
    t = pd.concat([a, b], axis=1).fillna(0).astype(int)
    t["variacao"] = t["fim"] - t["inicio"]
    return t.reset_index().rename(columns={coluna: "grupo"}).sort_values("variacao")


def contagem(a: pd.DataFrame, coluna: str, ordem: list[str] | None = None) -> pd.DataFrame:
    t = a.groupby(coluna).size().rename("headcount").reset_index()
    total = t["headcount"].sum()
    t["pct"] = t["headcount"] / total if total else 0
    t = t.sort_values("headcount", ascending=False)
    if ordem:
        t = t.set_index(coluna).reindex([o for o in ordem if o in set(t[coluna])]).reset_index()
    return t


def top_com_resto(t: pd.DataFrame, n: int = 10) -> tuple[pd.DataFrame, int, int]:
    """Os n maiores + quantos grupos e pessoas ficaram de fora."""
    resto = t.iloc[n:]
    return t.head(n), len(resto), int(resto["headcount"].sum())


def obras(a: pd.DataFrame) -> pd.DataFrame:
    """Headcount por obra (SA + LTDA unidas), com os CCs de cada empregador."""
    o = a[a["obra"].notna()]
    if o.empty:
        return pd.DataFrame(columns=["obra", "obra_nome", "empregador", "headcount", "total", "ccs"])
    t = o.groupby(["obra", "obra_nome", "empregador"]).size().rename("headcount").reset_index()
    t["total"] = t.groupby("obra")["headcount"].transform("sum")
    ccs = o.groupby("obra")["centro_de_custo"].agg(lambda s: " + ".join(sorted(set(map(str, s)))))
    t["ccs"] = t["obra"].map(ccs)
    return t.sort_values(["total", "obra"], ascending=[False, True])


def resumo(a: pd.DataFrame) -> dict:
    return {"headcount": len(a), "obras": int(a["obra"].nunique()), "lojas": int(a.loc[a["loja"], "centro_de_custo"].nunique()),
            "estados": int(a.loc[a["estado_nome"] != NAO_INFORMADO, "estado_nome"].nunique()),
            "cidades": int(a.loc[a["cidade"] != NAO_INFORMADO, "cidade"].nunique())}


def por_centro_de_custo(a: pd.DataFrame) -> pd.DataFrame:
    t = a.groupby(["centro_de_custo", "cc_texto", "diretoria", "area"]).size().rename("headcount").reset_index()
    total = t["headcount"].sum()
    t["pct"] = t["headcount"] / total if total else 0
    return t.sort_values("headcount", ascending=False)


def efetivo(a: pd.DataFrame) -> pd.DataFrame:
    """Lista nominal para exportação: todas as pessoas ativas do filtro."""
    cols = {"nome_funcionario": "Nome", "id_funcionario": "ID", "cc_texto": "Centro de custo", "area": "Área",
            "diretoria": "Diretoria", "cargo": "Cargo", "data_admissao": "Admissão"}
    return a[list(cols)].rename(columns=cols).sort_values(["Diretoria", "Área", "Centro de custo", "Nome"]).reset_index(drop=True)


def mapa_cidades(a: pd.DataFrame) -> pd.DataFrame:
    m = a.dropna(subset=["latitude", "longitude"])
    return (m.groupby(["cidade", "estado_nome", "latitude", "longitude"]).size().rename("headcount")
            .reset_index().sort_values("headcount", ascending=False))


def crescimento_anos(df: pd.DataFrame, ref: date, anos: int = 3) -> pd.DataFrame:
    """Headcount na mesma data de cada um dos últimos `anos` anos e a variação ano a ano."""
    linhas = []
    for k in range(anos, -1, -1):
        try:
            d = ref.replace(year=ref.year - k)
        except ValueError:  # 29/02
            d = ref.replace(year=ref.year - k, day=28)
        linhas.append({"data": d, "headcount": len(ativos_em(df, d))})
    t = pd.DataFrame(linhas)
    t["delta"] = t["headcount"].diff()
    t["pct"] = t["headcount"].pct_change()
    return t


def obras_lista(df: pd.DataFrame, desde: date) -> pd.DataFrame:
    """Obras com alguém ativo desde `desde`, da maior para a menor (pelo pico)."""
    o = df[df["obra"].notna() & (df["data_desligamento"].isna() | (df["data_desligamento"] >= desde))]
    t = o.groupby(["obra", "obra_nome"]).agg(ccs=("centro_de_custo", lambda s: " + ".join(sorted(set(map(str, s))))),
                                              pessoas=("id_funcionario", "nunique")).reset_index()
    return t.sort_values("pessoas", ascending=False)


def mobilizacao(df: pd.DataFrame, obra: str, fim: date) -> pd.DataFrame:
    """Curva de mobilização de uma obra: headcount no fim de cada mês, por empregador (SA / LTDA),
    da primeira admissão até `fim`."""
    o = df[df["obra"] == obra]
    if o.empty:
        return pd.DataFrame(columns=["mes", "empregador", "headcount"])
    linhas, d = [], min(o["data_admissao"]).replace(day=1)
    while d <= fim:
        corte = min(_fim_mes(d), fim)
        a = ativos_em(o, corte)
        for emp in ("SA", "LTDA"):
            linhas.append({"mes": d, "empregador": emp, "headcount": int((a["empregador"] == emp).sum())})
        d = (d + timedelta(days=32)).replace(day=1)
    return pd.DataFrame(linhas)


def inicio_mobilizacao(serie: pd.Series, fracao: float = .10, minimo: int = 3):
    """Primeiro mês em que a obra chega a `fracao` do próprio pico (e a pelo menos `minimo` pessoas).
    Algumas obras têm 1 ou 2 pessoas por anos antes de mobilizar: contar da primeira admissão engana."""
    if serie.empty:
        return None
    limite = max(minimo, serie.max() * fracao)
    acima = serie[serie >= limite]
    return acima.index.min() if len(acima) else serie.index.min()
