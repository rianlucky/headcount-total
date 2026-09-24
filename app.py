from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

import auth

ICON_PATH = Path(__file__).parent / "assets" / "icone-headcount-transparente.png"

st.set_page_config(
    page_title="Pacaembu | Headcount público",
    page_icon=str(ICON_PATH) if ICON_PATH.exists() else ":material/groups:",
    layout="wide",
    # "expanded" força a sidebar aberta mesmo em tela estreita, onde ela então
    # sobrepõe o conteúdo em vez de recolher — quebrava o layout no celular.
    initial_sidebar_state="auto",
)

# Sublinhado dos títulos (h3) e cards de composição com sombra — navy #064D66
# (mesmo tom do card de KPI Vega-Lite, ver BRAND_COLOR) + azul de destaque
# #2a78d6 do RealizaDO. Usa `key=` pra gerar classes estáveis (.st-key-...) em
# vez de depender de testids internos do Streamlit.
#
# Injetado ANTES do require_login(): esse mesmo CSS estiliza a tela de login
# (cartão dividido, gradiente navy) e precisa estar ativo já na primeira tela,
# já que require_login() dá st.stop() antes do resto do script rodar.
st.markdown(
    """
    <style>
    h3 {
        font-weight: 700;
        color: #064D66;
        margin-top: 25px;
    }
    .st-key-card-diretoria, .st-key-card-area, .st-key-card-state, .st-key-card-job-level, .st-key-card-evolution, .st-key-card-assignment-category {
        background-color: #FFFFFF;
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0, 50, 68, 0.08);
    }
    /* Vega/SVG não tem noção de "card arredondado" sozinho — o container corta
       os cantos quadrados do gráfico do KPI (ver render_kpi_card). */
    [class*="st-key-kpi-card-"] {
        border-radius: 14px;
        overflow: hidden;
        box-shadow: 0 2px 8px rgba(0, 50, 68, 0.10);
        border: none;
    }
    /* `showWidgetBorder = true` (tema) desenha uma borda em volta do container —
       sem isso, sobrava um fiozinho cinza por cima da faixa navy do card. */
    [class*="st-key-kpi-card-"] > div {
        border: none !important;
    }

    /* Tela de login (cartão dividido: marca à esquerda, formulário à direita) —
    mesmo padrão visual do painel Turnover Comercial, com o navy da marca. */
    .st-key-login_page { margin-top: 10vh; }
    [data-testid="stVerticalBlockBorderWrapper"].st-key-login_card {
        border: none !important; border-radius: 16px; overflow: hidden; padding: 0 !important;
        box-shadow: 0 14px 40px rgba(6, 77, 102, .18);
    }
    .st-key-login_card [data-testid="stHorizontalBlock"] { gap: 0 !important; }
    .st-key-login_left {
        background: linear-gradient(160deg, #064D66 0%, #2a78d6 100%);
        min-height: 460px; height: 100%; padding: 48px 30px;
        display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center;
    }
    .login-logo-pill {
        background: #FFFFFF; border-radius: 999px; padding: 10px 18px 10px 10px;
        display: inline-flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 18px;
        box-shadow: 0 6px 18px rgba(0, 0, 0, .18); max-width: 100%; box-sizing: border-box;
    }
    .login-logo-pill img { width: 30px; max-width: 100%; height: auto; display: block; flex-shrink: 0; }
    .login-pill-title { font-size: 15px; font-weight: 700; color: #064D66; white-space: nowrap; }
    .login-brand-sub { color: rgba(255, 255, 255, .88); font-size: 12px; line-height: 1.65; max-width: 220px; margin: 0 auto; text-align: center; }
    .st-key-login_right { padding: 48px 44px; min-height: 460px; height: 100%; display: flex; flex-direction: column; justify-content: center; }
    .login-form-title { font-size: 18px; font-weight: 600; color: #111110; margin: 0 0 4px; line-height: 1.4; }
    .login-form-sub { font-size: 12.5px; color: #6b6b68; margin: 0 0 22px; line-height: 1.5; }
    .st-key-login_right div[data-testid="stButton"] button {
        background: linear-gradient(160deg, #064D66 0%, #2a78d6 100%) !important; border: none !important;
        font-weight: 600 !important; border-radius: 8px !important; padding: 10px 0 !important;
    }
    .st-key-login_right div[data-testid="stButton"] button p { color: #FFFFFF !important; }
    .st-key-login_right div[data-testid="stButton"] button:hover { filter: brightness(1.08); }
    </style>
    """,
    unsafe_allow_html=True,
)

# Tela de login (e-mail + senha) igual à do painel Turnover Comercial — mesmo
# Neon que já guarda os dados de headcount, tabela própria `app_users` (ver
# auth.py). Bloqueia o resto do script até autenticar.
auth.init_db()
auth.require_login()

