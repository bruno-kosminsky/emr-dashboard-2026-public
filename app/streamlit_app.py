"""
Dashboard v2 — protótipo das abas "Agora" + "Evolução".

Roda em paralelo ao streamlit_app.py, usando o mesmo parquet. Sem login (acesso aberto).
Foco: responder em 30s "onde a turma está hoje e pra onde está indo".
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import psycopg2
from dotenv import load_dotenv
from plotly.subplots import make_subplots
import streamlit as st

# Carrega .env do diretório do dashboard (mesma origem do ETL).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "snapshots"

# Ponte st.secrets → os.environ: no Streamlit Cloud não há arquivo .env, as
# credenciais do Aurora vivem em st.secrets. Sem isso, os.getenv(...) volta None
# em produção e todas as queries diretas ao Aurora falham. Local (com .env) tem
# prioridade; o secret só preenche chaves ainda ausentes.
try:
    for _k in ("EMR_AURORA_HOST", "EMR_AURORA_PORT", "EMR_AURORA_USER", "EMR_AURORA_PASSWORD"):
        if not os.getenv(_k) and _k in st.secrets:
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    # st.secrets ausente/sem arquivo de secrets → segue com .env/os.environ.
    pass

st.set_page_config(page_title="Dashboard EMR · R1 2026", layout="wide", initial_sidebar_state="collapsed")

st.markdown(
    """
    <style>
      /* === EMR Design System v2026 (Residente) ===
         Paleta: #6CE190 Residente Green · #B4F900 Residente Lime ·
                 #F8F8F8 Off White · #FF514D Residente Orange.
         Tipografia: Outfit (única). */
      @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap');

      :root {
        --emr-green:        #6CE190;
        --emr-lime:         #B4F900;
        --emr-off-white:    #F8F8F8;
        --emr-orange:       #FF514D;
        --emr-dark:         #0F1F1A;
        --emr-dark-soft:    #1A2D26;
        --emr-text:         #0A1A14;
        --emr-text-muted:   #5A6B63;
        --emr-line:         #E5EBE7;
        --emr-card-bg:      #FFFFFF;
        --emr-shadow:       0 4px 24px rgba(15, 31, 26, .06);
        --emr-shadow-hover: 0 8px 32px rgba(15, 31, 26, .10);
        --emr-radius:       16px;
        --emr-radius-sm:    10px;
        --emr-gradient:     linear-gradient(135deg, #6CE190 0%, #B4F900 100%);
      }

      /* Background global */
      html, body, .stApp {background: var(--emr-off-white) !important;}

      /* Outfit em tudo */
      html, body, [class*="css"], .stMarkdown, .stCaption, .stRadio,
      .stButton, .stSelectbox, .stMultiSelect, .stDataFrame, .stTabs,
      .stExpander, h1, h2, h3, h4, h5, h6, p, span, div, label, input, textarea {
        font-family: 'Outfit', -apple-system, BlinkMacSystemFont, sans-serif !important;
      }

      h1, h2, h3, h4, h5 {color: var(--emr-dark) !important; letter-spacing: -0.01em;}
      h3 {font-weight: 700; font-size: 26px;}
      h4 {font-weight: 600; font-size: 20px;}
      h5 {font-weight: 600; font-size: 17px; margin-top: 32px !important; margin-bottom: 8px !important;}

      p, span, div {color: var(--emr-text);}
      .stMarkdown p, .stCaption {color: var(--emr-text-muted); font-size: 13.5px; line-height: 1.6;}

      .block-container {padding-top: 1.5rem; padding-bottom: 3rem; max-width: 1480px;}

      /* === Hero card === */
      .hero-card {
        background: var(--emr-card-bg);
        border: 1px solid var(--emr-line);
        border-radius: var(--emr-radius);
        padding: 22px 24px;
        height: 100%;
        box-shadow: var(--emr-shadow);
        transition: box-shadow .2s ease, transform .15s ease;
      }
      .hero-card:hover {box-shadow: var(--emr-shadow-hover); transform: translateY(-1px);}
      .hero-card .lbl {
        font-size: 11px; text-transform: uppercase;
        letter-spacing: 1.2px; color: var(--emr-text-muted);
        font-weight: 600;
      }
      .hero-card .val {
        font-size: 44px; font-weight: 700; line-height: 1.05;
        color: var(--emr-dark); font-variant-numeric: tabular-nums;
        margin-top: 6px; letter-spacing: -0.025em;
      }
      .hero-card .sub {font-size: 12.5px; color: var(--emr-text-muted); margin-top: 8px; line-height: 1.45;}
      .hero-card .delta-up {color: #1B9F4F; font-weight: 600;}
      .hero-card .delta-dn {color: var(--emr-orange); font-weight: 600;}
      .hero-card .delta-flat {color: var(--emr-text-muted);}
      .hero-card .pill {
        display: inline-block; width: 8px; height: 8px; border-radius: 50%;
        margin-right: 8px; vertical-align: middle;
      }

      /* === Tabela densa === */
      .gap-table {width: 100%; border-collapse: separate; border-spacing: 0;
        background: var(--emr-card-bg); border-radius: var(--emr-radius-sm);
        overflow: hidden; box-shadow: var(--emr-shadow);
      }
      .gap-table td, .gap-table th {padding: 12px 14px; font-size: 13px;}
      .gap-table th {
        background: linear-gradient(180deg, #FBFCFB 0%, #F4F7F5 100%);
        text-align: left; color: var(--emr-text); font-weight: 600;
        text-transform: uppercase; letter-spacing: .4px; font-size: 10.5px;
        border-bottom: 1px solid var(--emr-line);
      }
      .gap-table td {
        border-bottom: 1px solid var(--emr-line);
        font-variant-numeric: tabular-nums; color: var(--emr-text);
      }
      .gap-table tbody tr:last-child td {border-bottom: none;}
      .gap-table tbody tr:hover {background: rgba(108, 225, 144, .04);}
      .gap-table td.dim {font-weight: 600; color: var(--emr-dark);}
      .gap-table td.val {text-align: right;}
      .gap-table td.status {text-align: center; font-weight: 600;}
      table {font-variant-numeric: tabular-nums;}

      /* === Tabs === */
      .stTabs [data-baseweb="tab-list"] {gap: 8px; border-bottom: 1px solid var(--emr-line);
        background: transparent; padding: 0; margin-bottom: 24px;
      }
      .stTabs [data-baseweb="tab"] {
        background: transparent !important; color: var(--emr-text-muted) !important;
        padding: 10px 18px !important; border-radius: 0 !important;
        font-weight: 500 !important; font-size: 14px !important;
        transition: color .15s;
      }
      .stTabs [data-baseweb="tab"]:hover {color: var(--emr-dark) !important;}
      .stTabs [aria-selected="true"] {color: var(--emr-dark) !important; font-weight: 700 !important;}
      .stTabs [data-baseweb="tab-highlight"] {background: var(--emr-gradient) !important; height: 3px !important; border-radius: 2px;}

      /* === Banners (st.info, st.warning, st.caption no header) === */
      [data-testid="stAlert"] {border-radius: var(--emr-radius-sm) !important;
        border: 1px solid var(--emr-line) !important; background: var(--emr-card-bg) !important;
      }
      [data-testid="stAlert"][kind="info"] {border-left: 4px solid var(--emr-green) !important;}
      [data-testid="stAlert"][kind="warning"] {border-left: 4px solid #F5B800 !important;}

      /* === Multiselect / inputs === */
      [data-baseweb="select"] > div {
        border-radius: 10px !important; border-color: var(--emr-line) !important;
        background: var(--emr-card-bg) !important;
      }
      [data-baseweb="tag"] {background: var(--emr-gradient) !important; color: var(--emr-dark) !important;
        border-radius: 6px !important; font-weight: 600 !important;
      }

      /* === Header global EMR (banner gradient) === */
      .emr-hero {
        background: var(--emr-gradient);
        border-radius: var(--emr-radius);
        padding: 28px 36px;
        margin-bottom: 24px;
        box-shadow: var(--emr-shadow);
      }
      .emr-hero h3 {color: var(--emr-dark) !important; margin: 0 0 6px 0 !important;
        font-weight: 700; font-size: 30px; letter-spacing: -0.02em;
      }
      .emr-hero p {color: var(--emr-dark) !important; margin: 0; font-size: 14px; opacity: 0.85;}

      /* Esconde header e footer padrão do Streamlit pra visual limpo */
      header[data-testid="stHeader"] {background: transparent; height: 0;}
      footer {visibility: hidden;}
      #MainMenu {visibility: hidden;}

    </style>
    """,
    unsafe_allow_html=True,
)

# ============================================================
# CONSTANTES — canais de acerto canônico (faixas) e prescrição volumétrica
# ============================================================

# Canal de acerto canônico — v4 editorial (mesmo canal v3; v4 mantém intacto):
# - Excelência: ramp 55→75% (±3pp), slope slow Q1 (+1pp/m) · medium Q2 (+2pp/m) · fast Q3 (+4pp/m).
# - Proficiência: ramp 45→65% (±3pp), mesma curva deslocada -10pp. Tier não-contíguo
#   (gap de 4pp entre prof_max e excel_min cai em Proficiência via classify()).
# Fonte: prescricao_excelencia_v4.md + prescricao_proficiencia_v4.md (3 cenários).
PRESCRIPTION_MONTHLY = {
    "2026-01": {"label": "jan/26", "prof_min": 42, "prof_max": 48, "excel_min": 52, "excel_max": 58, "source": "v3_editorial"},
    "2026-02": {"label": "fev/26", "prof_min": 43, "prof_max": 49, "excel_min": 53, "excel_max": 59, "source": "v3_editorial"},
    "2026-03": {"label": "mar/26", "prof_min": 44, "prof_max": 50, "excel_min": 54, "excel_max": 60, "source": "v3_editorial"},
    "2026-04": {"label": "abr/26", "prof_min": 46, "prof_max": 52, "excel_min": 56, "excel_max": 62, "source": "v3_editorial"},
    "2026-05": {"label": "mai/26", "prof_min": 48, "prof_max": 54, "excel_min": 58, "excel_max": 64, "source": "v3_editorial"},
    "2026-06": {"label": "jun/26", "prof_min": 50, "prof_max": 56, "excel_min": 60, "excel_max": 66, "source": "v3_editorial"},
    "2026-07": {"label": "jul/26", "prof_min": 54, "prof_max": 60, "excel_min": 64, "excel_max": 70, "source": "v3_editorial"},
    "2026-08": {"label": "ago/26", "prof_min": 58, "prof_max": 64, "excel_min": 68, "excel_max": 74, "source": "v3_editorial"},
    "2026-09": {"label": "set/26", "prof_min": 62, "prof_max": 68, "excel_min": 72, "excel_max": 78, "source": "v3_editorial"},
}
PRESCRIPTION_ORDER = list(PRESCRIPTION_MONTHLY.keys())

# Target volumétrico mensal v4 (proficiência=LEVE, excelência=PADRÃO):
# - Excelência (PADRÃO, P70 do tier ENAMED ≥80): 7.630q/ano · 2,5h/dia.
# - Proficiência (LEVE, sem Inteligente + lag reduzido): 5.920q/ano · 2,1h/dia.
# Blocos: 800/ano (~100/mês; jan/set meio-mês). Flashcards: 930/ano (~120/mês).
# short_fmt = Fixação + Revisão mensais.
# Fonte: prescricao_excelencia_v4.md + prescricao_proficiencia_v4.md.
PRESCRIPTION_TARGETS = {
    "2026-01": {"questoes": (300, 300),   "flashcards": (60, 60),   "blocos": (50, 50),   "dias_ativos": (9, 9),   "short_fmt": (8, 8)},
    "2026-02": {"questoes": (540, 640),   "flashcards": (120, 120), "blocos": (100, 100), "dias_ativos": (14, 19), "short_fmt": (16, 16)},
    "2026-03": {"questoes": (680, 880),   "flashcards": (120, 120), "blocos": (100, 100), "dias_ativos": (15, 19), "short_fmt": (16, 16)},
    "2026-04": {"questoes": (680, 920),   "flashcards": (120, 120), "blocos": (100, 100), "dias_ativos": (16, 20), "short_fmt": (16, 16)},
    "2026-05": {"questoes": (780, 1060),  "flashcards": (120, 120), "blocos": (100, 100), "dias_ativos": (17, 20), "short_fmt": (16, 16)},
    "2026-06": {"questoes": (880, 1160),  "flashcards": (120, 120), "blocos": (100, 100), "dias_ativos": (18, 21), "short_fmt": (16, 16)},
    "2026-07": {"questoes": (920, 1200),  "flashcards": (120, 120), "blocos": (100, 100), "dias_ativos": (19, 21), "short_fmt": (16, 16)},
    "2026-08": {"questoes": (960, 1240),  "flashcards": (120, 120), "blocos": (100, 100), "dias_ativos": (20, 22), "short_fmt": (16, 16)},
    "2026-09": {"questoes": (180, 230),   "flashcards": (30, 30),   "blocos": (50, 50),   "dias_ativos": (12, 22), "short_fmt": (4, 4)},
}

DIMENSIONS = [
    ("questoes",     "Questões / mês"),
    ("flashcards",   "Flashcards / mês"),
    ("blocos",       "Blocos de aula / mês"),
    ("short_fmt",    "Short-fmt (FIXATION + REVISION) / mês"),
    ("dias_ativos",  "Dias ativos / mês"),
]

MONTH_LABELS_PT = {
    1: "jan", 2: "fev", 3: "mar", 4: "abr", 5: "mai", 6: "jun",
    7: "jul", 8: "ago", 9: "set", 10: "out", 11: "nov", 12: "dez",
}


def _format_mes(yyyymm: str) -> str:
    """Formata 'YYYY-MM' como 'mes/AA'. Fallback robusto pra meses fora do PRESCRIPTION_MONTHLY."""
    if yyyymm in PRESCRIPTION_MONTHLY:
        return PRESCRIPTION_MONTHLY[yyyymm]["label"]
    try:
        y, m = yyyymm.split("-")
        return f"{MONTH_LABELS_PT[int(m)]}/{y[-2:]}"
    except Exception:
        return yyyymm


def assign_safra(first_start: pd.Series) -> pd.Series:
    """Atribui safra de matrícula como 'YYYY-MM'.

    Veteranos pré-2026 e matrículas de jan/26 são agrupados em '2026-01' —
    representam quem já estava dentro quando o extensivo começou. As demais
    safras (fev, mar, abr/26...) seguem o mês real de first_start_date.
    """
    fsd = pd.to_datetime(first_start, errors="coerce")
    safra = fsd.dt.to_period("M").astype(str)
    return safra.mask(fsd < pd.Timestamp("2026-02-01"), "2026-01")


PRESCRIPTION_CLASSES = ["Excelência", "Proficiência", "Abaixo do canal", "Sem acerto canônico"]
PRESCRIPTION_COLORS = {
    "Excelência":          "#6CE190",  # Residente Green (v2026)
    "Proficiência":        "#1B9F4F",  # Verde mais escuro pra contraste no mesmo eixo
    "Abaixo do canal":     "#FF514D",  # Residente Orange
    "Sem acerto canônico": "#C9D2CD",  # Cinza neutro v2026
}
PRESCRIPTION_MEANING = {
    "Excelência":          "acertou ≥ alvo Excelência no mock do mês",
    "Proficiência":        "acertou ≥ alvo Proficiência no mock do mês",
    "Abaixo do canal":     "fez mock mas ficou abaixo do alvo",
    "Sem acerto canônico": "não fez mock no mês",
}


# ============================================================
# DADOS
# ============================================================

@st.cache_data(ttl=60 * 60)
def load_snapshot(snap_dir: Path = SNAPSHOTS):
    df = pd.read_parquet(snap_dir / "latest.parquet")
    cohort = pd.read_parquet(snap_dir / "latest_cohort.parquet")
    metrics = pd.read_parquet(snap_dir / "latest_cohort_metrics.parquet")
    df["semana_iso"] = pd.to_datetime(df["semana_iso"])
    for d in (df, cohort, metrics):
        d["account_id"] = d["account_id"].astype(int)
    snap_date = pd.Timestamp(
        (snap_dir / "latest.parquet").resolve().stat().st_mtime, unit="s", tz="UTC"
    ).tz_convert("America/Sao_Paulo")
    return df, cohort, metrics, snap_date


df, cohort, metrics_acum, snapshot_date = load_snapshot()

# Cohort B2C tem 1 linha por (account_id, turma) — alunos em 2+ turmas duplicam.
# `cohort_turma` preserva a coluna `turma` (pra filtros). `cohort` global é
# deduplicado por account_id (mantém a 1ª turma observada, pra compat com
# aggregate_month e demais funções que esperam 1 linha por aluno).
if "turma" in cohort.columns:
    cohort_turma = cohort.copy()
    cohort = cohort.drop_duplicates("account_id", keep="first").drop(columns=["turma"])
else:
    cohort_turma = cohort.assign(turma="Geral")

# Snapshot B2B paralelo (Inspirali, Mandic, FMO, Unisa, FACAPE, FARESI)
SNAPSHOTS_B2B = SNAPSHOTS / "b2b"
try:
    df_b2b, cohort_b2b, metrics_acum_b2b, snapshot_date_b2b = load_snapshot(SNAPSHOTS_B2B)
    HAS_B2B = True
except (FileNotFoundError, OSError):
    df_b2b = cohort_b2b = metrics_acum_b2b = snapshot_date_b2b = None
    HAS_B2B = False


# ============================================================
# CARGA DE AVALIAÇÕES (qualidade de conteúdo) DIRETO DO AURORA
# ============================================================
# Mapeamento dos big_areas (support.big_areas, is_hidden=False):
#   1 → Cirurgia (CG) · 2 → Clínica Médica (CM) · 3 → Pediatria (PED)
#   4 → GO · 5 → Medicina Preventiva (PREV)
BIG_AREAS_ORDER = [("CM", 2), ("CG", 1), ("PED", 3), ("GO", 4), ("PREV", 5)]
BIG_AREAS_NOMES = {1: "Cirurgia", 2: "Clínica Médica", 3: "Pediatria",
                   4: "Ginecologia/Obstetrícia", 5: "Med. Preventiva"}


def _aurora_conn(database: str):
    return psycopg2.connect(
        host=os.getenv("EMR_AURORA_HOST"),
        port=os.getenv("EMR_AURORA_PORT"),
        user=os.getenv("EMR_AURORA_USER"),
        password=os.getenv("EMR_AURORA_PASSWORD"),
        dbname=database,
        connect_timeout=15,
    )


@st.cache_data(ttl=60 * 60 * 24)
def load_specialty_to_big_area() -> dict[int, int]:
    """Mapa specialty_id → big_area_id (só big_areas 1-5 visíveis)."""
    with _aurora_conn("support") as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT id, big_area_id FROM public.specialties "
                "WHERE big_area_id IN (1,2,3,4,5)"
            )
            return {sid: bid for sid, bid in cur.fetchall()}


def _fetch_ratings(database: str, sql: str, account_ids: list[int]) -> pd.DataFrame:
    with _aurora_conn(database) as c:
        with c.cursor() as cur:
            cur.execute(sql, (account_ids,))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    return pd.DataFrame(rows, columns=cols)


@st.cache_data(ttl=60 * 60, show_spinner="Carregando avaliações de aulas…")
def load_avaliacoes_aulas(account_ids: tuple[int, ...]) -> pd.DataFrame:
    """Avaliações de aulas (streaming.lessons_ratings) com big_area derivada.

    Big_area da aula = first big_area_id encontrado nas specialties da aula
    (lesson_specialties). Aulas sem mapeamento ficam de fora.
    """
    ids = list(account_ids)
    # 1) ratings + lesson_id
    rates = _fetch_ratings(
        "streaming",
        """
        SELECT
            DATE_TRUNC('month', created_at)::date AS mes,
            lesson_id,
            rate,
            account_id
        FROM public.lessons_ratings
        WHERE account_id = ANY(%s)
          AND created_at >= '2026-01-01'
          AND created_at <  CURRENT_DATE + INTERVAL '1 day'
        """,
        ids,
    )
    if rates.empty:
        return rates.assign(big_area_id=pd.Series(dtype=int))
    # 2) lesson_id → specialty_id (todas as aulas avaliadas)
    lesson_ids = rates["lesson_id"].unique().tolist()
    ls = _fetch_ratings(
        "streaming",
        "SELECT lesson_id, specialty_id FROM public.lesson_specialties WHERE lesson_id = ANY(%s)",
        lesson_ids,
    )
    # 3) specialty → big_area (do support)
    spec_to_big = load_specialty_to_big_area()
    ls["big_area_id"] = ls["specialty_id"].map(spec_to_big)
    ls = ls.dropna(subset=["big_area_id"])
    # 4) primeira big_area por lesson
    lesson_big = (
        ls.sort_values(["lesson_id", "big_area_id"])
          .drop_duplicates("lesson_id", keep="first")
          [["lesson_id", "big_area_id"]]
    )
    lesson_big["big_area_id"] = lesson_big["big_area_id"].astype(int)
    out = rates.merge(lesson_big, on="lesson_id", how="inner")
    return out[["mes", "big_area_id", "rate", "account_id"]]


@st.cache_data(ttl=60 * 60, show_spinner="Carregando avaliações de questões…")
def load_avaliacoes_questoes(account_ids: tuple[int, ...]) -> pd.DataFrame:
    """Avaliações de comentários de questões com big_area derivada."""
    ids = list(account_ids)
    rates = _fetch_ratings(
        "assessment",
        """
        SELECT
            DATE_TRUNC('month', created_at)::date AS mes,
            question_id,
            rating AS rate,
            account_id
        FROM public.question_comment_ratings
        WHERE account_id = ANY(%s)
          AND created_at >= '2026-01-01'
          AND created_at <  CURRENT_DATE + INTERVAL '1 day'
        """,
        ids,
    )
    if rates.empty:
        return rates.assign(big_area_id=pd.Series(dtype=int))
    q_ids = rates["question_id"].unique().tolist()
    qs = _fetch_ratings(
        "assessment",
        "SELECT question_id, specialty_id FROM public.question_specialties WHERE question_id = ANY(%s)",
        q_ids,
    )
    spec_to_big = load_specialty_to_big_area()
    qs["big_area_id"] = qs["specialty_id"].map(spec_to_big)
    qs = qs.dropna(subset=["big_area_id"])
    q_big = (
        qs.sort_values(["question_id", "big_area_id"])
          .drop_duplicates("question_id", keep="first")
          [["question_id", "big_area_id"]]
    )
    q_big["big_area_id"] = q_big["big_area_id"].astype(int)
    out = rates.merge(q_big, on="question_id", how="inner")
    return out[["mes", "big_area_id", "rate", "account_id"]]


@st.cache_data(ttl=60 * 60, show_spinner="Carregando avaliações de materiais…")
def load_avaliacoes_materiais(account_ids: tuple[int, ...]) -> pd.DataFrame:
    """Avaliações de materiais (ebook/resumo/mapa mental/suporte) com big_area
    derivada via lesson_id → lesson_specialties → big_area.

    Tipos retornados (coluna `tipo_material`):
      EBOOK · SUMMARY · MIND_MAP · SUPPORTING_MATERIAL.
    """
    ids = list(account_ids)
    rates = _fetch_ratings(
        "streaming",
        """
        SELECT
            DATE_TRUNC('month', lfr.created_at)::date AS mes,
            lf.lesson_id,
            lf.type::text AS tipo_material,
            lfr.rate,
            lfr.account_id
        FROM public.lesson_file_rates lfr
        JOIN public.lesson_files lf ON lf.id = lfr.lesson_file_id
        WHERE lfr.account_id = ANY(%s)
          AND lfr.created_at >= '2026-01-01'
          AND lfr.created_at <  CURRENT_DATE + INTERVAL '1 day'
        """,
        ids,
    )
    if rates.empty:
        return rates.assign(big_area_id=pd.Series(dtype=int))
    lesson_ids = rates["lesson_id"].dropna().unique().tolist()
    if not lesson_ids:
        rates["big_area_id"] = pd.NA
        return rates[["mes", "big_area_id", "rate", "account_id", "tipo_material"]]
    ls = _fetch_ratings(
        "streaming",
        "SELECT lesson_id, specialty_id FROM public.lesson_specialties WHERE lesson_id = ANY(%s)",
        lesson_ids,
    )
    spec_to_big = load_specialty_to_big_area()
    ls["big_area_id"] = ls["specialty_id"].map(spec_to_big)
    ls = ls.dropna(subset=["big_area_id"])
    lesson_big = (
        ls.sort_values(["lesson_id", "big_area_id"])
          .drop_duplicates("lesson_id", keep="first")
          [["lesson_id", "big_area_id"]]
    )
    lesson_big["big_area_id"] = lesson_big["big_area_id"].astype(int)
    out = rates.merge(lesson_big, on="lesson_id", how="left")
    return out[["mes", "big_area_id", "rate", "account_id", "tipo_material"]]


@st.cache_data(ttl=60 * 60, show_spinner="Carregando avaliações de flashcards…")
def load_avaliacoes_flashcards(account_ids: tuple[int, ...]) -> pd.DataFrame:
    """Avaliações de decks de flashcards. Sem big_area (deck mapeia por
    subject_id, mas o mapeamento subject→big_area está fora do escopo)."""
    ids = list(account_ids)
    rates = _fetch_ratings(
        "flashcard",
        """
        SELECT
            DATE_TRUNC('month', created_at)::date AS mes,
            rating AS rate,
            account_id
        FROM public.deck_rating
        WHERE account_id = ANY(%s)
          AND created_at >= '2026-01-01'
          AND created_at <  CURRENT_DATE + INTERVAL '1 day'
        """,
        ids,
    )
    if rates.empty:
        return rates.assign(big_area_id=pd.Series(dtype=int))
    rates["big_area_id"] = pd.NA
    return rates[["mes", "big_area_id", "rate", "account_id"]]


# Simulados ENAMED Inspirali oficiais aplicados em 2026 (sem testes/sandbox).
# Descobertos automaticamente no Aurora por load_enamed_2026_templates() — a lista
# abaixo é apenas o FALLBACK usado se a query de descoberta falhar/voltar vazia
# (mantida em ordem cronológica). Não precisa ser editada quando um novo simulado
# é aplicado: o app passa a exibi-lo sozinho assim que ≥100 alunos o finalizam.
ENAMED_2026_TEMPLATES_FALLBACK: list[tuple[int, str, str]] = [
    (15798, "1º Simulado ENAMED Inspirali 2026", "2026-02-26"),
    (17948, "2º Simulado ENAMED Inspirali 2026", "2026-04-28"),
    (19994, "3º Simulado ENAMED Inspirali 2026", "2026-05-30"),
]

# Mínimo de alunos que finalizaram o simulado para ele contar como "oficial"
# (separa com folga os oficiais — milhares — de testes/sandbox e Rounds).
ENAMED_MIN_ALUNOS = 100


@st.cache_data(ttl=60 * 60, show_spinner="Descobrindo simulados ENAMED 2026…")
def load_enamed_2026_templates() -> list[tuple[int, str, str]]:
    """Descobre no Aurora os simulados ENAMED Inspirali 2026 oficiais.

    Critério: nome casa 'Nº Simulado Enamed - Inspirali 2026', exclui variantes
    de teste (nome contendo 'teste'), e ≥ ENAMED_MIN_ALUNOS alunos finalizados.
    Rótulo = nome real do template (com espaços normalizados); ordem cronológica
    pela 1ª finalização. Em caso de falha/vazio, cai para o fallback hardcoded.

    Retorna lista de (mock_template_id, nome, data_aplicacao_iso).
    """
    sql = """
        SELECT
            m.mock_template_id,
            mt.name,
            MIN(m.finished_at)::date AS data_aplicacao
        FROM public.mocks m
        JOIN public.mock_templates mt ON mt.id = m.mock_template_id
        WHERE m.created_at >= '2026-01-01'
          AND m.finished_at IS NOT NULL
          AND m.deleted_at IS NULL
          AND mt.name ~* '[0-9]+º\\s*Simulado\\s*Enamed\\s*-\\s*Inspirali\\s*2026'
          AND mt.name !~* 'teste'
        GROUP BY m.mock_template_id, mt.name
        HAVING COUNT(DISTINCT m.account_id) >= %s
        ORDER BY MIN(m.finished_at)
    """
    try:
        with _aurora_conn("assessment") as c:
            with c.cursor() as cur:
                cur.execute(sql, (ENAMED_MIN_ALUNOS,))
                rows = cur.fetchall()
        templates = [
            (int(tid), " ".join(str(name).split()), data.isoformat())
            for tid, name, data in rows
        ]
        if templates:
            return templates
    except Exception as exc:  # Aurora indisponível → fallback resiliente
        st.warning(
            f"Não foi possível descobrir os simulados ENAMED no Aurora "
            f"({exc}); usando lista de fallback.",
            icon="⚠️",
        )
    return list(ENAMED_2026_TEMPLATES_FALLBACK)


@st.cache_data(ttl=60 * 60, show_spinner="Carregando ENAMED 2026 do Aurora…")
def load_enamed_2026_results(account_ids: tuple[int, ...]) -> pd.DataFrame:
    """Para cada aluno do cohort, retorna acertos + question_count em cada um dos
    simulados ENAMED 2026 oficiais (descobertos por load_enamed_2026_templates).

    Colunas: account_id, mock_template_id, question_count, acertos, pct.
    """
    ids = list(account_ids)
    template_ids = [tid for tid, _, _ in load_enamed_2026_templates()]
    sql = """
        SELECT
            m.account_id,
            m.mock_template_id,
            m.question_count,
            COUNT(*) FILTER (WHERE alt.is_correct) AS acertos
        FROM public.mocks m
        JOIN public.alternative_answers aa ON aa.mock_id = m.id
        JOIN public.alternatives alt ON alt.id = aa.alternative_id
        WHERE m.mock_template_id = ANY(%s)
          AND m.account_id = ANY(%s)
          AND m.finished_at IS NOT NULL
          AND m.deleted_at IS NULL
        GROUP BY m.id, m.account_id, m.mock_template_id, m.question_count
    """
    with _aurora_conn("assessment") as c:
        with c.cursor() as cur:
            cur.execute(sql, (template_ids, ids))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    out = pd.DataFrame(rows, columns=cols)
    if not out.empty:
        out["pct"] = out["acertos"] / out["question_count"] * 100
    return out


# Mínimo de mock-takers num mês pra confiar nos percentis e recalibrar o canal.
# Abaixo disso, mantém o baseline 2025.
CANAL_MIN_N_MOCK_TAKERS = 100


def recompute_canais_2026(df_w: pd.DataFrame, cohort_df: pd.DataFrame) -> dict[str, dict]:
    """Anota observações empíricas (n_mock_takers, P75/P90 reais) por mês.

    NÃO sobrescreve os valores editoriais v4 (excel_min/excel_max do dict).
    O canal v4 da Excelência é decisão editorial calibrada com cohort R1 2025
    ENAMED ≥80 (ver prescricao_excelencia_v4.md); não é o P75/P90 cru.

    Esta função preserva os percentis empíricos pra instrumentação e validação,
    mas eles não alteram a classificação.
    """
    coh_2026 = cohort_df[["account_id", "first_start_date"]].copy()
    coh_2026["first_start_date"] = pd.to_datetime(coh_2026["first_start_date"])
    coh_2026 = coh_2026.dropna(subset=["first_start_date"])
    coh_ids = coh_2026["account_id"].unique()

    out: dict[str, dict] = {}
    for mes in PRESCRIPTION_ORDER:
        sub = df_w[df_w["semana_iso"].dt.to_period("M").astype(str) == mes]
        sub = sub[sub["account_id"].isin(coh_ids)]
        agg = sub.groupby("account_id", as_index=False).agg(
            acertos=("acerto_canonico_acertos", "sum"),
            qcount=("acerto_canonico_questao_count", "sum"),
        )
        agg = agg[agg["qcount"] > 0]
        n = len(agg)
        if n < CANAL_MIN_N_MOCK_TAKERS:
            continue
        pct = agg["acertos"] / agg["qcount"] * 100
        out[mes] = {
            "n_mock_takers": n,
            "empirical_p75": round(float(pct.quantile(0.75))),
            "empirical_p90": round(float(pct.quantile(0.90))),
        }
    return out


_canais_novos = recompute_canais_2026(df, cohort)
for _mes, _vals in _canais_novos.items():
    PRESCRIPTION_MONTHLY[_mes].update(_vals)


def classify(acerto_canonico_pct: float, mes: str) -> str:
    if pd.isna(acerto_canonico_pct):
        return "Sem acerto canônico"
    t = PRESCRIPTION_MONTHLY[mes]
    if acerto_canonico_pct >= t["excel_min"]:
        return "Excelência"
    if acerto_canonico_pct >= t["prof_min"]:
        return "Proficiência"
    return "Abaixo do canal"


def aggregate_month(df_weekly: pd.DataFrame, mes: str, cohort_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Agrega df semanal para o mês YYYY-MM, retorna um row por aluno do cohort.

    Se `cohort_df` for None, usa o cohort B2C global (padrão).
    """
    coh = cohort if cohort_df is None else cohort_df
    sub = df_weekly[df_weekly["semana_iso"].dt.to_period("M").astype(str) == mes]
    agg = sub.groupby("account_id", as_index=False).agg(
        dias_ativos=("dias_ativos", "sum"),
        questoes=("questoes", "sum"),
        flashcards=("flashcards", "sum"),
        blocos=("blocos", "sum"),
        acerto_canonico_acertos=("acerto_canonico_acertos", "sum"),
        acerto_canonico_questao_count=("acerto_canonico_questao_count", "sum"),
        sim_fixation=("sim_fixation", "sum"),
        sim_revision=("sim_revision", "sum"),
    )
    per = coh[["account_id"]].merge(agg, on="account_id", how="left").fillna(0)
    per["short_fmt"] = per["sim_fixation"] + per["sim_revision"]
    per["acerto_canonico_pct"] = (
        per["acerto_canonico_acertos"] / per["acerto_canonico_questao_count"]
    ).where(per["acerto_canonico_questao_count"] > 0) * 100
    per["faixa"] = per["acerto_canonico_pct"].apply(lambda v: classify(v, mes))
    return per


def faixa_counts(per: pd.DataFrame) -> dict[str, int]:
    counts = per["faixa"].value_counts().to_dict()
    return {f: int(counts.get(f, 0)) for f in PRESCRIPTION_CLASSES}


def closed_months(today: pd.Timestamp, df_w: pd.DataFrame | None = None) -> list[str]:
    """Meses do PRESCRIPTION_ORDER já fechados (anteriores ao mês atual).

    Se `df_w` for None, usa o df B2C global.
    """
    df_ = df if df_w is None else df_w
    cur = today.to_period("M").strftime("%Y-%m")
    return [m for m in PRESCRIPTION_ORDER if m < cur and m in set(
        df_["semana_iso"].dt.to_period("M").astype(str).unique()
    )]


def data_freshness() -> tuple[pd.Timestamp | None, bool]:
    """Retorna (data do último domingo com dados completos, semana parcial descartada?).

    Usa a mesma heurística do Bloco 5: se a última semana presente no df tem volume
    < 30% da média das 3 anteriores, considera incompleta e descarta.
    """
    weekly_vol = df.groupby("semana_iso")["questoes"].sum().sort_index()
    truncated = False
    if len(weekly_vol) >= 4:
        last_vol = weekly_vol.iloc[-1]
        prev_avg = weekly_vol.iloc[-4:-1].mean()
        if prev_avg > 0 and last_vol < prev_avg * 0.3:
            truncated = True
            weekly_vol = weekly_vol.iloc[:-1]
    if weekly_vol.empty:
        return None, False
    # semana_iso é a segunda; domingo = +6 dias
    return weekly_vol.index[-1] + pd.Timedelta(days=6), truncated


# ============================================================
# CABEÇALHO (fora de tabs)
# ============================================================

fresh_date, fresh_truncated = data_freshness()
fresh_txt = f"Dados até {fresh_date:%d/%m/%Y}" if fresh_date else "Dados indisponíveis"
st.markdown(
    f"""
    <div class="emr-hero">
      <h3>Dashboard Ensino e Produto EMR</h3>
    </div>
    """,
    unsafe_allow_html=True,
)

today = pd.Timestamp(snapshot_date.date())
months_closed = closed_months(today)
if not months_closed:
    st.error("Sem mês fechado no snapshot — não dá pra classificar a turma na prescrição.")
    st.stop()

last_closed = months_closed[-1]
prev_closed = months_closed[-2] if len(months_closed) >= 2 else None
total = len(cohort)
cur_mes_label = PRESCRIPTION_MONTHLY[last_closed]["label"]
prev_mes_label = PRESCRIPTION_MONTHLY[prev_closed]["label"] if prev_closed else None
# cur_per global é usado fora da função _render_agora_now (ex.: aba Evolução,
# bloco Métricas avançadas, filtra alunos em Excelência no mês de referência).
cur_per = aggregate_month(df, last_closed)


tab_agora, tab_b2b, tab_evolucao, tab_qualidade = st.tabs(
    ["Visão geral B2C", "Visão geral B2B", "Evolução B2C", "Qualidade B2C"]
)


# ============================================================
# NARRATIVAS DETERMINÍSTICAS (Análise automática por gráfico)
# ============================================================
# Cada função recebe os dados do gráfico e devolve HTML pronto pra renderização
# como um bloco .ia-report. As narrativas são geradas por regras condicionais
# sobre os números — não há chamada a LLM.

def _ia_report(body: str, disclaimer: str | None = None) -> str:
    disc = f'<div class="ia-disclaimer">{disclaimer}</div>' if disclaimer else ""
    return (
        f'<div class="ia-report">'
        f'<div class="ia-header">📊 Análise automática</div>'
        f'{body}{disc}</div>'
    )


def _diag_faixa_dominante(pct: float, faixa: str) -> str:
    if pct >= 50:
        return f"absoluta maioria em <strong>{faixa}</strong>"
    if pct >= 35:
        return f"concentração relevante em <strong>{faixa}</strong>"
    return f"distribuição pulverizada (maior fatia: {faixa} com {pct:.0f}%)"


def narrativa_faixas(cur_counts, prev_counts, total, cur_mes_label, prev_mes_label):
    pct = {f: cur_counts[f] / total * 100 for f in PRESCRIPTION_CLASSES}
    dominante = max(pct, key=pct.get)
    sem_mock = pct["Sem acerto canônico"]
    excel_pct = pct["Excelência"]
    prof_pct = pct["Proficiência"]
    abaixo_pct = pct["Abaixo do canal"]
    engaj_pct = 100 - sem_mock

    if prev_counts is not None:
        prev_excel = prev_counts["Excelência"] / total * 100
        prev_engaj = 100 - prev_counts["Sem acerto canônico"] / total * 100
        d_excel = excel_pct - prev_excel
        d_engaj = engaj_pct - prev_engaj
        if abs(d_excel) < 0.5 and abs(d_engaj) < 0.5:
            tendencia = (
                f"Comparado a {prev_mes_label}, o quadro é praticamente <strong>estável</strong> "
                f"(variação &lt;0,5pp em Excelência e em engajamento)."
            )
        else:
            partes = []
            if abs(d_excel) >= 0.5:
                sinal = "subiu" if d_excel > 0 else "caiu"
                partes.append(f"a fatia em Excelência <strong>{sinal} {abs(d_excel):.1f}pp</strong>")
            if abs(d_engaj) >= 0.5:
                sinal = "cresceu" if d_engaj > 0 else "recuou"
                partes.append(f"o engajamento no mock canônico <strong>{sinal} {abs(d_engaj):.1f}pp</strong>")
            tendencia = f"Em relação a {prev_mes_label}, {' e '.join(partes)}."
    else:
        tendencia = "Não há mês anterior fechado para servir de baseline — a leitura é estática."

    if sem_mock >= 60:
        alerta = (
            f"O <strong>engajamento crítico</strong> ({sem_mock:.0f}% sem mock canônico) "
            "é o sinal mais forte do quadro: a maioria da turma não está sendo "
            "avaliada no canal e qualquer leitura sobre aproveitamento fica enviesada "
            "para os engajados."
        )
    elif sem_mock >= 40:
        alerta = (
            f"O <strong>engajamento moderado</strong> ({sem_mock:.0f}% sem mock canônico) "
            "limita a fração da turma que pode ser plenamente classificada — "
            "interpretar Excel/Profic como % do engajado pode mudar a leitura."
        )
    else:
        alerta = (
            f"O engajamento está saudável (apenas {sem_mock:.0f}% sem mock canônico), "
            "então as fatias Excel/Profic/Abaixo refletem bem a turma como um todo."
        )

    body = (
        f"<p>No mês de <strong>{cur_mes_label}</strong>, a turma apresenta "
        f"{_diag_faixa_dominante(pct[dominante], dominante)}, com "
        f"<strong>{excel_pct:.0f}% em Excelência</strong>, "
        f"<strong>{prof_pct:.0f}% em Proficiência</strong> e "
        f"<strong>{abaixo_pct:.0f}% abaixo do canal</strong> entre os que fizeram mock canônico. "
        f"{tendencia} "
        f"{alerta} "
        f"O foco operacional natural é converter a base de <strong>Proficiência</strong> "
        f"em Excelência (intervenção de qualidade) e resgatar parte dos "
        f"<strong>{sem_mock:.0f}% sem mock</strong> de volta ao canal (intervenção de engajamento) — "
        f"ambas alavancam o indicador de Excelência no próximo mês fechado.</p>"
    )
    return _ia_report(body)


def narrativa_gap(cur_per, cur_per_ativos, n_ativos, total, cur_targets, dimensions, cur_mes_label):
    items = []
    for col, label in dimensions:
        med_ativos = float(cur_per_ativos[col].median()) if n_ativos else 0.0
        prof, excel = cur_targets[col]
        ratio_prof = med_ativos / prof if prof > 0 else 0.0
        ratio_excel = med_ativos / excel if excel > 0 else 0.0
        items.append({"label": label, "med": med_ativos, "prof": prof, "excel": excel,
                      "rp": ratio_prof, "re": ratio_excel})
    items.sort(key=lambda x: x["rp"])
    pior = items[0]
    melhor = items[-1]
    n_em_excel = sum(1 for x in items if x["re"] >= 1)
    n_em_prof = sum(1 for x in items if x["rp"] >= 1 and x["re"] < 1)
    n_abaixo = len(items) - n_em_excel - n_em_prof
    engajados_pct = n_ativos / total * 100

    if pior["rp"] < 0.5:
        intensidade_pior = "muito distante (menos da metade do mínimo de Proficiência)"
    elif pior["rp"] < 0.8:
        intensidade_pior = "consideravelmente distante"
    else:
        intensidade_pior = "perto, mas ainda abaixo"

    if melhor["re"] >= 1:
        diag_melhor = (
            f"Em contrapartida, <strong>{melhor['label'].lower()}</strong> "
            f"já atinge Excelência (mediana {melhor['med']:,.1f} vs target {melhor['excel']:,.0f})"
        )
    elif melhor["rp"] >= 1:
        diag_melhor = (
            f"O melhor desempenho está em <strong>{melhor['label'].lower()}</strong>, "
            f"dentro de Proficiência ({melhor['re']:.0%} do target Excelência)"
        )
    else:
        diag_melhor = (
            f"O melhor desempenho está em <strong>{melhor['label'].lower()}</strong>, "
            f"mas ainda só atinge {melhor['rp']:.0%} do target Proficiência"
        )

    body = (
        f"<p>Considerando apenas os <strong>{n_ativos:,} alunos ativos</strong> em {cur_mes_label} "
        f"({engajados_pct:.0f}% do cohort), o gap mais alarmante é em "
        f"<strong>{pior['label'].lower()}</strong>: mediana de "
        f"<strong>{pior['med']:,.1f}</strong> contra target de Proficiência "
        f"<strong>{pior['prof']:,.0f}</strong> — {intensidade_pior} "
        f"({pior['rp']:.0%} do target). "
        f"{diag_melhor}, sinalizando que o desbalanço entre dimensões é real. "
        f"No agregado, das {len(items)} dimensões medidas, "
        f"<strong>{n_em_excel} atinge(m) Excelência</strong>, "
        f"<strong>{n_em_prof} fica(m) em Proficiência</strong> e "
        f"<strong>{n_abaixo} ainda está(ão) abaixo do canal</strong>. "
        f"A leitura sugere intervenções focadas na dimensão de pior gap, "
        f"e não esforço distribuído homogeneamente entre todas as métricas.</p>"
    )
    return _ia_report(
        body,
        disclaimer="Mediana entre ativos isola o sinal dos engajados; "
        "alunos sem atividade no mês entram com zero na coluna 'turma toda' (não usada aqui).",
    )


def narrativa_scatter(scatter_df, ref_canal, cur_mes_label, cohort_total):
    n = len(scatter_df)
    if n == 0:
        return _ia_report(
            "<p>Não há alunos com acerto canônico calculado neste recorte para alimentar o mapa.</p>"
        )
    excel_min = ref_canal["excel_min"]
    prof_min = ref_canal["prof_min"]
    q_med = float(scatter_df["questoes"].median())

    upper_left = ((scatter_df["acerto_canonico_pct"] >= excel_min) & (scatter_df["questoes"] < q_med)).sum()
    upper_right = ((scatter_df["acerto_canonico_pct"] >= excel_min) & (scatter_df["questoes"] >= q_med)).sum()
    lower_right = ((scatter_df["acerto_canonico_pct"] < prof_min) & (scatter_df["questoes"] >= q_med)).sum()
    lower_left = ((scatter_df["acerto_canonico_pct"] < prof_min) & (scatter_df["questoes"] < q_med)).sum()
    meio = n - upper_left - upper_right - lower_right - lower_left
    pct_plotado = n / cohort_total * 100

    body = (
        f"<p>O mapa plota <strong>{n:,} alunos</strong> "
        f"({pct_plotado:.0f}% do cohort) que tinham acerto canônico calculável — "
        f"os ausentes não fizeram mock TEMPLATE ≥50q. "
        f"Acima da linha de Excelência ({excel_min}%), encontram-se <strong>{upper_right:,} alunos "
        f"de alto volume + alto acerto</strong> (canto superior-direito, perfil já consolidado) "
        f"e <strong>{upper_left:,} alunos de baixo volume + alto acerto</strong> "
        f"(canto superior-esquerdo, talentos com potencial subutilizado — alvo claro de intervenção "
        f"para empurrar volume). "
        f"No canto inferior-direito há <strong>{lower_right:,} alunos com volume alto e acerto "
        f"abaixo do canal</strong> — esforço significativo sem retorno, indicando problema de método "
        f"e não de dedicação. "
        f"A massa concentrada no quadrante inferior-esquerdo ({lower_left:,} alunos) e na zona neutra "
        f"({meio:,}) representa o grupo mais difícil de mover, exigindo combinação de engajamento e "
        f"qualidade simultaneamente.</p>"
    )
    return _ia_report(body)


def narrativa_trajetoria(pivot, months_closed):
    if pivot.empty or len(months_closed) == 0:
        return _ia_report("<p>Sem meses fechados suficientes para traçar trajetória.</p>")
    primeiro = pivot.iloc[0]
    ultimo = pivot.iloc[-1]
    d_excel = ultimo["Excelência"] - primeiro["Excelência"]
    d_sem = ultimo["Sem acerto canônico"] - primeiro["Sem acerto canônico"]
    d_prof = ultimo["Proficiência"] - primeiro["Proficiência"]
    primeiro_label = pivot.index[0]
    ultimo_label = pivot.index[-1]

    if d_excel > 2:
        diag_excel = f"<strong>cresceu {d_excel:.1f}pp</strong> (tendência saudável)"
    elif d_excel < -2:
        diag_excel = f"<strong>recuou {abs(d_excel):.1f}pp</strong> (sinal de alerta — turma perdendo aproveitamento)"
    else:
        diag_excel = f"ficou praticamente estável (variação {d_excel:+.1f}pp)"

    if d_sem > 5:
        diag_sem = (
            f"<strong>subiu {d_sem:.1f}pp</strong> — desengajamento crescente é a história dominante da janela"
        )
    elif d_sem < -5:
        diag_sem = f"<strong>caiu {abs(d_sem):.1f}pp</strong> — recuperação de engajamento"
    else:
        diag_sem = f"oscilou pouco ({d_sem:+.1f}pp) — engajamento estruturalmente estável"

    abaixo_atual = ultimo["Abaixo do canal"]
    excel_atual = ultimo["Excelência"]

    body = (
        f"<p>Entre <strong>{primeiro_label}</strong> e <strong>{ultimo_label}</strong>, "
        f"a fatia em Excelência {diag_excel}, enquanto a fatia 'Sem acerto canônico' {diag_sem}. "
        f"A Proficiência variou {d_prof:+.1f}pp no mesmo intervalo, indicando que o trânsito "
        f"entre faixas não foi apenas dentro do grupo engajado — boa parte da dinâmica vem da entrada "
        f"e saída do próprio engajamento no mock canônico. "
        f"No mês mais recente da janela, {excel_atual:.0f}% estão em Excelência e {abaixo_atual:.0f}% "
        f"estão abaixo do canal, o que define o tamanho relativo da oportunidade de promoção (subir "
        f"Profic→Excel) versus a de remediação (resgatar 'Abaixo' e 'Sem mock'). "
        f"A leitura combinada das duas tendências separa um problema de <em>aproveitamento</em> "
        f"(quando Excel cai com engajamento constante) de um problema de <em>engajamento</em> "
        f"(quando Sem mock sobe arrastando todas as faixas para baixo).</p>"
    )
    return _ia_report(body)


def narrativa_canal_acerto(ac_df, total):
    if ac_df.empty:
        return _ia_report("<p>Sem dados mensais de acerto canônico no recorte.</p>")
    primeiro = ac_df.iloc[0]
    ultimo = ac_df.iloc[-1]
    delta_med = ultimo["mediana_turma"] - primeiro["mediana_turma"]
    delta_n = ultimo["n_com_mock"] - primeiro["n_com_mock"]
    pct_engaj_atual = ultimo["n_com_mock"] / total * 100
    pct_engaj_ini = primeiro["n_com_mock"] / total * 100

    if ultimo["mediana_turma"] >= ultimo["excel_min"]:
        posicao = (
            f"<strong>dentro do canal Excelência v2</strong> "
            f"({ultimo['excel_min']}–{ultimo['excel_max']}%)"
        )
    elif ultimo["mediana_turma"] >= ultimo["prof_min"]:
        posicao = (
            f"<strong>dentro do canal Proficiência v1</strong> "
            f"({ultimo['prof_min']}–{ultimo['prof_max']}%), "
            f"a {ultimo['excel_min'] - ultimo['mediana_turma']:.1f}pp de entrar no canal Excelência v2"
        )
    else:
        posicao = (
            f"<strong>abaixo do canal Proficiência v1</strong> "
            f"(piso {ultimo['prof_min']}%), com gap de "
            f"{ultimo['prof_min'] - ultimo['mediana_turma']:.1f}pp pra entrar no canal"
        )

    if delta_med > 3:
        evol = f"<strong>subiu {delta_med:.1f}pp</strong> no período"
    elif delta_med < -3:
        evol = f"<strong>caiu {abs(delta_med):.1f}pp</strong> no período"
    else:
        evol = f"oscilou pouco ({delta_med:+.1f}pp), mantendo-se estável"

    body = (
        f"<p>A mediana de acerto canônico da turma R1 2026 fechou o último mês em "
        f"<strong>{ultimo['mediana_turma']:.1f}%</strong>, posicionando-se {posicao}. "
        f"A trajetória {evol}, sugerindo que o mock canônico — quando feito — "
        f"está {'evoluindo na direção certa' if delta_med > 0 else 'estagnado ou regredindo'} "
        f"em comparação com a referência histórica de 2025. "
        f"O número de alunos que efetivamente fizeram mock canônico saiu de "
        f"<strong>{int(primeiro['n_com_mock'])}</strong> ({pct_engaj_ini:.0f}% do cohort) para "
        f"<strong>{int(ultimo['n_com_mock'])}</strong> ({pct_engaj_atual:.0f}%), variação de "
        f"{delta_n:+.0f} alunos no engajamento ao canal. "
        f"O ponto crítico é que a mediana é calculada apenas entre os ~{pct_engaj_atual:.0f}% engajados "
        f"— se o objetivo é projetar performance da turma toda, ainda é preciso multiplicar "
        f"esse aproveitamento pela taxa de adesão ao mock canônico, hoje o maior bottleneck.</p>"
    )
    return _ia_report(body)


def narrativa_safra_excel(safra_df, ref_label):
    if safra_df.empty:
        return _ia_report("<p>Sem dados de safra disponíveis.</p>")
    s = safra_df.reset_index(drop=True)
    maior = s.loc[s["pct_excelencia"].idxmax()]
    menor = s.loc[s["pct_excelencia"].idxmin()]
    primeira = s.iloc[0]
    ultima = s.iloc[-1]
    delta = primeira["pct_excelencia"] - ultima["pct_excelencia"]

    if delta >= 8:
        gradiente = (
            f"O gradiente é <strong>claro e esperado</strong>: a safra mais antiga "
            f"({primeira['safra_label']}, {primeira['pct_excelencia']:.0f}%) supera a mais recente "
            f"({ultima['safra_label']}, {ultima['pct_excelencia']:.0f}%) em {delta:.1f}pp, "
            "compatível com a hipótese de que tempo de plataforma ajuda no aproveitamento"
        )
    elif delta >= 2:
        gradiente = (
            f"O gradiente é <strong>fraco</strong> ({delta:.1f}pp entre safra mais antiga e mais nova): "
            "tempo de plataforma agrega pouco no aproveitamento — ou o método não escala com o uso, "
            "ou as safras novas estão entrando mais bem preparadas"
        )
    elif delta >= -2:
        gradiente = (
            f"O gradiente é <strong>praticamente plano</strong> ({delta:+.1f}pp), "
            "indicando que estar há mais tempo na plataforma não está se traduzindo em "
            "vantagem de aproveitamento — sinal forte para revisar a curva de aprendizado"
        )
    else:
        gradiente = (
            f"O gradiente está <strong>invertido</strong> ({delta:+.1f}pp): safras mais recentes "
            "performam melhor que as antigas no canal de acerto — algo no produto ou no perfil "
            "mudou recentemente, e quem estava antes não está capturando o ganho"
        )

    body = (
        f"<p>Tomando <strong>{ref_label}</strong> como referência, a melhor safra é a de "
        f"<strong>{maior['safra_label']}</strong> com {maior['pct_excelencia']:.0f}% em Excelência "
        f"(n={int(maior['total'])}), enquanto a pior é a de <strong>{menor['safra_label']}</strong> "
        f"com {menor['pct_excelencia']:.0f}% (n={int(menor['total'])}). "
        f"{gradiente}. "
        f"A leitura ganha peso ao notar que a massa do cohort está concentrada em "
        f"<strong>{primeira['safra_label']}</strong> ({int(primeira['total'])} alunos), "
        f"de modo que a performance dessa safra arrasta o agregado e qualquer intervenção "
        f"tem maior alavancagem ali. "
        f"Combinada com a leitura do gráfico de volume logo abaixo, dá pra separar se o problema "
        f"é de <em>aproveitamento</em> (faz volume, não vira Excel) ou de <em>volume</em> "
        f"(não faz o suficiente pra ter chance de virar Excel) em cada safra.</p>"
    )
    return _ia_report(body)


def narrativa_safra_volume(vol_df, ref_label, safra_acerto_df):
    if vol_df.empty:
        return _ia_report("<p>Sem dados de safra disponíveis.</p>")
    v = vol_df.reset_index(drop=True)
    maior = v.loc[v["pct_excel"].idxmax()]
    menor = v.loc[v["pct_excel"].idxmin()]
    primeira = v.iloc[0]

    comp_lines = []
    if not safra_acerto_df.empty:
        merged = v.merge(
            safra_acerto_df[["safra", "pct_excelencia"]],
            on="safra", how="inner",
        )
        for _, row in merged.iterrows():
            gap = row["pct_excel"] - row["pct_excelencia"]
            comp_lines.append((row["safra_label"], row["pct_excel"], row["pct_excelencia"], gap))

    if comp_lines:
        maior_gap_vol = max(comp_lines, key=lambda x: x[3])
        if maior_gap_vol[3] > 5:
            comp = (
                f"A safra de <strong>{maior_gap_vol[0]}</strong> apresenta o maior descolamento "
                f"entre volume e aproveitamento ({maior_gap_vol[1]:.0f}% atinge volume Excel "
                f"vs apenas {maior_gap_vol[2]:.0f}% em Excel no acerto canônico, gap de "
                f"{maior_gap_vol[3]:.0f}pp) — esforço alto sem retorno proporcional, "
                f"caso clássico para revisar método de estudo, não dedicação"
            )
        else:
            comp = (
                "As taxas de volume e aproveitamento estão razoavelmente alinhadas entre safras "
                "— quem faz o volume prescrito também tende a colher o aproveitamento esperado"
            )
    else:
        comp = "Sem dados suficientes para comparar volume vs aproveitamento por safra"

    body = (
        f"<p>Olhando para o volume acumulado B+Q+F (blocos+questões+flashcards) de cada aluno "
        f"desde o mês de matrícula até <strong>{ref_label}</strong>, a melhor safra em "
        f"aderência ao volume Excelência é <strong>{maior['safra_label']}</strong> com "
        f"<strong>{maior['pct_excel']:.0f}%</strong> dos alunos atingindo o target acumulado "
        f"(n={int(maior['total'])}), enquanto a mais distante é <strong>{menor['safra_label']}</strong> "
        f"({menor['pct_excel']:.0f}%, n={int(menor['total'])}). "
        f"A safra <strong>{primeira['safra_label']}</strong> tem mediana de "
        f"<strong>{int(primeira['mediana_bqf']):,}</strong> atividades acumuladas vs target "
        f"Excel de {int(primeira['target_excel']):,} — a distância entre a mediana e o target "
        f"dá a magnitude do esforço típico de quem é mediano na safra. "
        f"{comp}. "
        f"Esse gráfico complementa o de aproveitamento logo acima ao separar a pergunta "
        f"<em>'a turma está estudando o suficiente?'</em> da pergunta "
        f"<em>'a turma está estudando bem?'</em> — fundamentais para priorizar intervenção entre "
        f"engajamento (volume) e qualidade (método).</p>"
    )
    return _ia_report(
        body,
        disclaimer="B+Q+F soma unidades diferentes sem ponderação. Útil como proxy de volume "
        "agregado de atividade, mas inadequado para comparar mix de estudo entre alunos.",
    )


def narrativa_matriz(cm_n, cm_pct, cm_total):
    if cm_n.empty or cm_total == 0:
        return _ia_report("<p>Sem dados Q1/26 para a matriz prescrição × resultado.</p>")

    def safe_pct(r, c):
        try:
            return float(cm_pct.loc[r, c])
        except KeyError:
            return 0.0

    def safe_n(r):
        try:
            return int(cm_n.loc[r].sum())
        except KeyError:
            return 0

    n_seg_excel = safe_n("Seguindo Excelência")
    ex_to_ex = safe_pct("Seguindo Excelência", "Excelência")
    ex_to_below = safe_pct("Seguindo Excelência", "Abaixo do canal")
    ex_to_nomock = safe_pct("Seguindo Excelência", "Sem acerto canônico")

    n_seg_abaixo = safe_n("Abaixo de Proficiência")
    abaixo_to_ex = safe_pct("Abaixo de Proficiência", "Excelência")
    abaixo_to_nomock = safe_pct("Abaixo de Proficiência", "Sem acerto canônico")

    if ex_to_ex >= 40:
        diag_excel = "uma aderência razoável entre volume prescrito e aproveitamento real"
    elif ex_to_ex >= 25:
        diag_excel = "uma conversão moderada — fazer volume Excel ajuda, mas longe de ser determinante"
    else:
        diag_excel = (
            "uma conversão baixa — fazer o volume prescrito praticamente não garante o "
            "resultado no canal de acerto, indicando que o método de estudo importa "
            "tanto ou mais que a quantidade"
        )

    body = (
        f"<p>Restringindo aos <strong>{cm_total:,} alunos Q1/26</strong>, "
        f"dos <strong>{n_seg_excel:,}</strong> que estão seguindo o volume prescrito de "
        f"Excelência, apenas <strong>{ex_to_ex:.0f}%</strong> colheram Excelência no acerto "
        f"canônico do mês — {diag_excel}. "
        f"Outros <strong>{ex_to_below:.0f}%</strong> caíram para Abaixo do canal mesmo com "
        f"volume alto, e <strong>{ex_to_nomock:.0f}%</strong> sequer fizeram mock canônico "
        f"(volume alto sem avaliação no canal — risco de overconfidence). "
        f"No extremo oposto, dos <strong>{n_seg_abaixo:,}</strong> alunos abaixo da prescrição, "
        f"<strong>{abaixo_to_ex:.0f}%</strong> ainda atingiram Excelência (talento ou método "
        f"excepcional) e <strong>{abaixo_to_nomock:.0f}%</strong> não fizeram mock canônico — "
        f"este último é o grupo prioritário de resgate, pois combina baixo volume e nenhuma "
        f"avaliação. "
        f"Ressalva metodológica importante: a matriz mede volume e resultado no <em>mesmo</em> "
        f"período, então a correlação contém efeitos simultâneos (alunos engajados tendem a fazer "
        f"as duas coisas) — a versão lead-lag abaixo isola melhor o efeito preditivo.</p>"
    )
    return _ia_report(body)


def narrativa_leadlag(ll_n, ll_pct, ll_total, ll_pairs, cm_pct):
    if ll_n.empty or ll_total == 0:
        return _ia_report("<p>Sem pares (N, N+1) suficientes para análise lead-lag.</p>")

    def safe_pct(df_, r, c):
        try:
            return float(df_.loc[r, c])
        except KeyError:
            return 0.0

    def safe_n(df_, r):
        try:
            return int(df_.loc[r].sum())
        except KeyError:
            return 0

    n_seg_excel = safe_n(ll_n, "Seguiu Excelência")
    ll_ex_to_ex = safe_pct(ll_pct, "Seguiu Excelência", "Excelência")
    ll_ex_to_nomock = safe_pct(ll_pct, "Seguiu Excelência", "Sem acerto canônico")

    n_seg_abaixo = safe_n(ll_n, "Abaixo de Proficiência")
    ll_abaixo_to_nomock = safe_pct(ll_pct, "Abaixo de Proficiência", "Sem acerto canônico")

    if cm_pct is not None and not cm_pct.empty:
        try:
            acum_ex_to_ex = float(cm_pct.loc["Seguindo Excelência", "Excelência"])
            delta = ll_ex_to_ex - acum_ex_to_ex
            if abs(delta) < 2:
                cmp = (
                    f"praticamente igual à versão simultânea do gráfico anterior ({acum_ex_to_ex:.0f}%), "
                    "sugerindo que a correlação simultânea já era proxy decente do efeito preditivo"
                )
            elif delta > 0:
                cmp = (
                    f"<strong>{abs(delta):.0f}pp maior</strong> que a versão simultânea anterior "
                    f"({acum_ex_to_ex:.0f}%) — a correlação simultânea estava subestimando o efeito "
                    "preditivo do volume sobre o resultado seguinte"
                )
            else:
                cmp = (
                    f"<strong>{abs(delta):.0f}pp menor</strong> que a versão simultânea anterior "
                    f"({acum_ex_to_ex:.0f}%) — a correlação simultânea inflava o efeito por capturar "
                    "alunos engajados nos dois eixos ao mesmo tempo, e o poder preditivo real é mais modesto"
                )
        except (KeyError, AttributeError):
            cmp = "sem matriz simultânea comparável neste contexto"
    else:
        cmp = "sem matriz simultânea para comparação"

    body = (
        f"<p>Empilhando todos os pares (aluno × mês N → mês N+1) entre os "
        f"{len(ll_pairs)} pares Q1/26 disponíveis, totalizando "
        f"<strong>{ll_total:,} observações</strong>, das "
        f"<strong>{n_seg_excel:,}</strong> em que o aluno seguiu volume de Excelência no mês N, "
        f"<strong>{ll_ex_to_ex:.0f}%</strong> estavam em Excelência no acerto canônico do mês "
        f"seguinte — {cmp}. "
        f"Outros <strong>{ll_ex_to_nomock:.0f}%</strong> que fizeram o volume não fizeram mock "
        f"canônico no mês seguinte (desengajamento da avaliação mesmo com esforço continuado). "
        f"Dos <strong>{n_seg_abaixo:,}</strong> alunos que ficaram abaixo da prescrição, "
        f"<strong>{ll_abaixo_to_nomock:.0f}%</strong> também sumiram do mock canônico em N+1 — "
        f"comportamento consistente com saída gradual do engajamento (baixo volume hoje vira "
        f"nenhum mock amanhã). "
        f"Limitação metodológica: o lead-lag reduz mas não elimina viés de seleção, pois alunos "
        f"que persistem na plataforma de N para N+1 já estão filtrados por engajamento — "
        f"a comparação correta seria contrafactual, e ela não está disponível aqui.</p>"
    )
    return _ia_report(body)


# ============================================================
# ABA AGORA (extraída em função pra reuso na aba B2B)
# ============================================================
# A função lê as globais `df`, `cohort`, `total`, `months_closed`, `last_closed`,
# `prev_closed` no momento da chamada. Para renderizar B2B, basta sobrescrever
# essas globais antes de chamar (ver `with tab_b2b:` abaixo).

def _compute_status_geral(df_: pd.DataFrame, cohort_: pd.DataFrame, last_closed_: str) -> dict:
    """Calcula 4 indicadores de status do cohort em 2026:

    - **Pagantes**: tamanho do cohort (inscrição válida — já filtrado upstream).
    - **Ativos**: alunos com `dias_ativos > 0` em alguma semana cujo início é
      ≥ (hoje - 15 dias). Reflete "entraram na plataforma nos últimos 15 dias".
    - **Engajados**: média semanal ≥25 questões E ≥8 blocos desde a primeira
      semana ativa do aluno em 2026 até a última semana fechada.
    - **Meta mínima**: média semanal ≥70 questões na mesma janela.
    """
    cohort_ids = cohort_["account_id"]
    n_pagantes = len(cohort_)

    # --- Ativos últimos 15 dias ---
    cutoff_15d = pd.Timestamp(date.today()) - pd.Timedelta(days=15)
    recent = df_[(df_["semana_iso"] >= cutoff_15d) & (df_["dias_ativos"] > 0)]
    recent = recent[recent["account_id"].isin(cohort_ids)]
    n_ativos_15d = int(recent["account_id"].nunique())

    # --- Engajados / meta mínima (lógica anterior) ---
    week_start = pd.Timestamp("2026-01-05")
    last_mes_end = pd.Timestamp(f"{last_closed_}-01") + pd.offsets.MonthEnd(0)
    last_sunday = last_mes_end - pd.Timedelta(days=(last_mes_end.weekday() + 1) % 7)
    last_monday = last_sunday - pd.Timedelta(days=6)

    sub = df_[(df_["semana_iso"] >= week_start) & (df_["semana_iso"] <= last_monday)]
    sub = sub[sub["account_id"].isin(cohort_ids)]
    if sub.empty:
        return {"n_pagantes": n_pagantes, "n_ativos_15d": n_ativos_15d,
                "n_engajados": 0, "n_meta_min": 0}

    first_active = (
        sub[sub["dias_ativos"] > 0]
        .groupby("account_id")["semana_iso"].min()
        .rename("first_active_week").reset_index()
    )
    if first_active.empty:
        return {"n_pagantes": n_pagantes, "n_ativos_15d": n_ativos_15d,
                "n_engajados": 0, "n_meta_min": 0}

    joined = sub.merge(first_active, on="account_id", how="inner")
    joined = joined[joined["semana_iso"] >= joined["first_active_week"]]
    by_aluno = joined.groupby("account_id").agg(
        questoes_total=("questoes", "sum"),
        blocos_total=("blocos", "sum"),
        first_active_week=("first_active_week", "first"),
    ).reset_index()
    by_aluno["n_semanas"] = (
        (last_monday - by_aluno["first_active_week"]).dt.days // 7 + 1
    ).clip(lower=1)
    by_aluno["mean_q_wk"] = by_aluno["questoes_total"] / by_aluno["n_semanas"]
    by_aluno["mean_b_wk"] = by_aluno["blocos_total"] / by_aluno["n_semanas"]

    n_engajados = int(((by_aluno["mean_q_wk"] >= 25) & (by_aluno["mean_b_wk"] >= 8)).sum())
    n_meta_min = int((by_aluno["mean_q_wk"] >= 70).sum())
    return {"n_pagantes": n_pagantes, "n_ativos_15d": n_ativos_15d,
            "n_engajados": n_engajados, "n_meta_min": n_meta_min}


def _render_agora_now(key_prefix: str = "agora"):
    # --- Status geral: pagantes, ativos (15d), engajados, meta mínima ---
    _status = _compute_status_geral(df, cohort, last_closed)
    _stat_pagantes = _status["n_pagantes"]
    _stat_ativos = _status["n_ativos_15d"]
    _stat_engaj = _status["n_engajados"]
    _stat_meta = _status["n_meta_min"]
    _denom = _stat_pagantes if _stat_pagantes else 1
    _pct_ativos = _stat_ativos / _denom * 100
    _pct_engaj = _stat_engaj / _denom * 100
    _pct_meta = _stat_meta / _denom * 100

    st.markdown("&nbsp;")
    sg1, sg2, sg3, sg4 = st.columns(4)
    sg1.markdown(
        f"""<div class="hero-card">
          <div class="lbl"><span class="pill" style="background:#32578A"></span>Pagantes</div>
          <div class="val">{_stat_pagantes:,}</div>
          <div class="sub">inscrição ativa</div>
        </div>""",
        unsafe_allow_html=True,
    )
    sg2.markdown(
        f"""<div class="hero-card">
          <div class="lbl"><span class="pill" style="background:#841A81"></span>Ativos</div>
          <div class="val">{_stat_ativos:,} <span style="font-size:14px;color:#71717a">({_pct_ativos:.0f}%)</span></div>
          <div class="sub">acessaram nos últimos 15 dias</div>
        </div>""",
        unsafe_allow_html=True,
    )
    sg3.markdown(
        f"""<div class="hero-card">
          <div class="lbl"><span class="pill" style="background:#05FC89"></span>Meta mínima</div>
          <div class="val">{_stat_meta:,} <span style="font-size:14px;color:#71717a">({_pct_meta:.0f}%)</span></div>
          <div class="sub">≥70 questões/semana (média)</div>
        </div>""",
        unsafe_allow_html=True,
    )
    sg4.markdown(
        f"""<div class="hero-card">
          <div class="lbl"><span class="pill" style="background:#EAB904"></span>Engajados</div>
          <div class="val">{_stat_engaj:,} <span style="font-size:14px;color:#71717a">({_pct_engaj:.0f}%)</span></div>
          <div class="sub">≥25 questões + ≥8 blocos/semana (média)</div>
        </div>""",
        unsafe_allow_html=True,
    )

    # Janela fixa: último mês fechado, com Δ vs mês anterior nos cards.
    cur_per = aggregate_month(df, last_closed)
    prev_per = aggregate_month(df, prev_closed) if prev_closed else None
    cur_targets = PRESCRIPTION_TARGETS[last_closed]
    cur_mes = last_closed
    cur_mes_label = PRESCRIPTION_MONTHLY[last_closed]["label"]
    prev_mes_label = PRESCRIPTION_MONTHLY[prev_closed]["label"] if prev_closed else None

    # --- Bloco 1: hero cards ---
    cur_counts = faixa_counts(cur_per)
    prev_counts = faixa_counts(prev_per) if prev_per is not None else None

    st.markdown("&nbsp;")
    cols = st.columns(4)
    # Critério dinâmico por faixa baseado no alvo do mês de referência.
    _ch = PRESCRIPTION_MONTHLY[cur_mes]
    _meaning_dyn = {
        "Excelência":          f"acertou ≥{_ch['excel_min']}% no mock de {cur_mes_label}",
        "Proficiência":        f"acertou {_ch['prof_min']}–{_ch['excel_min']-1}% no mock de {cur_mes_label}",
        "Abaixo do canal":     f"acertou <{_ch['prof_min']}% no mock de {cur_mes_label}",
        "Sem acerto canônico": f"não fez mock canônico em {cur_mes_label}",
    }
    for i, faixa in enumerate(PRESCRIPTION_CLASSES):
        n_cur = cur_counts[faixa]
        pct_cur = n_cur / total * 100
        color = PRESCRIPTION_COLORS[faixa]
        meaning = _meaning_dyn[faixa]

        if prev_counts is not None:
            n_prev = prev_counts[faixa]
            pct_prev = n_prev / total * 100
            delta_pp = pct_cur - pct_prev
            if abs(delta_pp) < 0.5:
                delta_html = f'<span class="delta-flat">≈ vs {prev_mes_label}</span>'
            elif delta_pp > 0:
                cls = "delta-up" if faixa in ("Excelência", "Proficiência") else "delta-dn"
                delta_html = f'<span class="{cls}">+{delta_pp:.1f}pp vs {prev_mes_label}</span>'
            else:
                cls = "delta-dn" if faixa in ("Excelência", "Proficiência") else "delta-up"
                delta_html = f'<span class="{cls}">{delta_pp:.1f}pp vs {prev_mes_label}</span>'
        else:
            delta_html = '<span class="delta-flat">—</span>'

        cols[i].markdown(
            f"""<div class="hero-card">
              <div class="lbl"><span class="pill" style="background:{color}"></span>{faixa}</div>
              <div class="val">{pct_cur:.0f}%</div>
              <div class="sub">{n_cur:,} de {total:,} alunos · {meaning}</div>
              <div class="sub" style="margin-top:8px">{delta_html}</div>
            </div>""",
            unsafe_allow_html=True,
        )

    _ch = PRESCRIPTION_MONTHLY[cur_mes]
    st.caption(
        f"Alvos {cur_mes_label}: Proficiência ≥{_ch['prof_min']}% · "
        f"Excelência ≥{_ch['excel_min']}% de acerto no mock."
    )
    # --- Bloco 1b: Volume por faixa (média e mediana) ---
    # Mostra quanto de questões/flashcards/blocos cada faixa consumiu no mês.
    # Alunos sem atividade entram com 0 — puxa a mediana de "Sem acerto canônico"
    # pra zero, o que é a leitura honesta: a faixa é dominada por inativos.
    _vol_cols = [
        ("questoes", "Questões"),
        ("flashcards", "Flashcards"),
        ("blocos", "Blocos de aula"),
        ("dias_ativos", "Dias ativos"),
    ]
    _stats_faixas = {
        f: cur_per[cur_per["faixa"] == f][[c for c, _ in _vol_cols]]
        for f in PRESCRIPTION_CLASSES
    }

    def _render_vol_table(stat: str) -> str:
        agg_fn = "mean" if stat == "mean" else "median"
        rows_html = []
        for col, label in _vol_cols:
            cells = []
            for f in PRESCRIPTION_CLASSES:
                sub = _stats_faixas[f][col]
                val = float(getattr(sub, agg_fn)()) if len(sub) else 0.0
                color = PRESCRIPTION_COLORS[f]
                cells.append(
                    f"<td class='val' style='border-left:3px solid {color}'>"
                    f"{val:,.0f}</td>"
                )
            turma = float(getattr(cur_per[col], agg_fn)())
            cells.append(f"<td class='val' style='font-weight:600'>{turma:,.0f}</td>")
            rows_html.append(f"<tr><td class='dim'>{label}</td>{''.join(cells)}</tr>")
        header_cells = "".join(
            f"<th style='text-align:right'>"
            f"<span class='pill' style='background:{PRESCRIPTION_COLORS[f]};display:inline-block;"
            f"width:8px;height:8px;border-radius:50%;margin-right:6px'></span>{f}"
            f"<br><span style='font-size:11px;color:#B8B8B8;font-weight:400'>n={cur_counts[f]:,}</span>"
            f"</th>"
            for f in PRESCRIPTION_CLASSES
        )
        header_cells += (
            f"<th style='text-align:right'>Turma toda"
            f"<br><span style='font-size:11px;color:#B8B8B8;font-weight:400'>n={total:,}</span></th>"
        )
        return (
            f"<table class='gap-table'>"
            f"<thead><tr><th>Dimensão</th>{header_cells}</tr></thead>"
            f"<tbody>{''.join(rows_html)}</tbody></table>"
        )

    st.markdown("&nbsp;")
    col_mean, col_med = st.columns(2)
    with col_mean:
        st.markdown(f"##### Média por faixa — {cur_mes_label}")
        st.markdown(_render_vol_table("mean"), unsafe_allow_html=True)
        st.caption("Média entre alunos da faixa no mês.")
    with col_med:
        st.markdown(f"##### Mediana por faixa — {cur_mes_label}")
        st.markdown(_render_vol_table("median"), unsafe_allow_html=True)
        st.caption("Aluno típico da faixa no mês.")

    # --- Bloco 1c: Volume ACUMULADO por faixa (jan/26 → último mês fechado) ---
    # Mostra o volume total que cada aluno construiu desde o início do extensivo.
    # Faixa usada é a do mês de referência (cur_mes) — mostra "quanto de esforço
    # acumulado cada faixa atual já fez". Comparável com o joelho dose-response.
    _df_acum = df[df["semana_iso"].dt.to_period("M").astype(str) <= cur_mes]
    _acum_aluno = _df_acum.groupby("account_id", as_index=False).agg(
        questoes=("questoes", "sum"),
        flashcards=("flashcards", "sum"),
        blocos=("blocos", "sum"),
        dias_ativos=("dias_ativos", "sum"),
    )
    # Cohort completo com 0 pra quem nunca fez nada
    _acum_per = cohort[["account_id"]].merge(_acum_aluno, on="account_id", how="left").fillna(0)
    _acum_per = _acum_per.merge(cur_per[["account_id", "faixa"]], on="account_id", how="left")
    _acum_stats_faixas = {
        f: _acum_per[_acum_per["faixa"] == f][[c for c, _ in _vol_cols]]
        for f in PRESCRIPTION_CLASSES
    }

    def _render_acum_table(stat: str) -> str:
        agg_fn = "mean" if stat == "mean" else "median"
        rows_html = []
        for col, label in _vol_cols:
            cells = []
            for f in PRESCRIPTION_CLASSES:
                sub = _acum_stats_faixas[f][col]
                val = float(getattr(sub, agg_fn)()) if len(sub) else 0.0
                color = PRESCRIPTION_COLORS[f]
                cells.append(
                    f"<td class='val' style='border-left:3px solid {color}'>"
                    f"{val:,.0f}</td>"
                )
            turma = float(getattr(_acum_per[col], agg_fn)())
            cells.append(f"<td class='val' style='font-weight:600'>{turma:,.0f}</td>")
            rows_html.append(f"<tr><td class='dim'>{label}</td>{''.join(cells)}</tr>")
        header_cells = "".join(
            f"<th style='text-align:right'>"
            f"<span class='pill' style='background:{PRESCRIPTION_COLORS[f]};display:inline-block;"
            f"width:8px;height:8px;border-radius:50%;margin-right:6px'></span>{f}"
            f"<br><span style='font-size:11px;color:#B8B8B8;font-weight:400'>n={cur_counts[f]:,}</span>"
            f"</th>"
            for f in PRESCRIPTION_CLASSES
        )
        header_cells += (
            f"<th style='text-align:right'>Turma toda"
            f"<br><span style='font-size:11px;color:#B8B8B8;font-weight:400'>n={total:,}</span></th>"
        )
        return (
            f"<table class='gap-table'>"
            f"<thead><tr><th>Dimensão</th>{header_cells}</tr></thead>"
            f"<tbody>{''.join(rows_html)}</tbody></table>"
        )

    st.markdown("&nbsp;")
    _first_mes_label = PRESCRIPTION_MONTHLY[PRESCRIPTION_ORDER[0]]["label"]
    col_acum_mean, col_acum_med = st.columns(2)
    with col_acum_mean:
        st.markdown(f"##### Média acumulada por faixa — {_first_mes_label} → {cur_mes_label}")
        st.markdown(_render_acum_table("mean"), unsafe_allow_html=True)
        st.caption(f"Soma de cada aluno desde {_first_mes_label}, depois média da faixa.")
    with col_acum_med:
        st.markdown(f"##### Mediana acumulada por faixa — {_first_mes_label} → {cur_mes_label}")
        st.markdown(_render_acum_table("median"), unsafe_allow_html=True)
        st.caption("Esforço total típico do aluno da faixa desde o início.")

    # --- Bloco 3: scatter acionável ---
    st.markdown("&nbsp;")
    st.markdown("##### Mapa da turma — Questões × Acerto")
    st.caption("Cada ponto é 1 aluno (acumulado).")

    scatter_df = metrics_acum.merge(
        cur_per[["account_id", "faixa"]], on="account_id", how="left"
    ).dropna(subset=["acerto_canonico_pct"])
    scatter_df["faixa"] = scatter_df["faixa"].astype(
        pd.CategoricalDtype(categories=PRESCRIPTION_CLASSES, ordered=True)
    )

    fig = px.scatter(
        scatter_df,
        x="questoes",
        y="acerto_canonico_pct",
        color="faixa",
        color_discrete_map=PRESCRIPTION_COLORS,
        hover_data={
            "name": True, "questoes": ":,", "flashcards": ":,",
            "blocos": ":,", "acerto_canonico_pct": ":.1f", "faixa": True,
        },
        labels={
            "questoes": "Questões totais (acumulado)",
            "acerto_canonico_pct": "% acerto canônico",
            "faixa": f"Faixa em {cur_mes_label}",
        },
        opacity=0.85,
    )
    ref = PRESCRIPTION_MONTHLY[cur_mes]
    fig.add_hline(y=ref["prof_min"], line_dash="dot", line_color="#32578A",
                  annotation_text=f"P25 Proficiência {ref['label']} ({ref['prof_min']}%)",
                  annotation_position="bottom right")
    fig.add_hline(y=ref["excel_min"], line_dash="dot", line_color="#05FC89",
                  annotation_text=f"P25 Excelência {ref['label']} ({ref['excel_min']}%)",
                  annotation_position="top right")
    fig.update_layout(
        height=460,
        plot_bgcolor="#fff", paper_bgcolor="#fff",
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=-0.22),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_scatter_map")


# Aba Agora — 9 sub-abas (Geral + 8 turmas). Reusa _render_agora_now via swap
# de globais (mesma estratégia da aba B2B).
def _render_agora_subtab(cohort_filtrado: pd.DataFrame, label_grupo: str, key_prefix: str):
    """Renderiza os blocos de Agora pra um subset filtrado do cohort B2C."""
    if cohort_filtrado.empty:
        st.warning(f"Sem alunos em {label_grupo}.")
        return
    cohort_uniq = cohort_filtrado.drop_duplicates("account_id", keep="first")
    if "turma" in cohort_uniq.columns:
        cohort_uniq = cohort_uniq.drop(columns=["turma"])
    ids = set(cohort_uniq["account_id"].astype(int).tolist())

    global df, cohort, total, months_closed, last_closed, prev_closed, cur_per
    _saves = (df, cohort, total, months_closed, last_closed, prev_closed, cur_per)
    try:
        df = df[df["account_id"].isin(ids)]
        cohort = cohort_uniq
        total = len(cohort)
        months_closed = closed_months(today, df)
        if not months_closed:
            st.info(f"Sem mês fechado com atividade em {label_grupo}.")
            return
        last_closed = months_closed[-1]
        prev_closed = months_closed[-2] if len(months_closed) >= 2 else None
        cur_per = aggregate_month(df, last_closed, cohort_df=cohort)
        st.caption(f"**{label_grupo}** — {total:,} alunos. Snapshot {snapshot_date:%d/%m %H:%M}.")
        _render_agora_now(key_prefix=key_prefix)
    finally:
        df, cohort, total, months_closed, last_closed, prev_closed, cur_per = _saves


# Turmas disponíveis — derivadas do cohort, ordenadas por tamanho.
_TURMAS_DISPONIVEIS = (
    cohort_turma.groupby("turma")["account_id"].nunique()
    .sort_values(ascending=False)
    .index.tolist()
)

with tab_agora:
    st.caption("Selecione 1 ou mais turmas:")
    _sel_turmas = st.multiselect(
        "Turmas",
        _TURMAS_DISPONIVEIS,
        default=_TURMAS_DISPONIVEIS,
        key="agora_turmas_multi",
        label_visibility="collapsed",
    )
    if not _sel_turmas:
        st.info("Selecione ao menos uma turma.")
    else:
        _cohort_sel = cohort_turma[cohort_turma["turma"].isin(_sel_turmas)]
        if len(_sel_turmas) == len(_TURMAS_DISPONIVEIS):
            _label = "Geral — todas as turmas"
        elif len(_sel_turmas) == 1:
            _label = _sel_turmas[0]
        else:
            _label = f"{len(_sel_turmas)} turmas: {', '.join(_sel_turmas)}"
        _render_agora_subtab(_cohort_sel, _label, "agora_multi")


# Aba B2B — sub-abas Inspirali (15 escolas) e Geral (grupos educacionais).
def _render_b2b_subtab(cohort_filtrado: pd.DataFrame, label_grupo: str, key_prefix: str = "b2b"):
    """Renderiza os blocos da aba Agora para um subset filtrado do cohort B2B.

    Faz swap das globais (df, cohort, total, last_closed, etc.) pra que
    `_render_agora_now()` use o subset filtrado.
    """
    if cohort_filtrado.empty:
        st.warning(f"Nenhum aluno em `{label_grupo}` no cohort B2B.")
        return

    ids_filtrados = set(cohort_filtrado["account_id"].astype(int).tolist())
    df_filtrado = df_b2b[df_b2b["account_id"].isin(ids_filtrados)]

    global df, cohort, total, months_closed, last_closed, prev_closed, cur_per
    _df_save, _cohort_save, _total_save = df, cohort, total
    _mc_save, _last_save, _prev_save, _cur_per_save = months_closed, last_closed, prev_closed, cur_per
    try:
        df = df_filtrado
        cohort = cohort_filtrado.drop_duplicates("account_id")
        total = len(cohort)
        months_closed = closed_months(today, df_filtrado)
        if not months_closed:
            st.info(f"Sem mês fechado com atividade em `{label_grupo}`.")
            return
        last_closed = months_closed[-1]
        prev_closed = months_closed[-2] if len(months_closed) >= 2 else None
        cur_per = aggregate_month(df, last_closed, cohort_df=cohort)
        st.info(
            f"📊 **{label_grupo}** — {total:,} alunos. Mesma régua v4 aplicada. "
            f"Snapshot {snapshot_date_b2b:%d/%m %H:%M}."
        )
        _render_agora_now(key_prefix=key_prefix)

        # --- Bloco ENAMED Inspirali 2026: desempenho do grupo ---
        st.markdown("&nbsp;")
        st.markdown("##### Simulados ENAMED Inspirali 2026")
        st.caption("Apenas mocks finalizados. % ≥ 60 = alunos com 60%+ de acerto.")
        try:
            _enamed_ids = tuple(sorted(int(a) for a in cohort["account_id"].tolist()))
            enamed_df = load_enamed_2026_results(_enamed_ids)
        except Exception as _e:
            st.error(f"Falha ao carregar ENAMED do Aurora: {_e}")
            enamed_df = pd.DataFrame()

        if enamed_df.empty:
            st.info(f"Nenhum aluno deste grupo finalizou um simulado ENAMED Inspirali 2026.")
        else:
            rows_html = []
            for tid, tname, tdate in load_enamed_2026_templates():
                sub = enamed_df[enamed_df["mock_template_id"] == tid]
                if sub.empty:
                    rows_html.append(
                        f"<tr><td class='dim'>{tname}</td>"
                        f"<td class='val' style='color:#71717a'>{tdate}</td>"
                        f"<td class='val' style='color:#B8B8B8' colspan='4'>sem participação</td></tr>"
                    )
                    continue
                n = len(sub)
                cov = n / total * 100 if total else 0.0
                media = float(sub["pct"].mean())
                mediana = float(sub["pct"].median())
                pct_60 = float((sub["pct"] >= 60).mean()) * 100
                cor60 = "#04A36A" if pct_60 >= 60 else ("#EAB904" if pct_60 >= 40 else "#E64444")
                rows_html.append(
                    f"<tr>"
                    f"<td class='dim'>{tname}</td>"
                    f"<td class='val' style='color:#71717a'>{tdate}</td>"
                    f"<td class='val'>{n:,}<br><span style='font-size:11px;color:#71717a'>{cov:.0f}% do grupo</span></td>"
                    f"<td class='val'>{media:.1f}%</td>"
                    f"<td class='val'>{mediana:.1f}%</td>"
                    f"<td class='val' style='color:{cor60};font-weight:600'>{pct_60:.1f}%</td>"
                    f"</tr>"
                )
            st.markdown(
                f"""<table class="gap-table">
                  <thead><tr>
                    <th>Simulado</th>
                    <th style="text-align:right">Data</th>
                    <th style="text-align:right">N finalizados</th>
                    <th style="text-align:right">Média</th>
                    <th style="text-align:right">Mediana</th>
                    <th style="text-align:right">% ≥ 60 acertos</th>
                  </tr></thead>
                  <tbody>{''.join(rows_html)}</tbody>
                </table>""",
                unsafe_allow_html=True,
            )
            # Agregado
            tot_n = len(enamed_df)
            tot_alunos = enamed_df["account_id"].nunique()
            tot_pct_60 = (enamed_df["pct"] >= 60).mean() * 100
            tot_media = enamed_df["pct"].mean()
            st.caption(
                f"Total: {tot_n:,} mocks · {tot_alunos:,}/{total:,} alunos · "
                f"média {tot_media:.1f}% · {tot_pct_60:.1f}% ≥ 60."
            )
    finally:
        df, cohort, total = _df_save, _cohort_save, _total_save
        months_closed, last_closed, prev_closed, cur_per = _mc_save, _last_save, _prev_save, _cur_per_save


with tab_b2b:
    if not HAS_B2B:
        st.warning(
            "Snapshot B2B não encontrado em `snapshots/b2b/latest*.parquet`. "
            "Rode `etl/extract_b2b.py` para gerar."
        )
    elif "ies_name" not in cohort_b2b.columns:
        st.warning(
            "Snapshot B2B desatualizado (sem colunas `company_name` e `ies_name`). "
            "Rode `etl/extract_b2b.py` novamente para regenerar."
        )
    else:
        # 15 IES oficiais Inspirali (filtra ies_id=1 EMR que aparece em alguns
        # users company_id=7 por legado de cadastro).
        INSPIRALI_IES_NAMES = {
            "AGES Irece", "AGES Jacobina", "FASEH",
            "UAM Mooca", "UAM Pira", "UAM SJC",
            "UNIBH", "UNIFACS",
            "UNIFG BR", "UniFG GBI", "UNIFG SS",
            "UNISUL PB", "UNISUL TUB",
            "UNP", "USJT",
        }
        sub_inspirali, sub_geral = st.tabs(["Inspirali", "Geral"])

        with sub_inspirali:
            insp_cohort_all = cohort_b2b[
                (cohort_b2b["company_id"] == 7)
                & (cohort_b2b["ies_name"].isin(INSPIRALI_IES_NAMES))
            ]
            ies_list = sorted(insp_cohort_all["ies_name"].dropna().unique().tolist())
            n_ies = len(ies_list)

            # Docentes já são excluídos na origem (queries/01_cohort_b2b.sql,
            # decisão 2026-06-01). A categoria "docente_extra" agora contém só
            # "Extensivo Curso Extra" (alunos reais), que entram normalmente.
            insp_students = insp_cohort_all

            # 3 sub-sub-abas. A aba ENAMED 2026 usa o período REAL do cadastro
            # (users.period IN 10,11), não a categoria de produto — decisão
            # 2026-05-28: o produto comprado superestimava o cohort (incluía
            # alunos de 9º/12º período). "demais" = complemento (todo o resto).
            if "period" not in insp_students.columns:
                st.warning(
                    "Snapshot B2B sem coluna `period`. Rode `etl/extract_b2b.py` "
                    "novamente para habilitar o filtro por 10º/11º período."
                )
                insp_students = insp_students.assign(period=pd.NA)
            INSP_CATS = [
                ("todos",  "Todos os alunos",                         lambda d: d),
                ("enamed", "Alunos ENAMED 2026 (10º e 11º períodos)", lambda d: d[d["period"].isin([10, 11])]),
                ("demais", "Demais alunos",                           lambda d: d[~d["period"].isin([10, 11])]),
            ]
            insp_subtabs = st.tabs([lab for _, lab, _ in INSP_CATS])
            for _idx, (cat_key, cat_label, cat_filter) in enumerate(INSP_CATS):
                with insp_subtabs[_idx]:
                    cohort_cat = cat_filter(insp_students)
                    n_cat_unique = cohort_cat["account_id"].nunique()
                    st.caption(f"{n_cat_unique:,} alunos · selecione 1 ou mais escolas:")
                    ies_list_cat = sorted(cohort_cat["ies_name"].dropna().unique().tolist())
                    if not ies_list_cat:
                        st.info(f"Nenhuma escola com alunos em '{cat_label}'.")
                        continue
                    sel_ies = st.multiselect(
                        "Escolas",
                        ies_list_cat,
                        default=ies_list_cat,
                        key=f"b2b_insp_{cat_key}_multi",
                        label_visibility="collapsed",
                    )
                    if not sel_ies:
                        st.info("Selecione ao menos uma escola.")
                        continue
                    _filtrado = cohort_cat[cohort_cat["ies_name"].isin(sel_ies)]
                    if len(sel_ies) == len(ies_list_cat):
                        _label = f"Inspirali · {cat_label} · {len(ies_list_cat)} escolas"
                    elif len(sel_ies) == 1:
                        _label = f"Inspirali · {cat_label} · {sel_ies[0]}"
                    else:
                        _label = f"Inspirali · {cat_label} · {len(sel_ies)} escolas"
                    _render_b2b_subtab(_filtrado, _label, key_prefix=f"b2b_insp_{cat_key}")

        with sub_geral:
            companies = (
                cohort_b2b[["company_id", "company_name"]]
                .drop_duplicates()
                .sort_values("company_name")
            )
            companies_list = companies["company_name"].tolist()
            st.caption(f"{len(companies_list)} grupos · selecione 1 ou mais:")
            sel_co = st.multiselect(
                "Grupos educacionais",
                companies_list,
                default=companies_list,
                key="b2b_geral_multi",
                label_visibility="collapsed",
            )
            if not sel_co:
                st.info("Selecione ao menos um grupo.")
            else:
                _filtrado = cohort_b2b[cohort_b2b["company_name"].isin(sel_co)]
                if len(sel_co) == len(companies_list):
                    _label = "Todos os grupos B2B"
                elif len(sel_co) == 1:
                    _label = f"Grupo · {sel_co[0]}"
                else:
                    _label = f"{len(sel_co)} grupos: {', '.join(sel_co)}"
                _render_b2b_subtab(_filtrado, _label, key_prefix="b2b_geral_multi")


# ============================================================
# ABA EVOLUÇÃO
# ============================================================

def _filter_by_ids(cohort_ids: tuple[int, ...] | None):
    """Retorna (coh_local, df_local, total_local) com base no filtro de turmas."""
    if cohort_ids is None:
        return cohort, df, total
    ids = set(cohort_ids)
    coh = cohort[cohort["account_id"].isin(ids)]
    df_local = df[df["account_id"].isin(ids)]
    return coh, df_local, len(coh)


@st.cache_data(ttl=60 * 60)
def faixa_por_mes(months: tuple[str, ...], cohort_ids: tuple[int, ...] | None = None) -> pd.DataFrame:
    """% da turma em cada faixa, mês a mês (linhas: mes; colunas: faixas)."""
    coh, df_local, total_local = _filter_by_ids(cohort_ids)
    rows = []
    for mes in months:
        per = aggregate_month(df_local, mes, cohort_df=coh)
        counts = faixa_counts(per)
        for f, n in counts.items():
            rows.append({
                "mes": mes,
                "mes_label": PRESCRIPTION_MONTHLY[mes]["label"],
                "faixa": f,
                "pct": n / total_local * 100 if total_local else 0,
                "n": n,
            })
    return pd.DataFrame(rows)


@st.cache_data(ttl=60 * 60)
def acerto_canonico_mensal(months: tuple[str, ...], cohort_ids: tuple[int, ...] | None = None) -> pd.DataFrame:
    """Mediana mensal de acerto canônico (entre alunos com mock canônico no mês)."""
    coh, df_local, _ = _filter_by_ids(cohort_ids)
    rows = []
    for mes in months:
        per = aggregate_month(df_local, mes, cohort_df=coh)
        com_mock = per[per["acerto_canonico_questao_count"] > 0]
        rows.append({
            "mes": mes,
            "mes_label": PRESCRIPTION_MONTHLY[mes]["label"],
            "mediana_turma": float(com_mock["acerto_canonico_pct"].median()) if len(com_mock) else None,
            "n_com_mock": len(com_mock),
            "prof_min": PRESCRIPTION_MONTHLY[mes]["prof_min"],
            "prof_max": PRESCRIPTION_MONTHLY[mes]["prof_max"],
            "excel_min": PRESCRIPTION_MONTHLY[mes]["excel_min"],
            "excel_max": PRESCRIPTION_MONTHLY[mes]["excel_max"],
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=60 * 60)
def safra_excelencia(ref_mes: str, cohort_ids: tuple[int, ...] | None = None) -> pd.DataFrame:
    """% em Excelência por safra de entrada (mês de first_start_date)."""
    coh_base, df_local, _ = _filter_by_ids(cohort_ids)
    per = aggregate_month(df_local, ref_mes, cohort_df=coh_base)
    coh = coh_base[["account_id", "first_start_date"]].copy()
    coh["first_start_date"] = pd.to_datetime(coh["first_start_date"])
    coh = coh.dropna(subset=["first_start_date"])
    coh["safra"] = assign_safra(coh["first_start_date"])
    merged = coh.merge(per[["account_id", "faixa"]], on="account_id", how="left")
    merged["faixa"] = merged["faixa"].fillna("Sem acerto canônico")
    grp = merged.groupby("safra").agg(
        total=("account_id", "count"),
        excelencia=("faixa", lambda s: (s == "Excelência").sum()),
        proficiencia=("faixa", lambda s: (s == "Proficiência").sum()),
        abaixo=("faixa", lambda s: (s == "Abaixo do canal").sum()),
        sem_mock=("faixa", lambda s: (s == "Sem acerto canônico").sum()),
    ).reset_index()
    grp["pct_excelencia"] = grp["excelencia"] / grp["total"] * 100
    grp["pct_proficiencia"] = grp["proficiencia"] / grp["total"] * 100
    grp["safra_label"] = grp["safra"].map(_format_mes)
    return grp.sort_values("safra")


Q1_SAFRAS = ("2026-01", "2026-02", "2026-03")


@st.cache_data(ttl=60 * 60)
def prescricao_vs_resultado_q1(ref_mes: str) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Matriz prescrição (volume) × resultado (faixa de acerto canônico).

    Restrita aos alunos que entraram no Q1/26 (first_start_date em jan-mar/26).
    - Linhas = prescrição que o aluno está SEGUINDO, derivada do volume B+Q+F
      acumulado do mês de entrada até ref_mes vs target acumulado mensal.
    - Colunas = faixa de acerto canônico no ref_mes (classe atual).

    Retorna (matrix_n, matrix_pct_row, total_alunos).
    """
    coh = cohort[["account_id", "first_start_date"]].copy()
    coh["first_start_date"] = pd.to_datetime(coh["first_start_date"])
    coh = coh.dropna(subset=["first_start_date"])
    coh["safra"] = assign_safra(coh["first_start_date"])
    coh = coh[coh["safra"].isin(Q1_SAFRAS)]
    if coh.empty:
        return pd.DataFrame(), pd.DataFrame(), 0

    weeks = df[["account_id", "semana_iso", "blocos", "questoes", "flashcards"]].copy()
    weeks["mes"] = weeks["semana_iso"].dt.to_period("M").astype(str)
    weeks = weeks[weeks["mes"] <= ref_mes]
    weeks["bqf"] = weeks["blocos"] + weeks["questoes"] + weeks["flashcards"]
    joined = weeks.merge(coh[["account_id", "safra"]], on="account_id", how="inner")
    joined = joined[joined["mes"] >= joined["safra"]]
    bqf_acum = joined.groupby("account_id", as_index=False)["bqf"].sum().rename(
        columns={"bqf": "bqf_acum"}
    )
    merged = coh.merge(bqf_acum, on="account_id", how="left")
    merged["bqf_acum"] = merged["bqf_acum"].fillna(0)

    def targets_for(entry_mes: str) -> tuple[float, float]:
        window = [m for m in PRESCRIPTION_ORDER if entry_mes <= m <= ref_mes]
        prof = sum(
            PRESCRIPTION_TARGETS[m]["blocos"][0]
            + PRESCRIPTION_TARGETS[m]["questoes"][0]
            + PRESCRIPTION_TARGETS[m]["flashcards"][0]
            for m in window
        )
        excel = sum(
            PRESCRIPTION_TARGETS[m]["blocos"][1]
            + PRESCRIPTION_TARGETS[m]["questoes"][1]
            + PRESCRIPTION_TARGETS[m]["flashcards"][1]
            for m in window
        )
        return float(prof), float(excel)

    target_map = {s: targets_for(s) for s in sorted(merged["safra"].unique())}
    merged["target_prof"] = merged["safra"].map(lambda s: target_map[s][0])
    merged["target_excel"] = merged["safra"].map(lambda s: target_map[s][1])

    def label_prescricao(row) -> str:
        if row["bqf_acum"] >= row["target_excel"]:
            return "Seguindo Excelência"
        if row["bqf_acum"] >= row["target_prof"]:
            return "Seguindo Proficiência"
        return "Abaixo de Proficiência"

    merged["prescricao"] = merged.apply(label_prescricao, axis=1)

    per = aggregate_month(df, ref_mes)
    merged = merged.merge(per[["account_id", "faixa"]], on="account_id", how="left")
    merged["faixa"] = merged["faixa"].fillna("Sem acerto canônico")

    row_order = ["Seguindo Excelência", "Seguindo Proficiência", "Abaixo de Proficiência"]
    col_order = PRESCRIPTION_CLASSES  # Excel, Profic, Abaixo, Sem mock
    matrix_n = pd.crosstab(merged["prescricao"], merged["faixa"]).reindex(
        index=row_order, columns=col_order, fill_value=0
    )
    row_totals = matrix_n.sum(axis=1).replace(0, pd.NA)
    matrix_pct = matrix_n.div(row_totals, axis=0) * 100
    matrix_pct = matrix_pct.fillna(0)
    return matrix_n, matrix_pct, len(merged)


@st.cache_data(ttl=60 * 60)
def lead_lag_prescricao_q1(
    months_closed_tuple: tuple[str, ...],
) -> tuple[pd.DataFrame, pd.DataFrame, int, list[tuple[str, str]]]:
    """Lead-lag: prescrição volumétrica em mês N (target MENSAL) × faixa de
    acerto canônico em mês N+2.

    Restrito a alunos Q1/26. Pares (N, N+2) construídos pulando 1 mês entre
    prescrição e resultado. Em cada par, um aluno só participa se já estava
    matriculado no mês N (safra <= N). Cada (aluno, par) é uma observação
    independente — alunos com mais meses fechados contribuem mais.

    Lag de 2 meses testa se a prescrição prediz resultado num horizonte mais
    longo, reduzindo viés de simultaneidade ainda mais que N+1. Custo: menos
    pares disponíveis (perde-se 1 par vs N+1).

    Retorna (matrix_n, matrix_pct_row, total_obs, pairs).
    """
    coh = cohort[["account_id", "first_start_date"]].copy()
    coh["first_start_date"] = pd.to_datetime(coh["first_start_date"])
    coh = coh.dropna(subset=["first_start_date"])
    coh["safra"] = assign_safra(coh["first_start_date"])
    coh = coh[coh["safra"].isin(Q1_SAFRAS)]
    if coh.empty:
        return pd.DataFrame(), pd.DataFrame(), 0, []

    pairs: list[tuple[str, str]] = []
    for i in range(len(months_closed_tuple) - 2):
        n = months_closed_tuple[i]
        n2 = months_closed_tuple[i + 2]
        if n in PRESCRIPTION_TARGETS and n2 in PRESCRIPTION_MONTHLY:
            pairs.append((n, n2))
    if not pairs:
        return pd.DataFrame(), pd.DataFrame(), 0, []

    def monthly_targets(mes: str) -> tuple[float, float]:
        t = PRESCRIPTION_TARGETS[mes]
        prof = t["blocos"][0] + t["questoes"][0] + t["flashcards"][0]
        excel = t["blocos"][1] + t["questoes"][1] + t["flashcards"][1]
        return float(prof), float(excel)

    chunks = []
    for n, n2 in pairs:
        coh_n = coh[coh["safra"] <= n][["account_id"]]
        if coh_n.empty:
            continue

        per_n = aggregate_month(df, n)
        per_n = per_n.merge(coh_n, on="account_id", how="inner")
        per_n["bqf"] = per_n["blocos"] + per_n["questoes"] + per_n["flashcards"]
        prof_t, excel_t = monthly_targets(n)

        def label_pres(b: float) -> str:
            if b >= excel_t:
                return "Seguiu Excelência"
            if b >= prof_t:
                return "Seguiu Proficiência"
            return "Abaixo de Proficiência"

        per_n["prescricao_n"] = per_n["bqf"].apply(label_pres)

        per_n2 = aggregate_month(df, n2)
        per_n2 = per_n2.merge(coh_n, on="account_id", how="inner")[
            ["account_id", "faixa"]
        ].rename(columns={"faixa": "faixa_n2"})

        merged = per_n[["account_id", "prescricao_n"]].merge(
            per_n2, on="account_id", how="left"
        )
        merged["faixa_n2"] = merged["faixa_n2"].fillna("Sem acerto canônico")
        merged["pair"] = f"{n}->{n2}"
        chunks.append(merged[["account_id", "prescricao_n", "faixa_n2", "pair"]])

    if not chunks:
        return pd.DataFrame(), pd.DataFrame(), 0, pairs

    all_obs = pd.concat(chunks, ignore_index=True)

    row_order = ["Seguiu Excelência", "Seguiu Proficiência", "Abaixo de Proficiência"]
    col_order = PRESCRIPTION_CLASSES
    matrix_n = pd.crosstab(all_obs["prescricao_n"], all_obs["faixa_n2"]).reindex(
        index=row_order, columns=col_order, fill_value=0
    )
    row_totals = matrix_n.sum(axis=1).replace(0, pd.NA)
    matrix_pct = (matrix_n.div(row_totals, axis=0) * 100).fillna(0)
    return matrix_n, matrix_pct, len(all_obs), pairs


DOSE_RESPONSE_DIMS = [
    ("questoes",   "Questões"),
    ("flashcards", "Flashcards"),
    ("blocos",     "Blocos de aula"),
    ("bqf",        "B+Q+F agregado"),
]


@st.cache_data(ttl=60 * 60)
def dose_response_obs_q1(
    months_closed_tuple: tuple[str, ...],
    condicionar_mock_n2: bool = True,
) -> pd.DataFrame:
    """Observações pra dose-response: volume em N → acerto canônico em N+2.

    Cada linha = (aluno, par N→N+2). Restrita a alunos Q1/26 já matriculados
    em N. Por default condicionada a ter mock em N+2 (responde "entre quem fez
    mock, qual dose maximiza chance de Excel?"). Pares pulam 1 mês (N+2).

    Colunas retornadas: account_id, mes_n, mes_n2, questoes, flashcards, blocos,
    bqf, pct_n2 (acerto canônico % em N+2), is_excel_n2 (vs canal recalibrado).
    """
    coh = cohort[["account_id", "first_start_date"]].copy()
    coh["first_start_date"] = pd.to_datetime(coh["first_start_date"])
    coh = coh.dropna(subset=["first_start_date"])
    coh["safra"] = assign_safra(coh["first_start_date"])
    coh = coh[coh["safra"].isin(Q1_SAFRAS)]
    if coh.empty:
        return pd.DataFrame()

    pairs: list[tuple[str, str]] = []
    for i in range(len(months_closed_tuple) - 2):
        n = months_closed_tuple[i]
        n2 = months_closed_tuple[i + 2]
        if n in PRESCRIPTION_MONTHLY and n2 in PRESCRIPTION_MONTHLY:
            pairs.append((n, n2))
    if not pairs:
        return pd.DataFrame()

    chunks = []
    for n, n2 in pairs:
        coh_n = coh[coh["safra"] <= n][["account_id"]]
        if coh_n.empty:
            continue
        per_n = aggregate_month(df, n).merge(coh_n, on="account_id", how="inner")
        per_n["bqf"] = per_n["blocos"] + per_n["questoes"] + per_n["flashcards"]
        per_n2 = aggregate_month(df, n2).merge(coh_n, on="account_id", how="inner")[
            ["account_id", "acerto_canonico_pct"]
        ].rename(columns={"acerto_canonico_pct": "pct_n2"})
        m = per_n[["account_id", "questoes", "flashcards", "blocos", "bqf"]].merge(
            per_n2, on="account_id", how="left"
        )
        m["mes_n"] = n
        m["mes_n2"] = n2
        chunks.append(m)

    if not chunks:
        return pd.DataFrame()
    obs = pd.concat(chunks, ignore_index=True)
    obs["excel_min_n2"] = obs["mes_n2"].map(lambda m: PRESCRIPTION_MONTHLY[m]["excel_min"])
    obs["is_excel_n2"] = obs["pct_n2"] >= obs["excel_min_n2"]
    if condicionar_mock_n2:
        obs = obs[obs["pct_n2"].notna()].reset_index(drop=True)
    return obs


def binned_dose_response(
    obs: pd.DataFrame, dim: str, n_bins: int = 8
) -> pd.DataFrame:
    """Para uma dimensão (coluna em obs), agrupa em quantis de volume e devolve:
    vol_p10/p50/p90 do bin, P(Excel canal 2026), P25/P50/P75 do pct_n2, n.

    Bins têm tamanho aproximadamente igual (qcut, com merge se duplicados).
    """
    sub = obs[obs["pct_n2"].notna()].copy()
    if sub.empty:
        return pd.DataFrame()
    sub["bin"] = pd.qcut(sub[dim], q=n_bins, duplicates="drop", labels=False)
    rows = []
    for b, bsub in sub.groupby("bin"):
        rows.append({
            "bin": int(b),
            "vol_p10": float(bsub[dim].quantile(0.10)),
            "vol_p50": float(bsub[dim].median()),
            "vol_p90": float(bsub[dim].quantile(0.90)),
            "vol_min": float(bsub[dim].min()),
            "vol_max": float(bsub[dim].max()),
            "pct_excel": float(bsub["is_excel_n2"].mean()) * 100,
            "canonico_p25": float(bsub["pct_n2"].quantile(0.25)),
            "canonico_p50": float(bsub["pct_n2"].median()),
            "canonico_p75": float(bsub["pct_n2"].quantile(0.75)),
            "n": int(len(bsub)),
        })
    return pd.DataFrame(rows).sort_values("vol_p50").reset_index(drop=True)


def safra_volume(ref_mes: str, cohort_ids: tuple[int, ...] | None = None) -> pd.DataFrame:
    """% da safra com B+Q+F acumulado ≥ target Excel/Profic acumulado.

    Janela de acúmulo: do mês de first_start_date até ref_mes (inclusive).
    Target acumulado: soma dos targets mensais (blocos+questões+flashcards)
    nos mesmos meses da janela. Classes mutuamente exclusivas.
    """
    coh_base, df_local, _ = _filter_by_ids(cohort_ids)
    coh = coh_base[["account_id", "first_start_date"]].copy()
    coh["first_start_date"] = pd.to_datetime(coh["first_start_date"])
    coh = coh.dropna(subset=["first_start_date"])
    coh["safra"] = assign_safra(coh["first_start_date"])
    coh = coh[coh["safra"] <= ref_mes]

    weeks = df_local[["account_id", "semana_iso", "blocos", "questoes", "flashcards"]].copy()
    weeks["mes"] = weeks["semana_iso"].dt.to_period("M").astype(str)
    weeks = weeks[weeks["mes"] <= ref_mes]
    weeks["bqf"] = weeks["blocos"] + weeks["questoes"] + weeks["flashcards"]

    joined = weeks.merge(coh[["account_id", "safra"]], on="account_id", how="inner")
    joined = joined[joined["mes"] >= joined["safra"]]
    bqf_acum = joined.groupby("account_id", as_index=False)["bqf"].sum().rename(
        columns={"bqf": "bqf_acum"}
    )

    merged = coh.merge(bqf_acum, on="account_id", how="left")
    merged["bqf_acum"] = merged["bqf_acum"].fillna(0)

    def targets_for(entry_mes: str) -> tuple[float, float]:
        window = [m for m in PRESCRIPTION_ORDER if entry_mes <= m <= ref_mes]
        prof = sum(
            PRESCRIPTION_TARGETS[m]["blocos"][0]
            + PRESCRIPTION_TARGETS[m]["questoes"][0]
            + PRESCRIPTION_TARGETS[m]["flashcards"][0]
            for m in window
        )
        excel = sum(
            PRESCRIPTION_TARGETS[m]["blocos"][1]
            + PRESCRIPTION_TARGETS[m]["questoes"][1]
            + PRESCRIPTION_TARGETS[m]["flashcards"][1]
            for m in window
        )
        return float(prof), float(excel)

    safras = sorted(merged["safra"].unique())
    target_map = {s: targets_for(s) for s in safras}
    merged["target_prof"] = merged["safra"].map(lambda s: target_map[s][0])
    merged["target_excel"] = merged["safra"].map(lambda s: target_map[s][1])
    merged["is_excel"] = merged["bqf_acum"] >= merged["target_excel"]
    merged["is_prof"] = (
        (merged["bqf_acum"] >= merged["target_prof"]) & (~merged["is_excel"])
    )

    grp = merged.groupby("safra").agg(
        total=("account_id", "count"),
        excel=("is_excel", "sum"),
        prof=("is_prof", "sum"),
        mediana_bqf=("bqf_acum", "median"),
    ).reset_index()
    grp["pct_excel"] = grp["excel"] / grp["total"] * 100
    grp["pct_prof"] = grp["prof"] / grp["total"] * 100
    grp["target_prof"] = grp["safra"].map(lambda s: target_map[s][0])
    grp["target_excel"] = grp["safra"].map(lambda s: target_map[s][1])
    grp["safra_label"] = grp["safra"].map(_format_mes)
    return grp.sort_values("safra")


@st.cache_data(ttl=60 * 60)
def alunos_diagnostico(ref_mes: str, prev_mes: str | None) -> dict[str, pd.DataFrame]:
    """3 listas acionáveis de alunos comparando ref_mes vs prev_mes.

    - quase_excel: Proficiência no mês com volume B+Q+F ≥ 75% target Excel
    - em_risco: Excelência no mês mas dias_ativos caiu ≥50% vs mês anterior
    - sumidos: dias_ativos_anterior ≥ 10 e dias_ativos_atual ≤ 3
    """
    cur = aggregate_month(df, ref_mes)
    cur["bqf"] = cur["blocos"] + cur["questoes"] + cur["flashcards"]
    cur_w = cur.merge(
        cohort[["account_id", "name", "email"]], on="account_id", how="left"
    )

    # --- Quase Excelência ---
    ex_b = PRESCRIPTION_TARGETS[ref_mes]["blocos"][1]
    ex_q = PRESCRIPTION_TARGETS[ref_mes]["questoes"][1]
    ex_f = PRESCRIPTION_TARGETS[ref_mes]["flashcards"][1]
    excel_bqf_target = ex_b + ex_q + ex_f
    quase = cur_w[
        (cur_w["faixa"] == "Proficiência")
        & (cur_w["bqf"] >= 0.75 * excel_bqf_target)
    ].copy()
    quase = quase[[
        "account_id", "name", "email", "acerto_canonico_pct",
        "dias_ativos", "questoes", "flashcards", "blocos", "bqf",
    ]].sort_values("acerto_canonico_pct", ascending=False)

    if prev_mes is None:
        return {"quase": quase, "em_risco": pd.DataFrame(), "sumidos": pd.DataFrame()}

    prev = aggregate_month(df, prev_mes)
    prev["bqf"] = prev["blocos"] + prev["questoes"] + prev["flashcards"]
    merged = cur_w.merge(
        prev[["account_id", "dias_ativos", "bqf", "faixa", "acerto_canonico_pct"]].rename(
            columns={
                "dias_ativos": "dias_prev",
                "bqf": "bqf_prev",
                "faixa": "faixa_prev",
                "acerto_canonico_pct": "ac_prev",
            }
        ),
        on="account_id", how="left",
    )

    # --- Em risco ---
    em_risco = merged[
        (merged["faixa"] == "Excelência")
        & (merged["dias_prev"].fillna(0) >= 5)
        & (merged["dias_ativos"] < 0.5 * merged["dias_prev"].fillna(0))
    ].copy()
    em_risco["queda_dias_pct"] = (
        (em_risco["dias_ativos"] - em_risco["dias_prev"])
        / em_risco["dias_prev"] * 100
    )
    em_risco = em_risco[[
        "account_id", "name", "email", "acerto_canonico_pct",
        "dias_prev", "dias_ativos", "queda_dias_pct", "bqf_prev", "bqf",
    ]].sort_values("queda_dias_pct")

    # --- Sumidos ---
    sumidos = merged[
        (merged["dias_prev"].fillna(0) >= 10)
        & (merged["dias_ativos"] <= 3)
    ].copy()
    sumidos = sumidos[[
        "account_id", "name", "email",
        "dias_prev", "dias_ativos",
        "faixa_prev", "faixa", "ac_prev", "acerto_canonico_pct",
    ]].sort_values("dias_prev", ascending=False)

    return {"quase": quase, "em_risco": em_risco, "sumidos": sumidos}


with tab_evolucao:
    st.caption("Selecione 1 ou mais turmas:")
    _evo_turmas = st.multiselect(
        "Turmas",
        _TURMAS_DISPONIVEIS,
        default=_TURMAS_DISPONIVEIS,
        key="evo_turmas_multi",
        label_visibility="collapsed",
    )
    if not _evo_turmas:
        st.info("Selecione ao menos uma turma.")
        st.stop()

    _evo_cohort_sel = cohort_turma[cohort_turma["turma"].isin(_evo_turmas)]
    _evo_cohort = _evo_cohort_sel.drop_duplicates("account_id", keep="first")
    _evo_ids_tuple = tuple(sorted(int(a) for a in _evo_cohort["account_id"].unique()))
    _evo_total = len(_evo_ids_tuple)
    if _evo_total == 0:
        st.info("Nenhum aluno nas turmas selecionadas.")
        st.stop()
    # Usa cache global (cohort_ids=None) quando todas as turmas estão marcadas
    _evo_filter = _evo_ids_tuple
    _evo_df = df[df["account_id"].isin(set(_evo_ids_tuple))]
    _evo_cur_per = aggregate_month(_evo_df, last_closed, cohort_df=_evo_cohort)
    if len(_evo_turmas) == len(_TURMAS_DISPONIVEIS):
        _evo_label = "Todas as turmas"
    elif len(_evo_turmas) == 1:
        _evo_label = _evo_turmas[0]
    else:
        _evo_label = f"{len(_evo_turmas)} turmas selecionadas"
    st.markdown(
        f"<div style='margin-top:-8px;margin-bottom:14px;color:#71717a;font-size:13px'>"
        f"<b>{_evo_label}</b> · {_evo_total:,} alunos</div>",
        unsafe_allow_html=True,
    )

    st.markdown("##### Trajetória das faixas — % da turma em cada faixa")
    st.caption("Mais verde = mais Excelência. Mais cinza = menos engajamento no mock.")

    meses_tuple = tuple(months_closed)
    faixa_df = faixa_por_mes(meses_tuple, _evo_filter)
    # Excelência fica na base pra a faixa que mais importa ser contínua e legível;
    # Sem acerto canônico (sempre dominante) vai no topo, ocupando o espaço residual.
    stack_order = ["Excelência", "Proficiência", "Abaixo do canal", "Sem acerto canônico"]
    pivot = faixa_df.pivot(index="mes_label", columns="faixa", values="pct").reindex(
        columns=stack_order
    )
    pivot = pivot.reindex([PRESCRIPTION_MONTHLY[m]["label"] for m in months_closed])

    fig_area = go.Figure()
    for f in stack_order:
        fig_area.add_trace(go.Scatter(
            x=pivot.index, y=pivot[f], name=f,
            mode="lines", stackgroup="one", groupnorm="percent",
            line=dict(width=0.5, color=PRESCRIPTION_COLORS[f]),
            fillcolor=PRESCRIPTION_COLORS[f],
            hovertemplate=f"<b>{f}</b><br>%{{y:.1f}}%<extra></extra>",
        ))
    fig_area.update_layout(
        height=380, plot_bgcolor="#fff", paper_bgcolor="#fff",
        margin=dict(l=10, r=10, t=10, b=10),
        yaxis=dict(title="% da turma", ticksuffix="%", range=[0, 100]),
        xaxis=dict(title="Mês"),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25),
        hovermode="x unified",
    )
    st.plotly_chart(fig_area, use_container_width=True)

    # --- Bloco 2: linha de acerto canônico mediano vs canais 2026 recalibrados ---
    st.markdown("&nbsp;")
    st.markdown("##### Mediana de acerto da turma vs alvos v3")
    st.caption("Linha = mediana de quem fez mock. Bandas = alvos v4 (Excel/Profic).")

    ac_df = acerto_canonico_mensal(meses_tuple, _evo_filter)
    fig_line = go.Figure()
    # Bandas (canais) — desenha cada uma como duas linhas com fill='tonexty'
    fig_line.add_trace(go.Scatter(
        x=ac_df["mes_label"], y=ac_df["excel_max"], mode="lines",
        line=dict(width=0, color="#05FC89"), showlegend=False, hoverinfo="skip",
    ))
    fig_line.add_trace(go.Scatter(
        x=ac_df["mes_label"], y=ac_df["excel_min"], mode="lines",
        line=dict(width=0, color="#05FC89"),
        fill="tonexty", fillcolor="rgba(5,252,137,.55)",
        name="Canal Excelência v2 (editorial)", hovertemplate="Excel %{y:.0f}%–<extra></extra>",
    ))
    fig_line.add_trace(go.Scatter(
        x=ac_df["mes_label"], y=ac_df["prof_max"], mode="lines",
        line=dict(width=0, color="#32578A"), showlegend=False, hoverinfo="skip",
    ))
    fig_line.add_trace(go.Scatter(
        x=ac_df["mes_label"], y=ac_df["prof_min"], mode="lines",
        line=dict(width=0, color="#32578A"),
        fill="tonexty", fillcolor="rgba(50,87,138,.50)",
        name="Canal Proficiência v1 (2025; v2-Profic pendente)",
    ))
    fig_line.add_trace(go.Scatter(
        x=ac_df["mes_label"], y=ac_df["mediana_turma"],
        mode="lines+markers+text",
        line=dict(color="#181717", width=3),
        marker=dict(size=9, color="#181717"),
        name="Mediana turma R1 2026",
        text=[
            f"n={n}/{_evo_total}<br>({n/_evo_total*100:.0f}%)"
            for n in ac_df["n_com_mock"]
        ],
        textposition="top center",
        textfont=dict(size=10, color="#181717"),
        hovertemplate=(
            "<b>Mediana turma</b>: %{y:.1f}%<br>"
            "%{text}<extra></extra>"
        ),
    ))
    fig_line.update_layout(
        height=420, plot_bgcolor="#fff", paper_bgcolor="#fff",
        margin=dict(l=10, r=10, t=10, b=10),
        yaxis=dict(title="% acerto canônico", ticksuffix="%", range=[20, 90]),
        xaxis=dict(title="Mês"),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25),
        hovermode="x unified",
    )
    st.plotly_chart(fig_line, use_container_width=True)

    # --- Bloco 3: % em Excelência por safra de entrada ---
    st.markdown("&nbsp;")
    st.markdown(f"##### % em Excelência por safra de entrada — referência {PRESCRIPTION_MONTHLY[last_closed]['label']}")
    st.caption(
        "Cada barra = mês de matrícula. **jan/26** inclui veteranos pré-2026. "
        "Safras antigas tendem a ter mais Excelência."
    )

    safra_df = safra_excelencia(last_closed, _evo_filter)
    if safra_df.empty:
        st.info("Sem dados de safra disponíveis.")
    else:
        fig_safra = go.Figure()
        fig_safra.add_trace(go.Bar(
            x=safra_df["safra_label"], y=safra_df["pct_excelencia"],
            marker_color=PRESCRIPTION_COLORS["Excelência"],
            name="% Excelência",
            text=[f"{p:.0f}%<br>n={t}" for p, t in zip(safra_df["pct_excelencia"], safra_df["total"])],
            textposition="outside",
            hovertemplate="Safra %{x}<br>%{y:.1f}% em Excelência<br>%{text}<extra></extra>",
        ))
        fig_safra.add_trace(go.Bar(
            x=safra_df["safra_label"], y=safra_df["pct_proficiencia"],
            marker_color=PRESCRIPTION_COLORS["Proficiência"],
            name="% Proficiência",
            hovertemplate="Safra %{x}<br>%{y:.1f}% em Proficiência<extra></extra>",
        ))
        fig_safra.update_layout(
            height=380, plot_bgcolor="#fff", paper_bgcolor="#fff",
            margin=dict(l=10, r=10, t=30, b=10),
            barmode="group",
            yaxis=dict(title="% da safra", ticksuffix="%"),
            xaxis=dict(title="Mês de entrada"),
            legend=dict(orientation="h", yanchor="bottom", y=-0.25),
        )
        st.plotly_chart(fig_safra, use_container_width=True)

    # --- Bloco 3b: % atingindo target de volume B+Q+F acumulado por safra ---
    st.markdown("&nbsp;")
    st.markdown(
        f"##### % atingindo target de volume B+Q+F acumulado por safra — referência {PRESCRIPTION_MONTHLY[last_closed]['label']}"
    )
    st.caption("Volume = blocos+questões+flashcards acumulados. Verde = ≥alvo Excel · Azul = ≥alvo Profic.")

    vol_df = safra_volume(last_closed, _evo_filter)
    if vol_df.empty:
        st.info("Sem dados de safra disponíveis.")
    else:
        fig_vol = go.Figure()
        fig_vol.add_trace(go.Bar(
            x=vol_df["safra_label"], y=vol_df["pct_excel"],
            marker_color=PRESCRIPTION_COLORS["Excelência"],
            name="% atingindo Excel",
            text=[f"{p:.0f}%<br>n={t}" for p, t in zip(vol_df["pct_excel"], vol_df["total"])],
            textposition="outside",
            customdata=list(zip(vol_df["total"], vol_df["target_excel"], vol_df["mediana_bqf"])),
            hovertemplate=(
                "Safra %{x}<br>"
                "%{y:.1f}% ≥ target Excel<br>"
                "Target Excel acum: %{customdata[1]:,.0f}<br>"
                "Mediana B+Q+F acum: %{customdata[2]:,.0f}<br>"
                "n=%{customdata[0]}<extra></extra>"
            ),
        ))
        fig_vol.add_trace(go.Bar(
            x=vol_df["safra_label"], y=vol_df["pct_prof"],
            marker_color=PRESCRIPTION_COLORS["Proficiência"],
            name="% entre Profic e Excel",
            customdata=list(zip(vol_df["total"], vol_df["target_prof"], vol_df["mediana_bqf"])),
            hovertemplate=(
                "Safra %{x}<br>"
                "%{y:.1f}% entre Profic e Excel<br>"
                "Target Profic acum: %{customdata[1]:,.0f}<br>"
                "Mediana B+Q+F acum: %{customdata[2]:,.0f}<br>"
                "n=%{customdata[0]}<extra></extra>"
            ),
        ))
        fig_vol.update_layout(
            height=380, plot_bgcolor="#fff", paper_bgcolor="#fff",
            margin=dict(l=10, r=10, t=30, b=10),
            barmode="group",
            yaxis=dict(title="% da safra", ticksuffix="%"),
            xaxis=dict(title="Mês de entrada"),
            legend=dict(orientation="h", yanchor="bottom", y=-0.25),
        )
        st.plotly_chart(fig_vol, use_container_width=True)

    # --- Bloco 5: métricas avançadas (collapsible) ---
    st.markdown("&nbsp;")
    st.markdown("##### Métricas semanais avançadas")
    st.caption(
        f"Excel = alunos em Excelência em {cur_mes_label}. Geral = turma toda. "
        "Agregação semanal sobre alunos ativos."
    )

    # Métricas: nome → (coluna no df, tipo de cálculo)
    # tipo: 'vol' = soma por aluno-semana → agg sobre alunos ativos
    #       'ratio_can' = acerto canônico % = ac_acertos / ac_qcount
    #       'ratio_simp' = acerto simples % = acertos_simples / respostas_simples
    METRICAS_AV = {
        "Blocos de aula":             ("blocos",       "vol"),
        "Questões realizadas":        ("questoes",     "vol"),
        "Flashcards realizados":      ("flashcards",   "vol"),
        "Simulados canônicos":        ("sim_template", "vol"),
        "Simulados revisão":          ("sim_revision", "vol"),
        "Simulados fixação":          ("sim_fixation", "vol"),
        "% acerto canônico":          (None,           "ratio_can"),
        "% acerto simples":           (None,           "ratio_simp"),
    }

    excel_ids = set(_evo_cur_per[_evo_cur_per["faixa"] == "Excelência"]["account_id"].astype(int).tolist())

    def _render_avancado(account_ids_filtro: set | None, key_prefix: str, label_grupo: str):
        df_sub = _evo_df[_evo_df["account_id"].isin(account_ids_filtro)] if account_ids_filtro is not None else _evo_df
        n_alunos = df_sub["account_id"].nunique() if account_ids_filtro is not None else _evo_total

        ccol_agg, ccol_metric = st.columns([1, 3])
        with ccol_agg:
            agg_choice = st.radio(
                "Agregação",
                ["Mediana", "Média"],
                key=f"{key_prefix}_agg",
                horizontal=False,
            )
        with ccol_metric:
            selected = st.multiselect(
                "Métricas",
                list(METRICAS_AV.keys()),
                default=["Questões realizadas"],
                key=f"{key_prefix}_metricas",
            )

        if not selected:
            st.info("Selecione ao menos uma métrica.")
            return

        # Início fixo: semana que contém 11/01/26 (segunda 05/01/26) — começo
        # do extensivo R1 2026. Descarta dados pré-início (ex.: 2025-12-29).
        df_sub = df_sub[df_sub["semana_iso"] >= pd.Timestamp("2026-01-05")]

        # Cutoff última semana parcial — descarta se volume < 30% da média das 3 anteriores
        weekly_vol = df_sub.groupby("semana_iso")["questoes"].sum().sort_index()
        cutoff_week = None
        if len(weekly_vol) >= 4:
            last_vol = weekly_vol.iloc[-1]
            prev_avg = weekly_vol.iloc[-4:-1].mean()
            if prev_avg > 0 and last_vol < prev_avg * 0.3:
                cutoff_week = weekly_vol.index[-1]
        df_closed = df_sub[df_sub["semana_iso"] < cutoff_week] if cutoff_week is not None else df_sub
        df_ativos = df_closed[df_closed["dias_ativos"] > 0]

        agg_fn = "median" if agg_choice == "Mediana" else "mean"

        plots = []
        for label in selected:
            col, tipo = METRICAS_AV[label]
            if tipo == "vol":
                serie = df_ativos.groupby("semana_iso")[col].agg(agg_fn).reset_index()
                serie.columns = ["semana_iso", "valor"]
            else:
                # % de acerto: calcula pct por aluno-semana, depois agrega
                if tipo == "ratio_can":
                    num, den = "acerto_canonico_acertos", "acerto_canonico_questao_count"
                else:
                    num, den = "acertos_simples", "respostas_simples"
                com_resposta = df_closed[df_closed[den] > 0].copy()
                com_resposta["pct"] = com_resposta[num] / com_resposta[den] * 100
                serie = com_resposta.groupby("semana_iso")["pct"].agg(agg_fn).reset_index()
                serie.columns = ["semana_iso", "valor"]
            serie["metrica"] = label
            plots.append(serie)

        long = pd.concat(plots, ignore_index=True)
        fig = px.line(
            long, x="semana_iso", y="valor", color="metrica", markers=True,
            labels={"semana_iso": "Semana ISO (segunda)", "valor": "Valor"},
        )
        fig.update_layout(
            height=420, plot_bgcolor="#fff", paper_bgcolor="#fff",
            margin=dict(l=10, r=10, t=10, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=-0.25),
        )
        st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_chart")
        st.caption(f"{label_grupo}: {n_alunos:,} alunos.")

    sub_excel, sub_geral = st.tabs(["Excel", "Geral"])
    with sub_excel:
        _render_avancado(
            excel_ids if excel_ids else None,
            key_prefix="av_excel",
            label_grupo=f"Excelência em {cur_mes_label}",
        )
    with sub_geral:
        _render_avancado(None, key_prefix="av_geral", label_grupo="Turma toda")


# ============================================================
# ABA QUALIDADE — avaliações de conteúdo (aulas + questões)
# ============================================================

with tab_qualidade:
    st.markdown("##### Qualidade do conteúdo")
    st.caption("Notas 1-5 dadas pelos alunos em aulas e comentários de questões (jan/26 → hoje).")

    _acc_ids = tuple(sorted(int(a) for a in cohort["account_id"].tolist()))
    try:
        aulas = load_avaliacoes_aulas(_acc_ids)
        questoes = load_avaliacoes_questoes(_acc_ids)
        materiais = load_avaliacoes_materiais(_acc_ids)
        flashcards = load_avaliacoes_flashcards(_acc_ids)
    except Exception as e:
        st.error(f"Falha ao carregar avaliações do Aurora: {e}")
        st.stop()
    ebook = materiais[materiais["tipo_material"] == "EBOOK"].drop(columns=["tipo_material"])
    resumo = materiais[materiais["tipo_material"] == "SUMMARY"].drop(columns=["tipo_material"])
    mapa = materiais[materiais["tipo_material"] == "MIND_MAP"].drop(columns=["tipo_material"])

    def _agg_area(d: pd.DataFrame) -> dict[int, dict]:
        """Por big_area_id retorna {avg, n, n_alunos}."""
        out: dict[int, dict] = {}
        for bid, sub in d.groupby("big_area_id"):
            out[int(bid)] = {
                "avg": float(sub["rate"].mean()),
                "n": int(len(sub)),
                "n_alunos": int(sub["account_id"].nunique()),
            }
        return out

    def _render_qualidade_table(d: pd.DataFrame, label: str) -> str:
        if d.empty:
            return f"<p style='color:#71717a'>Sem avaliações de {label.lower()} no período.</p>"
        # Geral primeiro
        geral_avg = float(d["rate"].mean())
        geral_n = int(len(d))
        geral_alunos = int(d["account_id"].nunique())
        por_area = _agg_area(d)
        cells = []
        # Geral
        cells.append(
            f"<td class='val' style='font-weight:600;background:#FFFAE0'>"
            f"<span style='font-size:18px;color:#181717'>★ {geral_avg:.2f}</span>"
            f"<br><span style='font-size:11px;color:#71717a'>n={geral_n:,} · {geral_alunos:,} alunos</span></td>"
        )
        for sigla, bid in BIG_AREAS_ORDER:
            info = por_area.get(bid)
            if info is None:
                cells.append(
                    f"<td class='val' style='color:#B8B8B8'>—<br><span style='font-size:11px'>sem dados</span></td>"
                )
            else:
                # Cor sutil: 4.5+ verde, 4.0-4.5 amarelo, <4 vermelho
                cor = "#04A36A" if info["avg"] >= 4.5 else ("#EAB904" if info["avg"] >= 4.0 else "#E64444")
                cells.append(
                    f"<td class='val'>"
                    f"<span style='font-size:16px;color:{cor};font-weight:600'>★ {info['avg']:.2f}</span>"
                    f"<br><span style='font-size:11px;color:#71717a'>n={info['n']:,}</span></td>"
                )
        return (
            f"<tr><td class='dim'>{label}</td>{''.join(cells)}</tr>"
        )

    # Header da tabela combinada
    sigla_to_nome = {sigla: BIG_AREAS_NOMES[bid] for sigla, bid in BIG_AREAS_ORDER}
    header_cols = (
        "<th style='text-align:right;background:#FFFAE0'>Geral</th>"
        + "".join(
            f"<th style='text-align:right' title='{sigla_to_nome[sigla]}'>{sigla}</th>"
            for sigla, _ in BIG_AREAS_ORDER
        )
    )
    st.markdown(
        f"<table class='gap-table'>"
        f"<thead><tr><th>Conteúdo</th>{header_cols}</tr></thead>"
        f"<tbody>"
        f"{_render_qualidade_table(aulas, 'Aulas')}"
        f"{_render_qualidade_table(questoes, 'Comentários de questões')}"
        f"{_render_qualidade_table(ebook, 'E-book')}"
        f"{_render_qualidade_table(resumo, 'Resumo')}"
        f"{_render_qualidade_table(mapa, 'Mapa mental')}"
        f"{_render_qualidade_table(flashcards, 'Flashcards')}"
        f"</tbody></table>",
        unsafe_allow_html=True,
    )
    st.caption("Cores: 🟢 ≥4,5 · 🟡 4,0-4,5 · 🔴 <4,0 · n = avaliações.")

    # --- Evolução mensal ---
    st.markdown("&nbsp;")
    st.markdown("##### Evolução mensal")
    st.caption(
        "Até {}. Mês a mês = só o mês. Acumulado = desde jan/26.".format(
            PRESCRIPTION_MONTHLY[last_closed]["label"])
    )

    _col_modo, _col_metricas = st.columns([1, 2])
    with _col_modo:
        modo_evol = st.radio(
            "Modo",
            ["Mês a mês (isolado)", "Acumulado desde jan/26"],
            key="qualidade_modo",
        )
    _CONTEUDOS_QUAL = ["Aulas", "Comentários de questões", "E-book", "Resumo", "Mapa mental", "Flashcards"]
    with _col_metricas:
        conteudos_sel = st.multiselect(
            "Conteúdos",
            _CONTEUDOS_QUAL,
            default=_CONTEUDOS_QUAL,
            key="qualidade_conteudos_multi",
        )
    acumulado = modo_evol.startswith("Acumulado")
    if not conteudos_sel:
        st.info("Selecione ao menos um conteúdo.")
        st.stop()

    def _serie_mensal(d: pd.DataFrame, label: str) -> pd.DataFrame:
        if d.empty:
            return pd.DataFrame(columns=["mes", "valor", "n", "metrica"])
        sub = d.copy()
        sub["mes"] = pd.to_datetime(sub["mes"])
        ref_end = pd.Timestamp(f"{last_closed}-01") + pd.offsets.MonthEnd(0)
        sub = sub[sub["mes"] <= ref_end]
        if sub.empty:
            return pd.DataFrame(columns=["mes", "valor", "n", "metrica"])
        sub = sub.sort_values("mes")
        meses_sorted = sorted(sub["mes"].unique())
        rows = []
        for m in meses_sorted:
            if acumulado:
                slice_ = sub[sub["mes"] <= m]
            else:
                slice_ = sub[sub["mes"] == m]
            if slice_.empty:
                continue
            rows.append({
                "mes": pd.Timestamp(m),
                "valor": float(slice_["rate"].mean()),
                "n": int(len(slice_)),
                "metrica": label,
            })
        return pd.DataFrame(rows)

    _SOURCE_QUAL = {
        "Aulas": aulas,
        "Comentários de questões": questoes,
        "E-book": ebook,
        "Resumo": resumo,
        "Mapa mental": mapa,
        "Flashcards": flashcards,
    }
    series = pd.concat(
        [_serie_mensal(_SOURCE_QUAL[label], label) for label in conteudos_sel],
        ignore_index=True,
    )

    if series.empty:
        st.info("Sem dados de avaliação no período.")
    else:
        fig = px.line(
            series, x="mes", y="valor", color="metrica", markers=True,
            labels={"mes": "Mês", "valor": "Média ★", "metrica": "Conteúdo"},
            color_discrete_map={
                "Aulas": "#32578A",
                "Comentários de questões": "#841A81",
                "E-book": "#04A36A",
                "Resumo": "#EAB904",
                "Mapa mental": "#FF7F50",
                "Flashcards": "#6CE190",
            },
        )
        # Anotação do n em cada ponto
        for _, row in series.iterrows():
            fig.add_annotation(
                x=row["mes"], y=row["valor"],
                text=f"n={row['n']}",
                showarrow=False, yshift=12,
                font=dict(size=10, color="#71717a"),
            )
        fig.update_layout(
            height=420,
            plot_bgcolor="#fff", paper_bgcolor="#fff",
            margin=dict(l=10, r=10, t=10, b=10),
            yaxis=dict(range=[1, 5], dtick=0.5),
            xaxis=dict(tickformat="%b/%y"),
            legend=dict(orientation="h", yanchor="bottom", y=-0.25),
        )
        st.plotly_chart(fig, use_container_width=True)


