"""Painel Headcount Total — quantas pessoas a Pacaembu Construtora tem, onde estão e como o quadro
mudou (Desenvolvimento Organizacional).

Fonte: core.v_headcount_pessoas no Neon (uma linha por atribuição, migração 018), com um usuário de
banco só de leitura. Cálculos em metricas.py (usado também pela validação diária). Padrão visual,
barra lateral, cabeçalho com selos e cards: skill padrao-painel-streamlit (painel_padrao.py).

    streamlit run app.py
"""
from __future__ import annotations

import hashlib
import io
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import psycopg2
import pydeck as pdk
import streamlit as st

import auth
import metricas as m
import painel_padrao as pp

ASSETS = Path(__file__).resolve().parent / "assets"
ICONE = ASSETS / "icone-headcount-transparente.png"
AZUL, AMARELO, VERMELHO, VERDE = "#064D66", "#FAB900", "#F02727", "#22C55E"
AZUL_MEDIO, CINZA, CINZA_ESCURO = "#4A8FA8", "#6B7280", "#1F2937"
MESES_PT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
SEM_DADOS = "Sem pessoas no filtro selecionado."

st.set_page_config(page_title="Headcount Total · Pacaembu Construtora", page_icon=str(ICONE), layout="wide")

# login antes de qualquer dado (mesma tabela acesso.app_users dos outros painéis) + matriz de acessos
auth.exigir_login()
auth.exigir_acesso_ao_painel("headcount")


# ----------------------------------------------------------------------------- dados