# Brand palette is defined in .streamlit/config.toml (navy institucional #064D66,
# unified with the KPI card's Vega-Lite spec). Keep the app usable without CSS.
COMPANY_NAME = "Pacaembu"
BRAND_COLOR = "#064D66"
DATA_END = date(2026, 8, 31)
DIRETORIAS = [
    "Construção",
    "Engenharia",
    "Operações",
    "Comercial",
    "Corporativo",
]
STATES = ["São Paulo", "Rio de Janeiro", "Minas Gerais", "Paraná", "Bahia"]
# Uma cidade (a capital) por estado mock, só pra dar coordenada ao mapa de
# dispersão quando o Neon está fora do ar — não precisa ser realista.
MOCK_CITY_BY_STATE = {
    "São Paulo": ("São Paulo", -23.5505, -46.6333),
    "Rio de Janeiro": ("Rio de Janeiro", -22.9068, -43.1729),
    "Minas Gerais": ("Belo Horizonte", -19.9167, -43.9345),
    "Paraná": ("Curitiba", -25.4284, -49.2733),
    "Bahia": ("Salvador", -12.9714, -38.5014),
}
MOCK_JOB_FUNCTION_BY_LEVEL = {
    "Direção": "Diretor",
    "Gerência": "Gerente",
    "Coordenação": "Coordenador",
    "Especialista": "Especialista",
    "Operacional": "Analista",
}
MOCK_ASSIGNMENT_CATEGORY_BY_DIRETORIA = {
    "Construção": "Obras",
    "Engenharia": "Corporativo",
    "Operações": "Corporativo",
    "Comercial": "Comercial",
    "Corporativo": "Corporativo",
}
AREAS_BY_DIRETORIA = {
    "Construção": ["Obras", "Apoio às obras"],
    "Engenharia": ["Engenharia", "Desenvolvimento"],
    "Operações": ["Operações", "Suprimentos"],
    "Comercial": ["Comercial", "Atendimento"],
    "Corporativo": ["Corporativo", "Serviços compartilhados"],
}
JOB_LEVELS = ["Direção", "Gerência", "Coordenação", "Especialista", "Operacional"]
AREAS = [center for centers in AREAS_BY_DIRETORIA.values() for center in centers]
MESES_ABREV = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}
# Backfilled months before the weekly snapshots existed carry only a company-wide
# total (no reliable historical diretoria/centro de custo — see CONTEXT.md).
HISTORICAL_TOTAL_SENTINEL = "Total histórico"


def _ordered_options(values: pd.Series, preferred_order: list[str] | None = None) -> list[str]:
    """Distinct values from live data, sorted alphabetically unless a preferred order is given."""
    distinct = set(values.dropna())
    if preferred_order:
        ordered = [v for v in preferred_order if v in distinct]
        ordered += sorted(distinct - set(preferred_order))
        return ordered
    return sorted(distinct)


def _make_mock_data() -> pd.DataFrame:
    months = pd.date_range("2024-01-31", DATA_END, freq="ME")
    rng = np.random.default_rng(42)
    diretoria_base = {
        "Construção": 540,
        "Engenharia": 245,
        "Operações": 210,
        "Comercial": 165,
        "Corporativo": 120,
    }
    rows: list[dict[str, object]] = []
    for month_index, month in enumerate(months):
        seasonal = 1 + 0.018 * np.sin(month_index / 2.8)
        for diretoria_index, diretoria in enumerate(DIRETORIAS):
            diretoria_growth = 1 + (month_index * (0.003 + diretoria_index * 0.0007))
            total = int(round(diretoria_base[diretoria] * seasonal * diretoria_growth + rng.normal(0, 7)))
            center_values = rng.multinomial(max(total, 1), [0.68, 0.32])
            level_mix = (
                [0.02, 0.08, 0.12, 0.18, 0.60]
                if diretoria in ("Construção", "Operações")
                else [0.03, 0.12, 0.18, 0.38, 0.29]
            )
            # Field-heavy diretorias spread across job sites; others concentrate at the São Paulo HQ.
            state_mix = [0.32, 0.20, 0.18, 0.17, 0.13] if diretoria == "Construção" else [0.70, 0.10, 0.08, 0.07, 0.05]
            for center, center_headcount in zip(AREAS_BY_DIRETORIA[diretoria], center_values):
                level_values = rng.multinomial(max(int(center_headcount), 1), level_mix)
                for level, level_headcount in zip(JOB_LEVELS, level_values):
                    state_values = rng.multinomial(max(int(level_headcount), 1), state_mix)
                    for state, headcount in zip(STATES, state_values):
                        city, city_lat, city_lon = MOCK_CITY_BY_STATE[state]
                        rows.append(
                            {
                                "snapshot_date": month.date(),
                                "diretoria": diretoria,
                                "area": center,
                                "job_level": level,
                                "job_function": MOCK_JOB_FUNCTION_BY_LEVEL[level],
                                "state": state,
                                "city": city,
                                "city_lat": city_lat,
                                "city_lon": city_lon,
                                "assignment_category": MOCK_ASSIGNMENT_CATEGORY_BY_DIRETORIA[diretoria],
                                "headcount": int(headcount),
                            }
                        )
    return pd.DataFrame(rows)


def _neon_database_url() -> str | None:
    """Read the Neon connection string: .streamlit/secrets.toml first, env var as fallback."""
    try:
        return st.secrets["neon"]["database_url"]
    except Exception:
        return os.getenv("NEON_DATABASE_URL")