@st.cache_data(ttl=600, show_spinner="Carregando dados…")
def carregar() -> tuple[pd.DataFrame, datetime | None]:
    with psycopg2.connect(st.secrets["neon"]["database_url"], connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM core.v_headcount_pessoas")
        base = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
        try:
            cur.execute("SELECT max(concluido_em) FROM ops.v_ultima_carga WHERE schema_nome = 'core' AND tabela = 'fato_funcionario'")
            carga = cur.fetchone()[0]
        except Exception:  # noqa: BLE001 — sem permissão no registro de cargas, usa a data da base
            conn.rollback()
            carga = None
    return m.preparar(base), carga


def _mes_txt(d: date) -> str:
    return f"{MESES_PT[d.month - 1]}/{d:%y}"


def _int(v) -> str:
    return f"{int(v):,}".replace(",", ".")


def _pct(v, casas=1) -> str:
    return "—" if v is None or pd.isna(v) else f"{v * 100:.{casas}f}%".replace(".", ",")


def _sinal(v: int) -> str:
    return f"+{_int(v)}" if v > 0 else ("−" + _int(-v) if v < 0 else "0")


def _info(titulo: str, texto: str = SEM_DADOS) -> None:
    st.html(f'<div class="titulo-graf">{titulo}</div>')
    st.info(texto, icon=":material/info:")


# ----------------------------------------------------------------------------- gráficos

def evolucao(ev: pd.DataFrame) -> dict:
    """Barras do headcount no fim de cada mês, com o total em cima e a variação (▲/▼) acima dele."""
    d = ev.assign(mes_txt=[_mes_txt(x) for x in ev["mes"]],
                  delta_txt=["" if pd.isna(x) else ("▲ " if x > 0 else "▼ " if x < 0 else "● ") + _int(abs(x)) for x in ev["delta"]],
                  cor_delta=[CINZA if pd.isna(x) or x == 0 else (VERDE if x > 0 else VERMELHO) for x in ev["delta"]],
                  total_txt=[_int(x) for x in ev["headcount"]])
    teto = float(d["headcount"].max()) * 1.2 if len(d) else 1
    x = {"field": "mes_txt", "type": "ordinal", "sort": list(d["mes_txt"]), "axis": {"labelAngle": 0, "title": None}}
    y = {"field": "headcount", "type": "quantitative", "scale": {"domain": [0, teto]},
         "axis": {"grid": True, "title": None, "labelExpr": "replace(format(datum.value, ',d'), ',', '.')"}}
    return {"data": {"values": d.to_dict("records")}, "layer": [
        {"mark": {"type": "bar", "color": AZUL, "cornerRadiusTopLeft": 4, "cornerRadiusTopRight": 4},
         "encoding": {"x": x, "y": y, "tooltip": [{"field": "mes_txt", "title": "Mês"}, {"field": "total_txt", "title": "Headcount"},
                                                   {"field": "delta_txt", "title": "Variação"}]}},
        {"mark": {"type": "text", "dy": -9, "fontSize": 12, "fontWeight": 800, "color": CINZA_ESCURO},
         "encoding": {"x": x, "y": y, "text": {"field": "total_txt"}}},
        {"mark": {"type": "text", "dy": -25, "fontSize": 10, "fontWeight": 700},
         "encoding": {"x": x, "y": y, "text": {"field": "delta_txt"}, "color": {"field": "cor_delta", "type": "nominal", "scale": None}}},
    ]}


def crescimento(t: pd.DataFrame) -> dict:
    """Headcount na mesma data de cada ano: total na barra e a variação ano a ano (pessoas e %)."""
    d = t.assign(ano=[f"{x:%d/%m/%Y}" for x in t["data"]], total_txt=[_int(v) for v in t["headcount"]],
                 var_txt=["" if pd.isna(dl) else f"{_sinal(int(dl))} ({'+' if p_ > 0 else ''}{_pct(p_)})" for dl, p_ in zip(t["delta"], t["pct"])],
                 cor=[AZUL if i == len(t) - 1 else AZUL_MEDIO for i in range(len(t))])
    teto = float(d["headcount"].max()) * 1.22 if len(d) else 1
    x = {"field": "ano", "type": "ordinal", "sort": list(d["ano"]), "axis": {"labelAngle": 0, "title": None}}
    y = {"field": "headcount", "type": "quantitative", "scale": {"domain": [0, teto]},
         "axis": {"grid": True, "title": None, "labelExpr": "replace(format(datum.value, ',d'), ',', '.')"}}
    return {"data": {"values": d.to_dict("records")}, "layer": [
        {"mark": {"type": "bar", "cornerRadiusTopLeft": 4, "cornerRadiusTopRight": 4, "width": {"band": .6}},
         "encoding": {"x": x, "y": y, "color": {"field": "cor", "type": "nominal", "scale": None},
                      "tooltip": [{"field": "ano", "title": "Data"}, {"field": "total_txt", "title": "Headcount"},
                                  {"field": "var_txt", "title": "Variação no ano"}]}},
        {"mark": {"type": "text", "dy": -9, "fontSize": 13, "fontWeight": 800, "color": CINZA_ESCURO},
         "encoding": {"x": x, "y": y, "text": {"field": "total_txt"}}},
        {"mark": {"type": "text", "dy": -27, "fontSize": 11, "fontWeight": 700, "color": VERDE},
         "encoding": {"x": x, "y": y, "text": {"field": "var_txt"}}},
    ]}


def curva_mobilizacao(t: pd.DataFrame) -> dict:
    """Headcount da obra no fim de cada mês, empilhado por empregador (SA / LTDA)."""
    d = t.assign(mes_txt=[_mes_txt(x) for x in t["mes"]])
    tot = d.groupby("mes_txt", sort=False)["headcount"].sum().reset_index()
    # com muitos meses (staff rotativo tem desde 2012) os números em cima das barras embolam
    tot = tot.assign(txt=[_int(v) if v and len(tot) <= 40 else "" for v in tot["headcount"]])
    x = {"field": "mes_txt", "type": "ordinal", "sort": list(dict.fromkeys(d["mes_txt"])),
         "axis": {"labelAngle": 0, "title": None, "labelOverlap": "greedy"}}
    return {"layer": [
        {"data": {"values": d.to_dict("records")}, "mark": {"type": "bar", "cornerRadiusTopLeft": 2, "cornerRadiusTopRight": 2},
         "encoding": {"x": x, "y": {"field": "headcount", "type": "quantitative", "stack": True, "axis": {"grid": True, "title": None}},
                      "color": {"field": "empregador", "scale": {"domain": ["SA", "LTDA", "Staff rotativo"], "range": [AZUL, AMARELO, AZUL_MEDIO]},
                                "legend": {"orient": "top", "labelExpr": "datum.label == 'SA' ? 'SA (CC 49…)' : datum.label == 'LTDA' ? 'LTDA (CC 52…)' : datum.label"}},
                      "tooltip": [{"field": "mes_txt", "title": "Mês"}, {"field": "empregador", "title": "Empregador"},
                                  {"field": "headcount", "title": "Pessoas"}]}},
        {"data": {"values": tot.to_dict("records")}, "mark": {"type": "text", "dy": -7, "fontSize": 10, "fontWeight": 700, "color": CINZA_ESCURO},
         "encoding": {"x": x, "y": {"field": "headcount", "type": "quantitative"}, "text": {"field": "txt"}}},
    ], "padding": {"top": 12}}


def variacao(t: pd.DataFrame) -> dict:
    """Barras divergentes: quem mais cresceu (verde) e quem mais encolheu (vermelho) no período."""
    d = t.assign(cor=[VERDE if v > 0 else VERMELHO for v in t["variacao"]], txt=[_sinal(v) for v in t["variacao"]],
                 lado=["dir" if v >= 0 else "esq" for v in t["variacao"]])
    ordem = list(t.sort_values("variacao", ascending=False)["grupo"])
    y = {"field": "grupo", "type": "nominal", "sort": ordem, "axis": {"title": None, "labelLimit": 210}}
    xq = {"field": "variacao", "type": "quantitative", "axis": {"grid": True, "title": None}}
    return {"data": {"values": d.to_dict("records")}, "layer": [
        {"mark": {"type": "bar", "cornerRadiusEnd": 3},
         "encoding": {"y": y, "x": xq, "color": {"field": "cor", "type": "nominal", "scale": None},
                      "tooltip": [{"field": "grupo", "title": "Grupo"}, {"field": "inicio", "title": "No início"},
                                  {"field": "fim", "title": "No fim"}, {"field": "txt", "title": "Variação"}]}},
        {"transform": [{"filter": "datum.lado == 'dir'"}],
         "mark": {"type": "text", "align": "left", "dx": 5, "fontSize": 11, "fontWeight": 700, "color": CINZA_ESCURO},
         "encoding": {"y": y, "x": xq, "text": {"field": "txt"}}},
        {"transform": [{"filter": "datum.lado == 'esq'"}],
         "mark": {"type": "text", "align": "right", "dx": -5, "fontSize": 11, "fontWeight": 700, "color": CINZA_ESCURO},
         "encoding": {"y": y, "x": xq, "text": {"field": "txt"}}},
    ], "padding": {"right": 28, "left": 6}}


def barras(t: pd.DataFrame, campo: str, cor: str = AZUL, limite: int = 190) -> dict:
    """Barras horizontais com o número na ponta (dentro da barra quando cabe)."""
    maximo = t["headcount"].max() if len(t) else 1
    d = t.assign(dentro=t["headcount"] / maximo >= .22, txt=[_int(v) for v in t["headcount"]],
                 pct_txt=[_pct(v) for v in t["pct"]])
    y = {"field": campo, "type": "nominal", "sort": list(t[campo]), "axis": {"title": None, "labelLimit": limite}}
    xq = {"field": "headcount", "type": "quantitative", "axis": {"title": None, "grid": True}}
    return {"data": {"values": d.to_dict("records")}, "layer": [
        {"mark": {"type": "bar", "cornerRadiusEnd": 4, "color": cor},
         "encoding": {"y": y, "x": xq, "tooltip": [{"field": campo, "title": " "}, {"field": "txt", "title": "Headcount"},
                                                   {"field": "pct_txt", "title": "Participação"}]}},
        {"transform": [{"filter": "datum.dentro"}], "mark": {"type": "text", "align": "right", "dx": -6, "fontSize": 11, "fontWeight": 700, "color": "#FFFFFF"},
         "encoding": {"y": y, "x": xq, "text": {"field": "txt"}}},
        {"transform": [{"filter": "!datum.dentro"}], "mark": {"type": "text", "align": "left", "dx": 5, "fontSize": 11, "fontWeight": 700, "color": cor},
         "encoding": {"y": y, "x": xq, "text": {"field": "txt"}}},
    ], "padding": {"right": 22}}


def donut(t: pd.DataFrame, campo: str) -> dict:
    cores = [AZUL, AMARELO, AZUL_MEDIO, VERMELHO, "#9CA3AF"]
    dominio = list(t[campo])
    d = t.assign(ordem=range(len(t)), rotulo=[_pct(p, 0) if p >= .04 else "" for p in t["pct"]], txt=[_int(v) for v in t["headcount"]])
    enc = {"theta": {"field": "headcount", "type": "quantitative", "stack": True}, "order": {"field": "ordem", "type": "quantitative"}}
    return {"data": {"values": d.to_dict("records")}, "view": {"stroke": None}, "layer": [
        {"mark": {"type": "arc", "innerRadius": 58, "outerRadius": 100, "padAngle": .02, "cornerRadius": 4, "stroke": "#fff", "strokeWidth": 2},
         "encoding": {**enc, "color": {"field": campo, "scale": {"domain": dominio, "range": cores[:len(dominio)]},
                                       "legend": {"orient": "right", "direction": "vertical", "symbolType": "circle", "labelFontSize": 12}},
                      "tooltip": [{"field": campo, "title": "Categoria"}, {"field": "txt", "title": "Headcount"},
                                  {"field": "pct", "title": "Participação", "format": ".1%"}]}},
        {"mark": {"type": "text", "radius": 79, "fontSize": 12, "fontWeight": 800, "color": "#FFFFFF"},
         "encoding": {**enc, "text": {"field": "rotulo"}}},
    ]}


def obras_empilhadas(t: pd.DataFrame) -> dict:
    """Top obras: barra por obra, dividida entre SA (49) e LTDA (52), total na ponta."""
    d = t.assign(rotulo=[f"{n}|CC {c}" for n, c in zip(t["obra_nome"], t["ccs"])], txt=[_int(v) for v in t["headcount"]])
    ordem = list(dict.fromkeys(d["rotulo"]))
    tot = d.drop_duplicates("rotulo")[["rotulo", "total"]].assign(txt=lambda x: [_int(v) for v in x["total"]])
    y = {"field": "rotulo", "type": "nominal", "sort": ordem,
         "axis": {"title": None, "labelLimit": 260, "labelExpr": "split(datum.label, '|')", "labelPadding": 6}}
    return {"layer": [
        {"data": {"values": d.to_dict("records")}, "mark": {"type": "bar", "cornerRadiusEnd": 3},
         "encoding": {"y": y, "x": {"field": "headcount", "type": "quantitative", "stack": True, "axis": {"title": None, "grid": True}},
                      "color": {"field": "empregador", "title": "Empregador", "scale": {"domain": ["SA", "LTDA"], "range": [AZUL, AMARELO]},
                                "legend": {"orient": "top", "labelExpr": "datum.label == 'SA' ? 'SA (CC 49…)' : 'LTDA (CC 52…)'"}},
                      "tooltip": [{"field": "obra_nome", "title": "Obra"}, {"field": "empregador", "title": "Empregador"},
                                  {"field": "txt", "title": "Pessoas"}, {"field": "ccs", "title": "Centros de custo"}]}},
        {"data": {"values": tot.to_dict("records")}, "mark": {"type": "text", "align": "left", "dx": 5, "fontSize": 11, "fontWeight": 800, "color": CINZA_ESCURO},
         "encoding": {"y": y, "x": {"field": "total", "type": "quantitative"}, "text": {"field": "txt"}}},
    ], "padding": {"right": 26}}


CORES_OBRAS = [AZUL, AMARELO, VERMELHO, AZUL_MEDIO, "#22A06B", "#8A4FD6", "#EB6834", "#6B7280"]


def comparar_obras(t: pd.DataFrame, alinhado: bool) -> dict:
    """Uma linha por obra: headcount no fim de cada mês (calendário) ou por mês desde o início da obra."""
    dominio = list(dict.fromkeys(t["obra_nome"]))
    if alinhado:
        x = {"field": "mes_obra", "type": "quantitative", "axis": {"title": "Meses desde a mobilização", "grid": False, "tickMinStep": 1}}
    else:
        x = {"field": "mes", "type": "temporal", "axis": {"title": None, "format": "%b/%y", "grid": False,
                                                           "labelExpr": "['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'][month(datum.value)] + '/' + timeFormat(datum.value, '%y')"}}
    return {"data": {"values": t.to_dict("records")},
            "mark": {"type": "line", "strokeWidth": 2.5, "point": {"size": 28, "filled": True}, "interpolate": "monotone"},
            "encoding": {"x": x, "y": {"field": "headcount", "type": "quantitative", "axis": {"grid": True, "title": None}},
                         "color": {"field": "obra_nome", "scale": {"domain": dominio, "range": CORES_OBRAS[:len(dominio)]},
                                   "legend": {"orient": "top", "columns": 2, "labelLimit": 360}},
                         "tooltip": [{"field": "obra_nome", "title": "Obra"}, {"field": "mes_txt", "title": "Mês"},
                                     {"field": "mes_obra", "title": "Mês da obra"}, {"field": "headcount", "title": "Pessoas"}]}}


def mapa(tab: pd.DataFrame) -> None:
    st.html('<div class="titulo-graf">Onde as pessoas trabalham</div>')
    if tab.empty:
        st.info(SEM_DADOS, icon=":material/info:")
        return
    d = tab.assign(raio=(tab["headcount"] ** .5) * 8500, hc_txt=[_int(v) for v in tab["headcount"]])
    # bolhas semitransparentes com borda: dá para ler o mapa (e as bolhas vizinhas) através delas
    camada = pdk.Layer("ScatterplotLayer", data=d, get_position="[longitude, latitude]", get_radius="raio",
                       get_fill_color=[6, 77, 102, 115], get_line_color=[6, 77, 102, 230], line_width_min_pixels=1.2,
                       stroked=True, pickable=True)
    vista = pdk.ViewState(latitude=float(d["latitude"].mean()), longitude=float(d["longitude"].mean()), zoom=4.3)
    deck = pdk.Deck(layers=[camada], initial_view_state=vista, map_provider="carto", map_style="light",
                    tooltip={"html": "<b>{cidade}</b> · {estado_nome}<br/>Headcount: {hc_txt}", "style": {"fontFamily": "Nunito"}})
    chave = hashlib.md5(d[["cidade", "headcount"]].to_json().encode()).hexdigest()
    st.pydeck_chart(deck, height=440, key=f"mapa-{chave}")


def planilha_efetivo(efetivo: pd.DataFrame, filtros: dict, ref: date) -> bytes:
    """Excel com o efetivo e uma aba com os filtros usados (fica registrado no arquivo)."""
    buf = io.BytesIO()
    linhas = [("Data da base", f"{ref:%d/%m/%Y}"), ("Gerado em", f"{datetime.now():%d/%m/%Y %H:%M}"), ("Pessoas", len(efetivo))]
    linhas += [(k, ", ".join(map(str, v)) if isinstance(v, (list, tuple)) else v) for k, v in filtros.items() if v]
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        efetivo.to_excel(xl, sheet_name="Efetivo", index=False)
        pd.DataFrame(linhas, columns=["Filtro", "Valor"]).to_excel(xl, sheet_name="Filtros", index=False)
        for aba in xl.sheets.values():
            for col in aba.columns:
                aba.column_dimensions[col[0].column_letter].width = min(60, max(len(str(c.value or "")) for c in col) + 2)
    return buf.getvalue()


# ----------------------------------------------------------------------------- página

try:
    df, carga = carregar()
except Exception as exc:  # noqa: BLE001
    st.error("Não consegui ler a base do Neon. Confira o bloco [neon] em .streamlit/secrets.toml.", icon=":material/error:")
    st.caption(type(exc).__name__)
    st.stop()

ref = m.data_referencia(df)
pp.logo(ICONE, "Headcount Total")

with pp.barra_lateral(fonte="Neon + Databricks", atualizado_em=carga or ref):
    st.markdown("**Período**")
    _padrao = (m.inicio_padrao(ref), ref)
    _escolha = st.date_input("Período", value=_padrao, min_value=date(2020, 1, 1), max_value=ref,
                             format="DD/MM/YYYY", label_visibility="collapsed")
    per_ini, per_fim = _escolha if isinstance(_escolha, (tuple, list)) and len(_escolha) == 2 else _padrao
    # opções dos filtros: só quem esteve no quadro em algum momento do período (sem CCs antigos)
    _no_periodo = df[(df["data_admissao"] <= per_fim) & (df["data_desligamento"].isna() | (df["data_desligamento"] >= per_ini))]
    opcoes = lambda c: sorted(_no_periodo[c].dropna().unique())  # noqa: E731
    st.caption("Cards, alocação, mapa e efetivo mostram o quadro na data final; a evolução e o movimento usam o período inteiro.")
    st.markdown("**Filtros**")
    sel = {
        "diretoria": st.multiselect("Diretoria", opcoes("diretoria"), placeholder="Todas"),
        "area": st.multiselect("Área", opcoes("area"), placeholder="Todas"),
        "cc_texto": st.multiselect("Centro de custo", opcoes("cc_texto"), placeholder="Todos"),
        "estado_nome": st.multiselect("Estado", opcoes("estado_nome"), placeholder="Todos"),
        "nivel": st.multiselect("Nível de cargo", list(m.contagem(m.ativos_em(df, ref), "nivel")["nivel"]), placeholder="Todos"),
        "categoria_atribuicao": st.multiselect("Categoria de atribuição", opcoes("categoria_atribuicao"), placeholder="Todas"),
    }

per_fim = min(per_fim, ref)
rotulos = {"diretoria": "Diretoria", "area": "Área", "cc_texto": "Centro de custo", "estado_nome": "Estado",
           "nivel": "Nível", "categoria_atribuicao": "Categoria"}
filtros_txt = {"Período": f"{per_ini:%d/%m/%Y} a {per_fim:%d/%m/%Y}", **{rotulos[k]: v for k, v in sel.items()}}
pp.cabecalho("Headcount Total", atualizado_em=carga or ref, filtros=filtros_txt,
             legenda=f"Quadro ativo em {per_fim:%d/%m/%Y} · admitido até a data e sem desligamento (ou desligado depois)")

base = df
for col, vals in sel.items():
    if vals:
        base = base[base[col].isin(vals)]
ativos = m.ativos_em(base, per_fim)
if ativos.empty:
    st.info(SEM_DADOS, icon=":material/info:")
    st.stop()

r = m.resumo(ativos)
pt = m.ponte(base, per_ini, per_fim)
tres = m.crescimento_anos(base, per_fim, 3)
ev = m.evolucao(base, per_ini, per_fim)
ant = len(m.ativos_em(base, per_fim.replace(day=1) - timedelta(days=1)))
c = st.columns(6)
with c[0]:
    dl = r["headcount"] - ant
    pp.kpi("Headcount ativo", _int(r["headcount"]), "Estável vs mês ant." if dl == 0 else f"{'▲' if dl > 0 else '▼'} {_int(abs(dl))} vs mês ant.",
           VERDE if dl > 0 else VERMELHO if dl < 0 else CINZA)
with c[1]:
    cres = (pt["fim"] - pt["inicio"]) / pt["inicio"] if pt["inicio"] else None
    pp.kpi("Crescimento no período", ("+" if (cres or 0) > 0 else "") + _pct(cres), f"{_sinal(pt['fim'] - pt['inicio'])} pessoas",
           VERDE if (cres or 0) > 0 else VERMELHO if (cres or 0) < 0 else CINZA)
with c[2]:
    d3 = int(tres["headcount"].iloc[-1] - tres["headcount"].iloc[0])
    p3 = d3 / tres["headcount"].iloc[0] if tres["headcount"].iloc[0] else None
    pp.kpi("Crescimento em 3 anos", ("+" if (p3 or 0) > 0 else "") + _pct(p3), f"{_sinal(d3)} desde {tres['data'].iloc[0]:%m/%Y}",
           VERDE if d3 > 0 else VERMELHO if d3 < 0 else CINZA)
with c[3]:
    pp.kpi("Admissões no período", _int(pt["admissoes"]), f"média de {_int(round(pt['admissoes'] / max(1, len(ev))))} por mês")
with c[4]:
    pp.kpi("Obras ativas", _int(r["obras"]), "SA + LTDA juntas")
with c[5]:
    pp.kpi("Lojas ativas", _int(r["lojas"]), f"{_int(r['cidades'])} cidades · {_int(r['estados'])} estados")

# ---------------------------------------------------------------- evolução e movimento
pp.secao("Como o quadro evoluiu")
pp.grafico("Headcount no fim de cada mês", evolucao(ev), 330, SEM_DADOS)

g1, g2 = st.columns([5, 7])
with g1:
    pp.grafico("Crescimento nos últimos 3 anos", crescimento(tres), 320, SEM_DADOS)
    st.html(f'<div class="nota">Headcount em {per_fim:%d/%m} de cada ano e a variação sobre o ano anterior. '
            'Com filtro, cada pessoa conta na diretoria/área/CC atual.</div>')
with g2:
    with st.container(horizontal=True, vertical_alignment="center"):
        st.html('<div class="titulo-graf" style="margin-bottom:0">Quem mais cresceu e quem mais encolheu no período</div>')
        agrupar = st.segmented_control("Agrupar", ["Área", "Diretoria", "Centro de custo"], default="Área",
                                       label_visibility="collapsed", key="agrupar_variacao") or "Área"
    col_var = {"Área": "area", "Diretoria": "diretoria", "Centro de custo": "cc_texto"}[agrupar]
    var = m.variacao_por(base, col_var, per_ini, per_fim)
    var = var[var["variacao"] != 0]
    var = pd.concat([var.tail(6), var.head(6)]).drop_duplicates("grupo") if len(var) else var
    if var.empty:
        st.info("Sem variação no período para o filtro selecionado.", icon=":material/info:")
    else:
        pp.grafico("", variacao(var), 320, SEM_DADOS)

# ---------------------------------------------------------------- alocação
pp.secao("Onde as pessoas estão")
a1, a2 = st.columns([7, 5])
with a1:
    pp.grafico("Alocação por diretoria", barras(m.contagem(ativos, "diretoria"), "diretoria"), 360, SEM_DADOS)
with a2:
    pp.grafico("Categoria de atribuição", donut(m.contagem(ativos, "categoria_atribuicao"), "categoria_atribuicao"), 290, SEM_DADOS)

b1, b2 = st.columns(2)
with b1:
    top_area, n_resto, p_resto = m.top_com_resto(m.contagem(ativos, "area"), 10)
    pp.grafico("Áreas com mais pessoas", barras(top_area, "area"), 340, SEM_DADOS)
    if n_resto:
        st.html(f'<div class="nota">+ {_int(n_resto)} outras áreas somam {_int(p_resto)} pessoas '
                f'({_pct(p_resto / r["headcount"])} do quadro). Filtre uma diretoria para ver as áreas dela.</div>')
with b2:
    with st.container(horizontal=True, vertical_alignment="center"):
        st.html('<div class="titulo-graf" style="margin-bottom:0">Nível de cargo</div>')
        ver = st.segmented_control("Ver por", ["Nível", "Função"], default="Nível", label_visibility="collapsed", key="ver_nivel") or "Nível"
    if ver == "Nível":
        pp.grafico("", barras(m.contagem(ativos, "nivel"), "nivel", limite=210), 330, SEM_DADOS)
        st.html('<div class="nota">Nível de gerenciamento do cadastro, com as variações juntas: Gerente inclui Gerente de '
                'Vendas; Gerente Executivo inclui os de Obras; Coordenador/Especialista inclui Coordenador de Obras; '
                'Diretor inclui Diretor de Obras. Veja o detalhe em "Função".</div>')
    else:
        top_f, n_f, p_f = m.top_com_resto(m.contagem(ativos, "funcao"), 12)
        pp.grafico("", barras(top_f, "funcao"), 380, SEM_DADOS)
        if n_f:
            st.html(f'<div class="nota">+ {_int(n_f)} outras funções somam {_int(p_f)} pessoas.</div>')

# ---------------------------------------------------------------- centros de custo e obras
pp.secao("Centros de custo e obras")
cc_tab = m.por_centro_de_custo(ativos)
o1, o2 = st.columns(2)
with o1:
    pp.grafico("10 centros de custo com mais pessoas", barras(cc_tab.head(10), "cc_texto", limite=260), 380, SEM_DADOS)
with o2:
    ob = m.obras(ativos)
    top_obras = ob[ob["obra"].isin(ob.drop_duplicates("obra").head(10)["obra"])]
    if top_obras.empty:
        _info("10 obras com mais pessoas", "Sem obras no filtro selecionado.")
    else:
        pp.grafico("10 obras com mais pessoas", obras_empilhadas(top_obras), 420, SEM_DADOS)
        st.html('<div class="nota">Obra = CCs 49 (empregador SA) e 52 (empregador LTDA) com os mesmos 3 últimos dígitos, '
                'somados; abaixo do nome, os CCs de cada obra.</div>')

pp.secao("Curva de mobilização das obras")
obras_op = m.obras_lista(base, per_fim.replace(year=per_fim.year - 3))
if obras_op.empty:
    st.info("Sem obras no filtro selecionado.", icon=":material/info:")
else:
    obra_por_rotulo = {f"{n} · CC {c}": o for o, n, c in zip(obras_op["obra"], obras_op["obra_nome"], obras_op["ccs"])}
    escolhidas = st.multiselect("Obras", list(obra_por_rotulo), default=list(obra_por_rotulo)[:1], max_selections=8,
                                placeholder="Escolha até 8 obras para comparar", label_visibility="collapsed", key="obras_mobilizacao")
    if not escolhidas:
        st.info("Escolha ao menos uma obra.", icon=":material/info:")
    elif len(escolhidas) == 1:
        obra_sel = obra_por_rotulo[escolhidas[0]]
        mob = m.mobilizacao(base, obra_sel, per_fim)
        tot_mes = mob.groupby("mes")["headcount"].sum()
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            pp.kpi("Início da obra", f"{tot_mes.index.min():%m/%Y}" if len(tot_mes) else "—", "primeira admissão")
        with k2:
            pp.kpi("Pico", _int(tot_mes.max()) if len(tot_mes) else "—", f"em {_mes_txt(tot_mes.idxmax())}" if len(tot_mes) else "")
        with k3:
            pp.kpi("Hoje", _int(tot_mes.iloc[-1]) if len(tot_mes) else "—",
                   f"{_pct(tot_mes.iloc[-1] / tot_mes.max())} do pico" if len(tot_mes) and tot_mes.max() else "")
        with k4:
            pp.kpi("Passaram pela obra", _int(base.loc[base["curva_chave"] == obra_sel, "id_funcionario"].nunique()), "pessoas diferentes")
        pp.grafico("", curva_mobilizacao(mob), 300, SEM_DADOS)
        st.html('<div class="nota">Pessoas ativas no fim de cada mês, da primeira admissão até a data final, separadas por '
                'empregador. Mostra quando a obra mobilizou, o pico e a desmobilização. Escolha mais obras para comparar '
                '(os CCs de staff rotativo 47501 e 47502 também estão na lista).</div>')
    else:
        eixo = st.segmented_control("Eixo", ["Pela mobilização de cada obra", "Pelo calendário"], default="Pela mobilização de cada obra",
                                    label_visibility="collapsed", key="eixo_mobilizacao") or "Pela mobilização de cada obra"
        curvas, resumo_obras = [], []
        for rot in escolhidas:
            o = obra_por_rotulo[rot]
            mob = m.mobilizacao(base, o, per_fim).groupby("mes")["headcount"].sum().reset_index()
            nome = obras_op.loc[obras_op["obra"] == o, "obra_nome"].iloc[0]
            inicio_mob = m.inicio_mobilizacao(mob.set_index("mes")["headcount"])
            mob = mob[mob["mes"] >= inicio_mob].reset_index(drop=True)
            mob = mob.assign(obra_nome=nome, mes_obra=range(1, len(mob) + 1), mes_txt=[_mes_txt(x) for x in mob["mes"]])
            curvas.append(mob)
            resumo_obras.append({"Obra": nome, "CCs": obras_op.loc[obras_op["obra"] == o, "ccs"].iloc[0],
                                 "Mobilização": f"{mob['mes'].min():%m/%Y}", "Pico": int(mob["headcount"].max()),
                                 "Mês do pico": _mes_txt(mob.loc[mob["headcount"].idxmax(), "mes"]),
                                 "Meses até o pico": int(mob.loc[mob["headcount"].idxmax(), "mes_obra"]) - 1,
                                 "Hoje": int(mob["headcount"].iloc[-1]),
                                 "Passaram pela obra": int(base.loc[base["curva_chave"] == o, "id_funcionario"].nunique())})
        curvas = pd.concat(curvas, ignore_index=True)
        curvas["mes"] = pd.to_datetime(curvas["mes"])
        pp.grafico("", comparar_obras(curvas, eixo == "Pela mobilização de cada obra"), 340, SEM_DADOS)
        st.dataframe(pd.DataFrame(resumo_obras), hide_index=True, width="stretch",
                     column_config={"Obra": st.column_config.TextColumn(width="large")})
        st.html('<div class="nota">"Pela mobilização" alinha as curvas no mês em que cada obra chegou a 10% do próprio pico '
                '(e a pelo menos 3 pessoas) — antes disso muitas obras têm 1 ou 2 pessoas por meses. Compara o ritmo de '
                'mobilização e o tamanho do pico. SA e LTDA somadas.</div>')

# ---------------------------------------------------------------- mapa
pp.secao("Mapa")
m1, m2 = st.columns([8, 4])
with m1:
    mapa(m.mapa_cidades(ativos))
with m2:
    pp.grafico("Headcount por estado", barras(m.contagem(ativos, "estado_nome"), "estado_nome"), 200, SEM_DADOS)
    pp.grafico("Cidades com mais pessoas", barras(m.contagem(ativos, "cidade").head(8), "cidade", cor=AZUL_MEDIO), 230, SEM_DADOS)

# ---------------------------------------------------------------- efetivo
pp.secao("Efetivo por centro de custo")
st.dataframe(cc_tab[["cc_texto", "diretoria", "area", "headcount", "pct"]], hide_index=True, width="stretch", height=420,
             column_config={"cc_texto": st.column_config.TextColumn("Centro de custo", width="large"),
                            "diretoria": "Diretoria", "area": "Área",
                            "headcount": st.column_config.NumberColumn("Headcount", format="%d"),
                            "pct": st.column_config.ProgressColumn("Participação", format="percent", min_value=0,
                                                                   max_value=float(cc_tab["pct"].max()) if len(cc_tab) else 1)})
ef = m.efetivo(ativos)
st.download_button(f"Baixar efetivo ({_int(len(ef))} pessoas, Excel)", planilha_efetivo(ef, filtros_txt, per_fim),
                   f"efetivo_{per_fim:%Y%m%d}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   icon=":material/download:", type="primary")
st.caption("O efetivo traz nome, ID, centro de custo (número e nome), área, diretoria, cargo e admissão de todas as "
           "pessoas ativas do filtro na data final; a aba \"Filtros\" registra os filtros usados.")