@st.cache_data(ttl="15m", show_spinner="Carregando dados de headcount...")
def load_headcount_data() -> tuple[pd.DataFrame, str]:
    """Load the public aggregate contract, falling back to a deterministic mock.

    The live source is Neon, kept fresh by the weekly batch job in `etl/` (see
    CONTEXT.md) — the app never talks to Databricks directly.
    """
    database_url = _neon_database_url()
    if database_url:
        try:
            import psycopg2

            query = """
                SELECT snapshot_date, diretoria, area, job_level, job_function, state,
                       city, city_lat, city_lon, assignment_category, headcount
                FROM org.headcount_publico
            """
            with psycopg2.connect(database_url) as connection:
                data = pd.read_sql(query, connection)
            data["snapshot_date"] = pd.to_datetime(data["snapshot_date"]).dt.date
            return data, "Neon"
        except Exception as exc:
            # A public-facing mock must still open when integration credentials expire.
            # Printed (not shown in the UI) so it's visible in the terminal/server logs while debugging setup.
            print(f"[headcount] Neon connection failed, falling back to mock: {exc}")
    return _make_mock_data(), "Mock local"


@st.cache_data(ttl="15m")
def load_extra_metrics() -> tuple[int | None, int | None]:
    """"Obras ativas"/"Lojas ativas": contagem de centros de custo distintos por
    prefixo (49xxx/48xxx), gravada à parte pelo ETL (ver etl/extract_databricks_to_neon.py)
    — não dá pra derivar da tabela principal, que só guarda "área" (já agrupada),
    não o código bruto do centro de custo. Sem Neon (mock local), retorna None/None."""
    database_url = _neon_database_url()
    if not database_url:
        return None, None
    try:
        import psycopg2

        query = """
            SELECT obras_ativas, lojas_ativas
            FROM org.headcount_extra_metrics
            ORDER BY snapshot_date DESC
            LIMIT 1
        """
        with psycopg2.connect(database_url) as connection:
            row = pd.read_sql(query, connection)
        if row.empty:
            return None, None
        return int(row["obras_ativas"].iloc[0]), int(row["lojas_ativas"].iloc[0])
    except Exception as exc:
        print(f"[headcount] Falha ao ler métricas extras (obras/lojas ativas): {exc}")
        return None, None


def format_number(value: float | int) -> str:
    return f"{int(round(value)):,}".replace(",", ".")


def format_percent(value: float) -> str:
    return f"{value:+.1f}%".replace(".", ",")


def format_growth(value: float | None) -> str:
    return format_percent(value) if value is not None else "—"


def growth_pct(monthly_totals: pd.Series, months_back: int) -> float | None:
    """% change between the latest snapshot and `months_back` snapshots earlier."""
    if len(monthly_totals) <= months_back:
        return None
    previous = monthly_totals.iloc[-1 - months_back]
    if not previous:
        return None
    return ((monthly_totals.iloc[-1] / previous) - 1) * 100


def render_variation_chart(data: pd.DataFrame, category: str, height: int) -> None:
    """Diverging bar chart (green = grew, red = shrank) for month-over-month movers."""
    chart = (
        alt.Chart(data)
        .mark_bar(cornerRadius=6)
        .encode(
            x=alt.X("variacao:Q", title="Variação (colaboradores)"),
            y=alt.Y(f"{category}:N", title=None, sort="-x", axis=alt.Axis(labelLimit=260)),
            color=alt.condition(alt.datum.variacao >= 0, alt.value("#0ca30c"), alt.value("#d03b3b")),
            tooltip=[category, "atual", "anterior", "variacao"],
        )
        .properties(height=height)
    )
    st.altair_chart(chart)


def _kpi_card_spec(label: str, value_text: str, value_color: str, subtitle_text: str, subtitle_color: str) -> dict:
    """KPI card matching the existing Databricks/Genie dashboards: navy ribbon label
    (with a cut/arrow point, built from a white rect + rotated-square notch), big
    value, colored subtitle, gold accent bar at the bottom. Exact spec supplied by
    the user, adapted to take plain Python values instead of a bound dataset."""

    def _layer(mark: dict, encoding: dict) -> dict:
        return {"data": {"values": [{}]}, "mark": mark, "encoding": encoding}

    return {
        "width": "container",
        "height": "container",
        "config": {
            "autosize": {"type": "fit", "contains": "padding"},
            "view": {"stroke": None, "fill": "#FFFFFF"},
            "text": {"font": "sans-serif"},
        },
        "layer": [
            _layer(
                {"type": "rect", "color": "#FAB900", "tooltip": None},
                {
                    "x": {"value": 0}, "x2": {"value": {"expr": "width"}},
                    "y": {"value": {"expr": "height-7"}}, "y2": {"value": {"expr": "height"}},
                },
            ),
            _layer(
                {"type": "rect", "color": "#064D66", "tooltip": None},
                {
                    "x": {"value": 0}, "x2": {"value": {"expr": "width"}},
                    "y": {"value": 0}, "y2": {"value": {"expr": "height*0.20"}},
                },
            ),
            _layer(
                {"type": "rect", "color": "#FFFFFF", "tooltip": None},
                {
                    "x": {"value": {"expr": "width*0.88 - height*0.10"}},
                    "x2": {"value": {"expr": "width*0.88 + height*0.10 + 1"}},
                    "y": {"value": 0}, "y2": {"value": {"expr": "height*0.21"}},
                },
            ),
            _layer(
                {
                    "type": "point", "shape": "square", "filled": True, "fill": "#FFFFFF",
                    "color": "#FFFFFF", "stroke": None, "strokeWidth": 0, "opacity": 1, "fillOpacity": 1,
                    "tooltip": None,
                },
                {
                    "x": {"value": {"expr": "width*0.88 + height*0.15"}},
                    "y": {"value": {"expr": "height*0.105"}},
                    "size": {"value": {"expr": "pow(height*0.20, 2)"}},
                    "angle": {"value": 45},
                },
            ),
            _layer(
                {
                    "type": "text", "font": "sans-serif", "color": "#FFFFFF", "fontSize": 10,
                    "fontWeight": "bold", "align": "left", "baseline": "middle",
                    # Trunca com "…" em vez de invadir a ponta da faixa quando o
                    # card fica estreito (tela pequena ou muitos cards na linha).
                    "limit": {"expr": "width*0.72 - 14"},
                },
                {"x": {"value": 14}, "y": {"value": {"expr": "height*0.10"}}, "text": {"value": label.upper()}},
            ),
            _layer(
                {
                    "type": "text", "font": "sans-serif", "color": value_color, "fontSize": 32,
                    "fontWeight": "700", "align": "center", "baseline": "middle",
                    "limit": {"expr": "width - 12"},
                },
                {"x": {"value": {"expr": "width/2"}}, "y": {"value": {"expr": "height*0.55"}}, "text": {"value": value_text}},
            ),
            _layer(
                {
                    "type": "text", "font": "sans-serif", "color": subtitle_color, "fontSize": 11,
                    "fontWeight": "600", "align": "center", "baseline": "middle",
                    "limit": {"expr": "width - 12"},
                },
                {"x": {"value": {"expr": "width/2"}}, "y": {"value": {"expr": "height*0.83"}}, "text": {"value": subtitle_text}},
            ),
        ],
    }


def render_kpi_card(
    label: str,
    value_text: str,
    subtitle_text: str,
    subtitle_color: str = "#6B7280",
    value_color: str = "#1F2937",
    height: int = 140,
) -> None:
    spec = _kpi_card_spec(label, value_text, value_color, subtitle_text, subtitle_color)
    # `st-key-kpi-card-*` container clips the chart's square corners (Vega/SVG has
    # no notion of a rounded card boundary on its own — see CSS block above).
    # The chart's own `key` is content-derived so Streamlit remounts it whenever
    # the value/label changes, instead of patching the previous instance in place
    # — vega-embed's incremental data update does not always resort ordinal axes
    # (see render_evolution_chart for the bug this caused with a stale key).
    card_key = "".join(ch for ch in label if ch.isalnum()).lower()
    with st.container(key=f"kpi-card-{card_key}"):
        st.vega_lite_chart(
            spec, width="stretch", height=height, theme=None,
            key=f"kpi-vega-{card_key}-{value_text}-{subtitle_text}",
        )


def render_evolution_chart(data: pd.DataFrame, category: str, value: str, label: str, height: int) -> None:
    """Column chart for the headcount trend — Vega-Lite direct (not st.bar_chart) so
    the x-axis stays ordinal/categorical (one evenly-spaced column per month) instead
    of a continuous time scale, which made bars render as thin, far-apart spikes.
    Data labels sit above each bar (a separate text layer, not the bar's own).

    `sort` is an explicit list (the dataframe's own row order, already chronological)
    — `"sort": None` was supposed to preserve data order but didn't reliably (reproduced
    with periods over ~12 months: newer months rendered before older ones instead of
    a single ascending sequence). Pinning the exact order removes the ambiguity instead
    of relying on Vega's "no sort" semantics. `key` is content-derived too, so Streamlit
    remounts the chart on a new period rather than patching the previous instance."""
    category_order = data[category].tolist()
    chart_key = f"evolution-{len(data)}-{data[category].iloc[0]}-{data[category].iloc[-1]}" if len(data) else "evolution-empty"
    spec = {
        "width": "container",
        "height": height,
        "data": {"values": data.to_dict(orient="records")},
        "encoding": {
            "x": {"field": category, "type": "ordinal", "sort": category_order, "title": "Mês", "axis": {"labelAngle": -45}},
            "y": {"field": value, "type": "quantitative", "title": "Colaboradores"},
        },
        "layer": [
            {
                "mark": {"type": "bar", "color": BRAND_COLOR, "cornerRadiusTopLeft": 4, "cornerRadiusTopRight": 4},
                "encoding": {"tooltip": [{"field": category, "title": "Mês"}, {"field": value, "title": "Colaboradores"}]},
            },
            {
                "mark": {"type": "text", "dy": -10, "fontSize": 14, "fontWeight": "700", "color": "#232323"},
                "encoding": {"text": {"field": label, "type": "nominal"}},
            },
        ],
        "config": {"view": {"stroke": None, "fill": "#FFFFFF"}},
    }
    st.vega_lite_chart(spec, width="stretch", height=height, theme=None, key=chart_key)


@st.cache_resource
def _build_logo_wordmark(icon_path: str, text: str) -> "Image.Image | None":
    """Icon + wordmark side by side, baked into one image — st.logo() has a single
    fixed slot per image and won't let us place separate text next to it."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        icon = Image.open(icon_path).convert("RGBA")
        target_h = 64
        icon = icon.resize((int(icon.width * target_h / icon.height), target_h))

        font = None
        for font_path in (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"):
            if Path(font_path).exists():
                font = ImageFont.truetype(font_path, 40)
                break
        if font is None:
            font = ImageFont.load_default()

        draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        text_box = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = text_box[2] - text_box[0], text_box[3] - text_box[1]

        padding = 14
        canvas = Image.new("RGBA", (icon.width + padding + text_w + 4, target_h), (0, 0, 0, 0))
        canvas.paste(icon, (0, 0), icon)
        draw = ImageDraw.Draw(canvas)
        draw.text(
            (icon.width + padding, (target_h - text_h) // 2 - text_box[1]),
            text,
            font=font,
            fill=BRAND_COLOR,
        )
        return canvas
    except Exception as exc:
        print(f"[headcount] Falha ao montar o logo com texto: {exc}")
        return None


def render_bar_chart(
    data: pd.DataFrame, category: str, value: str, x_label: str, height: int, top_n: int | None = None
) -> None:
    """Horizontal bar chart in the brand color — category names stay on the y-axis,
    fully readable, instead of rotated/truncated x-axis labels. `top_n` folds the
    long tail into "Outros" so charts with many categories (ex.: Área) stay legible."""
    plot_data = data
    if top_n and len(data) > top_n:
        top = data.nlargest(top_n, value)
        rest_total = data[value].sum() - top[value].sum()
        outros = pd.DataFrame([{category: "Outros", value: rest_total}])
        plot_data = pd.concat([top, outros], ignore_index=True)

    chart = (
        alt.Chart(plot_data)
        .mark_bar(cornerRadiusTopRight=8, cornerRadiusBottomRight=8, color=BRAND_COLOR)
        .encode(
            y=alt.Y(f"{category}:N", title=None, sort="-x", axis=alt.Axis(labelLimit=260)),
            x=alt.X(f"{value}:Q", title=x_label),
            tooltip=[category, value],
        )
        .properties(height=height)
    )
    st.altair_chart(chart)


# Paleta fixa (não gerada) pra identidade categórica — navy da marca pro maior
# grupo, dourado do accent dos cards de KPI pro segundo, azul mais claro da
# mesma família pro terceiro. "Não informado" sempre cinza neutro, nunca uma
# dessas cores (evita se passar por uma categoria real).
ASSIGNMENT_CATEGORY_COLORS = {
    "Obras": BRAND_COLOR,
    "Corporativo": "#2E86AB",
    "Comercial": "#FAB900",
    "Não informado": "#9CA3AF",
}


def render_pie_chart(data: pd.DataFrame, category: str, value: str, height: int) -> None:
    """Pizza — só faz sentido aqui porque são poucas categorias (3 + eventual
    "Não informado"); com mais fatias viraria ilegível (ver anti-patterns do
    dataviz). Legenda própria em HTML (não a do Vega) — a legenda nativa do
    Vega-Lite não reserva espaço fora da área do gráfico com `width` numérico
    fixo, então ela ficava sobrepondo a pizza em vez de ficar ao lado."""
    total = data[value].sum()
    plot_data = data.assign(**{f"{value}_pct": data[value] / total if total else 0})
    colors = [ASSIGNMENT_CATEGORY_COLORS.get(c, "#9CA3AF") for c in plot_data[category]]
    chart = (
        alt.Chart(plot_data)
        .mark_arc(outerRadius=height / 2 - 10, stroke="#FFFFFF", strokeWidth=2)
        .encode(
            theta=alt.Theta(f"{value}:Q", stack=True),
            color=alt.Color(f"{category}:N", scale=alt.Scale(domain=plot_data[category].tolist(), range=colors), legend=None),
            tooltip=[
                alt.Tooltip(f"{category}:N", title="Categoria"),
                alt.Tooltip(f"{value}:Q", title="Colaboradores"),
                alt.Tooltip(f"{value}_pct:Q", title="Participação", format=".1%"),
            ],
        )
        .properties(width=height, height=height)
    )
    with st.container(horizontal=True, horizontal_alignment="center"):
        st.altair_chart(chart, width="content", theme=None)

    chips = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:6px;margin:0 14px;">'
        f'<span style="width:10px;height:10px;border-radius:50%;background:{ASSIGNMENT_CATEGORY_COLORS.get(cat, "#9CA3AF")};'
        f'display:inline-block;flex:none;"></span>'
        f'<span style="color:#374151;font-size:13px;">{cat} · {format_number(int(headcount))} ({pct:.0%})</span>'
        f"</span>"
        for cat, headcount, pct in zip(plot_data[category], plot_data[value], plot_data[f"{value}_pct"])
    )
    st.markdown(f'<div style="text-align:center;margin-top:10px;">{chips}</div>', unsafe_allow_html=True)


def _hex_lerp(color_a: str, color_b: str, t: float) -> str:
    rgb_a = tuple(int(color_a[i : i + 2], 16) for i in (1, 3, 5))
    rgb_b = tuple(int(color_b[i : i + 2], 16) for i in (1, 3, 5))
    mixed = (round(a + (b - a) * t) for a, b in zip(rgb_a, rgb_b))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def render_city_map(data: pd.DataFrame, value: str, height: int) -> None:
    """Bolha por cidade do posto de trabalho — tamanho e cor pelo headcount.
    `st.map` (Carto, já embutido no Streamlit/pydeck, sem lib nova) resolveu
    melhor que o geoshape "achatado" do Vega/Plotly, que não parecia mapa de
    verdade (feedback do usuário em 2026-09-14). Antes era por estado (bolha na
    capital); agora usa a cidade real do posto de trabalho — ver
    etl/mapping.py:resolve_city e etl/local_mapping.json."""
    plot_data = data.dropna(subset=["city_lat", "city_lon"])
    if plot_data.empty:
        st.caption("Sem cidade identificada pro posto de trabalho neste recorte.")
        return

    vmin, vmax = plot_data[value].min(), plot_data[value].max()
    span = (vmax - vmin) or 1
    plot_data = plot_data.assign(
        color=plot_data[value].apply(lambda v: _hex_lerp("#a9c6d6", BRAND_COLOR, (v - vmin) / span)),
        # Raio em metros ~ raiz do headcount, pra São Paulo não esmagar visualmente as cidades menores.
        radius=15_000 + (plot_data[value] ** 0.5) * 2_200,
    )

    st.map(plot_data, latitude="city_lat", longitude="city_lon", size="radius", color="color", height=height)


all_data, source_name = load_headcount_data()
all_data["snapshot_date"] = pd.to_datetime(all_data["snapshot_date"]).dt.date
# Presidência e Conselho Administrativo são duas diretorias reais no cc_mapping,
# mas o painel público as exibe juntas — pedido do usuário em 2026-09-14.
all_data["diretoria"] = all_data["diretoria"].replace(
    {"Presidencia": "Presidencia e Conselho Administrativo", "Conselho Administrativo": "Presidencia e Conselho Administrativo"}
)
min_date = all_data["snapshot_date"].min()
max_date = all_data["snapshot_date"].max()
obras_ativas, lojas_ativas = load_extra_metrics()

# Todo o histórico (não só o snapshot atual) tem quebra real por diretoria/área/
# estado/cargo — reconstruída pessoa a pessoa a partir do mirror interno no Neon
# (ver etl/backfill_from_neon_history.py). HISTORICAL_TOTAL_SENTINEL só continua
# aqui como proteção defensiva caso uma carga antiga/manual volte a gravar esse
# sentinel. "Não informado" fica de fora da composição pública: são CCs ainda sem
# diretoria/área mapeada em cc_mapping.json, não uma diretoria real da empresa.
breakdown_data = all_data[
    (all_data["diretoria"] != HISTORICAL_TOTAL_SENTINEL) & (all_data["diretoria"] != "Não informado")
]

diretoria_options = _ordered_options(breakdown_data["diretoria"])
area_options = _ordered_options(breakdown_data["area"])
state_options = _ordered_options(breakdown_data["state"])
job_function_options = _ordered_options(breakdown_data["job_function"])
assignment_category_options = _ordered_options(breakdown_data["assignment_category"])

DEFAULT_PERIOD_MONTHS = 6

# st.logo vive num slot fixo no topo do app/sidebar (acima de qualquer outro
# conteúdo) e, com `icon_image`, continua aparecendo no canto superior esquerdo
# mesmo com a sidebar fechada — diferente de um st.image dentro do `with st.sidebar`.
# `image` leva o ícone + "Headcount" compostos numa imagem só (só o ícone pro
# estado fechado, em `icon_image`), porque st.logo não aceita texto ao lado.
if ICON_PATH.exists():
    wordmark = _build_logo_wordmark(str(ICON_PATH), "Headcount")
    st.logo(wordmark if wordmark is not None else str(ICON_PATH), icon_image=str(ICON_PATH), size="large")

auth.render_sidebar_account()

with st.sidebar:
    st.caption(
        "Consulta pública de headcount da Pacaembu. Veja quantas pessoas trabalham "
        "na empresa hoje e como esse número mudou ao longo do tempo."
    )

    st.markdown("## Explorar dados")
    default_start = max(min_date, (pd.Timestamp(max_date) - pd.DateOffset(months=DEFAULT_PERIOD_MONTHS)).date())
    date_range = st.date_input(
        "Data ou período",
        value=(default_start, max_date),
        min_value=min_date,
        max_value=max_date,
        format="DD/MM/YYYY",
    )
    selected_diretorias = st.multiselect(
        "Diretoria", options=diretoria_options, default=[], placeholder="Todas"
    )
    selected_areas = st.multiselect(
        "Área", options=area_options, default=[], placeholder="Todos"
    )
    selected_states = st.multiselect(
        "Estado", options=state_options, default=[], placeholder="Todos"
    )
    selected_job_functions = st.multiselect(
        "Nível de cargo", options=job_function_options, default=[], placeholder="Todos"
    )
    selected_assignment_categories = st.multiselect(
        "Categoria de Atribuição", options=assignment_category_options, default=[], placeholder="Todas"
    )
    st.caption("Fonte: Neon + Databricks" if source_name == "Neon" else f"Fonte: {source_name}")

# Enquanto o usuário só escolheu a data de início do intervalo (antes de clicar
# a segunda data no calendário), o widget devolve uma tupla de 1 elemento — não
# 2 nem uma date solta. Sem esse caso, `start_date`/`end_date` viravam a tupla
# inteira e o `.between()` mais abaixo quebrava com TypeError.
if isinstance(date_range, tuple):
    if len(date_range) == 2:
        start_date, end_date = date_range
    elif len(date_range) == 1:
        start_date = end_date = date_range[0]
    else:
        start_date, end_date = min_date, max_date
else:
    start_date = end_date = date_range

# Nada selecionado num filtro = todas as opções valem (não "nenhuma").
effective_diretorias = selected_diretorias or diretoria_options
effective_areas = selected_areas or area_options
effective_states = selected_states or state_options
effective_job_functions = selected_job_functions or job_function_options
effective_assignment_categories = selected_assignment_categories or assignment_category_options

# Tendência e total: todo o histórico já tem quebra real por diretoria/área/
# estado/cargo (reconstruída pessoa a pessoa — ver etl/backfill_from_neon_history.py),
# então os filtros da barra lateral valem aqui também, não só na composição do
# último snapshot. Filtro vazio ("Todas") continua contando todo mundo, inclusive
# quem está em "Não informado" — só exclui esse grupo quando o usuário escolhe
# uma diretoria/área/estado/cargo específico.
def _dimension_mask(column: pd.Series, selected: list[str]) -> pd.Series:
    if not selected:
        return pd.Series(True, index=column.index)
    return column.isin(selected)


company_filter_mask = (
    _dimension_mask(all_data["diretoria"], selected_diretorias)
    & _dimension_mask(all_data["area"], selected_areas)
    & _dimension_mask(all_data["state"], selected_states)
    & _dimension_mask(all_data["job_function"], selected_job_functions)
    & _dimension_mask(all_data["assignment_category"], selected_assignment_categories)
)
def _one_snapshot_per_month(daily: pd.DataFrame) -> pd.DataFrame:
    """Colapsa pra 1 linha por mês (a do snapshot mais recente) — o job semanal
    grava mais de um snapshot dentro do mesmo mês corrente (ex.: 10/09 e 13/09),
    e sem isso os dois caíam na mesma coluna "Set/26" do gráfico de evolução,
    duplicando a barra e bagunçando o cálculo de crescimento mês a mês."""
    if daily.empty:
        return daily
    month_key = daily["snapshot_date"].apply(lambda d: (d.year, d.month))
    latest_idx = daily.groupby(month_key)["snapshot_date"].idxmax()
    return daily.loc[latest_idx].sort_values("snapshot_date")


company_monthly = _one_snapshot_per_month(
    all_data[company_filter_mask & all_data["snapshot_date"].between(start_date, end_date)]
    .groupby("snapshot_date", as_index=False)["headcount"]
    .sum()
)
company_monthly_all = _one_snapshot_per_month(
    all_data[company_filter_mask]
    .groupby("snapshot_date", as_index=False)["headcount"]
    .sum()
)

# Composição: só o(s) snapshot(s) mais recente(s) têm quebra por dimensão.
breakdown_filtered = breakdown_data[
    breakdown_data["diretoria"].isin(effective_diretorias)
    & breakdown_data["area"].isin(effective_areas)
    & breakdown_data["state"].isin(effective_states)
    & breakdown_data["job_function"].isin(effective_job_functions)
    & breakdown_data["assignment_category"].isin(effective_assignment_categories)
]
breakdown_in_range = breakdown_filtered[breakdown_filtered["snapshot_date"].between(start_date, end_date)]

with st.container(horizontal=True, vertical_alignment="center"):
    st.markdown("### Headcount Pacaembu")
    st.badge("Dados públicos", icon=":material/public:", color="green")
    st.badge(f"Atualizado em: {max_date.strftime('%d/%m/%Y')}", icon="🔄", color="blue")

st.caption(
    f"Período consultado: {start_date.strftime('%d/%m/%Y')} a {end_date.strftime('%d/%m/%Y')} · "
    "contagem de colaboradores por snapshot mensal"
)

if company_monthly.empty:
    st.warning("Não há dados para o período selecionado.")
    st.stop()

latest_company_date = company_monthly["snapshot_date"].max()
latest_total = int(company_monthly.loc[company_monthly["snapshot_date"] == latest_company_date, "headcount"].iloc[0])

if len(company_monthly_all) >= 2:
    month_delta = int(company_monthly_all["headcount"].iloc[-1] - company_monthly_all["headcount"].iloc[-2])
else:
    month_delta = None
if month_delta is None:
    delta_text, delta_color = "Sem mês anterior", "#6B7280"
elif month_delta > 0:
    delta_text, delta_color = f"+ {month_delta} vs mês ant.", "#22C55E"
elif month_delta < 0:
    delta_text, delta_color = f"− {abs(month_delta)} vs mês ant.", "#F02727"
else:
    delta_text, delta_color = "Estável vs mês ant.", "#6B7280"

first_total_period = int(company_monthly["headcount"].iloc[0])
period_growth = ((latest_total / first_total_period) - 1) * 100 if first_total_period else None
period_growth_color = "#22C55E" if (period_growth or 0) > 0 else "#F02727" if (period_growth or 0) < 0 else "#1F2937"

# Estados Ativos usa o snapshot mais recente dentro do filtro atual — cai pra 0
# se o período escolhido não incluir nenhum snapshot com quebra por dimensão
# (ver "breakdown_in_range" mais abaixo).
if not breakdown_in_range.empty:
    _latest_breakdown_for_kpis = breakdown_in_range[
        breakdown_in_range["snapshot_date"] == breakdown_in_range["snapshot_date"].max()
    ]
    estados_ativos = _latest_breakdown_for_kpis["state"].nunique()
else:
    estados_ativos = 0

kpi_cols = st.columns(5)
with kpi_cols[0]:
    render_kpi_card("Headcount ativo", format_number(latest_total), delta_text, delta_color)
with kpi_cols[1]:
    render_kpi_card(
        "Crescimento no período", format_growth(period_growth), "No período selecionado", value_color=period_growth_color
    )
with kpi_cols[2]:
    render_kpi_card("Estados ativos", str(estados_ativos), "Estados no filtro atual")
with kpi_cols[3]:
    render_kpi_card("Obras ativas", str(obras_ativas) if obras_ativas is not None else "—", "Total da empresa")
with kpi_cols[4]:
    render_kpi_card("Lojas ativas", str(lojas_ativas) if lojas_ativas is not None else "—", "Total da empresa")

with st.container(border=True, key="card-evolution"):
    st.subheader("Como o headcount evoluiu")
    # Rótulo categórico (não a data crua) — senão o eixo vira uma escala temporal
    # contínua de ~2 anos e as colunas mensais aparecem finíssimas, bem espaçadas.
    chart_data = pd.DataFrame({
        "mes": company_monthly["snapshot_date"].apply(lambda d: f"{MESES_ABREV[d.month]}/{d.year % 100:02d}"),
        "headcount": company_monthly["headcount"].astype(int),
    })
    chart_data["headcount_label"] = chart_data["headcount"].apply(format_number)
    render_evolution_chart(chart_data, "mes", "headcount", "headcount_label", height=320)

if breakdown_in_range.empty:
    st.info(
        "Sem quebra por diretoria, centro de custo, estado ou cargo no período selecionado — "
        "essa composição só existe a partir do snapshot semanal mais recente."
    )
else:
    latest_breakdown_date = breakdown_in_range["snapshot_date"].max()
    latest = breakdown_in_range[breakdown_in_range["snapshot_date"] == latest_breakdown_date]
    by_diretoria = (
        latest.groupby("diretoria", as_index=False)["headcount"]
        .sum()
        .sort_values("headcount", ascending=False)
    )
    by_area = (
        latest.groupby("area", as_index=False)["headcount"]
        .sum()
        .sort_values("headcount", ascending=False)
    )
    by_city = (
        latest[latest["city"] != "Não informado"]
        .groupby("city", as_index=False)
        .agg(headcount=("headcount", "sum"), city_lat=("city_lat", "first"), city_lon=("city_lon", "first"))
        .sort_values("headcount", ascending=False)
    )
    by_job_function = (
        latest.groupby("job_function", as_index=False)["headcount"]
        .sum()
        .sort_values("headcount", ascending=False)
    )
    by_assignment_category = (
        latest.groupby("assignment_category", as_index=False)["headcount"]
        .sum()
        .sort_values("headcount", ascending=False)
    )
    left, right = st.columns(2)
    with left:
        with st.container(border=True, key="card-diretoria"):
            st.subheader("Alocação por diretoria")
            render_bar_chart(by_diretoria, "diretoria", "headcount", "Colaboradores", height=380)
    with right:
        with st.container(border=True, key="card-area"):
            st.subheader("Área")
            st.caption("Top 10 — o resto entra em \"Outros\".")
            render_bar_chart(by_area, "area", "headcount", "Colaboradores", height=380, top_n=10)

    left, right = st.columns(2)
    with left:
        with st.container(border=True, key="card-state"):
            st.subheader("Dispersão da Força de Trabalho")
            st.caption("Bolha na cidade do posto de trabalho — tamanho e cor = colaboradores.")
            render_city_map(by_city, "headcount", height=320)
    with right:
        with st.container(border=True, key="card-job-level"):
            st.subheader("Nível de cargo")
            render_bar_chart(by_job_function, "job_function", "headcount", "Colaboradores", height=280)

    with st.container(border=True, key="card-assignment-category"):
        st.subheader("Distribuição por Categoria de Atribuição")
        render_pie_chart(by_assignment_category, "assignment_category", "headcount", height=280)

    st.subheader("Leitura do último snapshot")
    summary = by_diretoria.rename(columns={"diretoria": "Diretoria", "headcount": "Colaboradores"})
    summary["Participação"] = summary["Colaboradores"] / summary["Colaboradores"].sum()
    st.caption("Clique numa diretoria pra ver a participação das áreas dentro dela.")
    selection = st.dataframe(
        summary,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Participação": st.column_config.ProgressColumn(
                "Participação", format="percent", min_value=0, max_value=1
            ),
        },
    )

    selected_rows = selection["selection"]["rows"] if selection else []
    if selected_rows:
        selected_diretoria = summary.iloc[selected_rows[0]]["Diretoria"]
        st.markdown(f"**Áreas em {selected_diretoria}**")
        detail = (
            latest[latest["diretoria"] == selected_diretoria]
            .groupby("area", as_index=False)["headcount"]
            .sum()
            .sort_values("headcount", ascending=False)
        )
        detail_table = detail.rename(columns={"area": "Área", "headcount": "Colaboradores"})
        detail_table["Participação"] = detail_table["Colaboradores"] / detail_table["Colaboradores"].sum()
        st.dataframe(
            detail_table,
            hide_index=True,
            width="stretch",
            column_config={
                "Participação": st.column_config.ProgressColumn(
                    "Participação", format="percent", min_value=0, max_value=1
                ),
            },
        )

    csv_data = summary.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Baixar resumo agregado",
        data=csv_data,
        file_name="headcount_publico_resumo.csv",
        mime="text/csv",
    )

with st.expander("Sobre este painel"):
    st.write(
        "Este é um guia público de headcount. Os números representam colaboradores "
        "agregados por snapshot, diretoria, centro de custo, estado e nível de cargo."
    )
    st.write(
        "A tendência histórica (antes do primeiro snapshot semanal) foi reconstruída a partir "
        "das datas de admissão e desligamento, então mostra só o total da empresa — sem quebra "
        "por diretoria, centro de custo ou estado, porque não temos onde cada pessoa estava "
        "alocada no passado, só a posição atual."
    )
    st.caption(f"Fonte atual: {source_name}")
