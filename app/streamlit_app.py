"""
Dashboard v2 — abas "Agora" + "Evolução".

Roda em paralelo ao streamlit_app.py, usando o mesmo parquet e gate de senha.
Foco: responder em 30s "onde a turma está hoje e pra onde está indo".
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
import requests
import streamlit as st

try:
    from prescription_v2 import (
        EXCEL_VOLUME_PROFILE,
        PRESCRIPTION_MONTHLY,
        PRESCRIPTION_ORDER,
        PRESCRIPTION_TARGETS,
        PRESCRIPTION_VERSION,
    )
    from snapshot_utils import (
        activity_month_labels,
        deduplicate_accounts,
        deduplicate_account_metrics,
        detect_atypical_collection,
        eligible_cohort_for_month,
        weekly_student_ratio,
    )
except ModuleNotFoundError:
    from app.prescription_v2 import (
        EXCEL_VOLUME_PROFILE,
        PRESCRIPTION_MONTHLY,
        PRESCRIPTION_ORDER,
        PRESCRIPTION_TARGETS,
        PRESCRIPTION_VERSION,
    )
    from app.snapshot_utils import (
        activity_month_labels,
        deduplicate_accounts,
        deduplicate_account_metrics,
        detect_atypical_collection,
        eligible_cohort_for_month,
        weekly_student_ratio,
    )

# Paleta canônica — Brandbook EMR 2026, p.102. Espelha as CSS vars do bloco de
# estilo abaixo; se mudar aqui, mude lá. Nomes são os do brandbook.
EMR = {
    "green":     "#264641",  # Residente Green — base institucional
    "approved":  "#6CE190",  # Residente Approved
    "lime":      "#B4F900",  # Residente Lime
    "off_white": "#F8F8F8",  # Residente Off White
    "orange":    "#FF7013",  # Residente Orange
    # complementares — o brandbook as libera para sistemas web, teto de 30%
    "blue":      "#50BCFF",
    "red":       "#FF514D",
    "purple":    "#9500DB",
    "yellow":    "#FFC805",
    "green_deep":"#009B3F",
    # derivados do shell dark (não constam no brandbook)
    "surface":   "#1A302C",
    "muted":     "#93A8A2",
    "neutral":   "#8FA39D",
}

# Template global dos gráficos. colorway = primárias de destaque + complementares,
# que é exatamente o uso que o brandbook prevê para elas ("sistemas web").
_emr_dark = go.layout.Template(pio.templates["plotly_white"])
_emr_dark.layout.update(
    font=dict(family="Outfit, sans-serif", color=EMR["off_white"]),
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    xaxis=dict(gridcolor="rgba(248,248,248,.08)", zerolinecolor="rgba(248,248,248,.15)"),
    yaxis=dict(gridcolor="rgba(248,248,248,.08)", zerolinecolor="rgba(248,248,248,.15)"),
    legend=dict(font=dict(color=EMR["off_white"])),
    hoverlabel=dict(bgcolor=EMR["surface"], font=dict(color=EMR["off_white"])),
    colorway=[EMR["approved"], EMR["lime"], EMR["blue"], EMR["yellow"],
              EMR["red"], EMR["purple"], EMR["green_deep"], EMR["orange"]],
)
pio.templates["emr_dark"] = _emr_dark
pio.templates.default = "emr_dark"

# App público: NÃO conecta no Aurora em runtime. Os parquets anonimizados (gerados
# 1x/dia pelo ETL) vivem num GitHub Release PRIVADO; o app os baixa em runtime com um
# token só-leitura (st.secrets["DATA_TOKEN"]). Assim o repositório do app pode ser
# público (sem nenhum dado) e o acesso é controlado só pela senha. Em dev local (sem
# DATA_TOKEN), lê os parquets do disco em snapshots/.

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "snapshots"
SNAPSHOTS_B2B_DIR = SNAPSHOTS / "b2b"

# Repo privado e tag do release que hospeda os parquets.
DATA_REPO = "brunokosminsky-svg/emr-dashboard-2026"
DATA_TAG = "latest-data"

st.set_page_config(page_title="Dashboard EMR · R1 2026", layout="wide", initial_sidebar_state="collapsed")


def _require_password() -> None:
    """Gate de senha única compartilhada. A senha (hash bcrypt) vive em
    st.secrets["APP_PASSWORD_HASH"] no Streamlit Cloud — nunca no repositório.
    Sem o secret, falha fechado. Desenvolvimento sem senha exige a opção local
    explícita ALLOW_INSECURE_LOCAL=true."""
    try:
        pw_hash = st.secrets.get("APP_PASSWORD_HASH")
    except Exception:
        pw_hash = None
    if not pw_hash:
        allow_local = os.getenv("ALLOW_INSECURE_LOCAL", "").strip().lower()
        if allow_local in {"1", "true", "yes"}:
            st.warning("Modo local sem senha habilitado por ALLOW_INSECURE_LOCAL.")
            return
        st.error("Configuração inválida: APP_PASSWORD_HASH não foi definido.")
        st.stop()
    if st.session_state.get("_authed"):
        return
    import bcrypt
    st.markdown("#### Dashboard EMR · R1 2026")
    with st.form("login"):
        senha = st.text_input("Senha de acesso", type="password")
        entrar = st.form_submit_button("Entrar")
    if entrar:
        try:
            ok = bcrypt.checkpw(senha.encode(), pw_hash.encode() if isinstance(pw_hash, str) else pw_hash)
        except Exception:
            ok = False
        if ok:
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    st.stop()


_require_password()

st.markdown(
    """
    <style>
      /* === EMR Design System — Brandbook 2026 (p.102) ===
         CANÔNICO (não alterar sem revisar o brandbook):
           Residente Green    #264641  base institucional
           Residente Approved #6CE190  aprovação · resultado · avanço
           Residente Lime     #B4F900  performance · digital · ação
           Residente Off White#F8F8F8  neutro · clareza · espaço
           Residente Orange   #FF7013  call to action · virada
         Complementares (brandbook autoriza p/ sistemas web, teto 30%):
           #50BCFF · #FF514D · #9500DB · #FFC805 · #009B3F
         Gradiente institucional: #6CE190 → #B4F900 (único autorizado).
         Tipografia: Outfit — pesos autorizados: Regular 400, Medium 500, Bold 700.

         DERIVADO (o brandbook não especifica tema dark de app): as superfícies
         --emr-bg/--emr-card são escurecimentos de #264641; --emr-text-muted é
         #264641 clareado e dessaturado. */
      @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;700&display=swap');

      :root {
        /* --- primárias canônicas --- */
        --emr-green:        #264641;   /* Residente Green */
        --emr-approved:     #6CE190;   /* Residente Approved */
        --emr-lime:         #B4F900;   /* Residente Lime */
        --emr-off-white:    #F8F8F8;   /* Residente Off White */
        --emr-orange:       #FF7013;   /* Residente Orange */

        /* --- complementares canônicas (máx 30% de presença) --- */
        --emr-blue:         #50BCFF;
        --emr-red:          #FF514D;
        --emr-purple:       #9500DB;
        --emr-yellow:       #FFC805;
        --emr-green-deep:   #009B3F;

        /* --- superfícies derivadas de #264641 --- */
        --emr-bg:           #101E1B;
        --emr-card-bg:      #1A302C;
        --emr-card-soft:    #203A35;
        --emr-card-hi:      #264641;
        --emr-text:         #F8F8F8;
        --emr-text-muted:   #93A8A2;
        --emr-neutral:      #8FA39D;
        --emr-line:         rgba(248, 248, 248, .10);
        --emr-track:        rgba(248, 248, 248, .09);

        --emr-shadow:       0 4px 24px rgba(0, 0, 0, .28);
        --emr-shadow-hover: 0 8px 32px rgba(0, 0, 0, .38);
        --emr-radius:       16px;
        --emr-radius-sm:    10px;
        --emr-gradient:     linear-gradient(135deg, #6CE190 0%, #B4F900 100%);
        --emr-sidebar-w:    232px;
        --emr-gap:          14px;
        --emr-topbar-h:     64px;
      }

      html, body, .stApp {background: var(--emr-bg) !important;}

      /* Outfit em tudo */
      html, body, [class*="css"], .stMarkdown, .stCaption, .stRadio,
      .stButton, .stSelectbox, .stMultiSelect, .stDataFrame, .stTabs,
      .stExpander, h1, h2, h3, h4, h5, h6, p, span, div, label, input, textarea {
        font-family: 'Outfit', -apple-system, BlinkMacSystemFont, sans-serif !important;
      }

      h1, h2, h3, h4, h5 {color: var(--emr-text) !important; letter-spacing: -0.01em;}
      h3 {font-weight: 700; font-size: 26px;}
      h4 {font-weight: 600; font-size: 20px;}
      h5 {font-weight: 600; font-size: 17px; margin-top: 32px !important; margin-bottom: 8px !important;}

      p, span, div {color: var(--emr-text);}
      .stMarkdown p, .stCaption {color: var(--emr-text-muted); font-size: 13.5px; line-height: 1.6;}

      .block-container {
        padding-top: calc(var(--emr-topbar-h) + 20px) !important; padding-bottom: 3rem; max-width: none;
        padding-left: calc(var(--emr-sidebar-w) + 40px) !important;
        padding-right: 40px !important;
      }

      /* gutter denso — bento */
      [data-testid="stHorizontalBlock"] {gap: var(--emr-gap) !important;}

      /* === Sidebar (tabs nível 1 viram nav vertical fixa) === */
      .stMainBlockContainer > [data-testid="stVerticalBlock"] > .stTabs > div > div > [data-baseweb="tab-list"] {
        position: fixed; top: 0; left: 0; bottom: 0;
        width: var(--emr-sidebar-w);
        flex-direction: column; align-items: stretch;
        background: var(--emr-card-bg);
        border-right: 1px solid var(--emr-line); border-bottom: none;
        padding: 88px 14px 24px 14px; margin: 0; gap: 6px;
        z-index: 999; overflow-y: auto;
      }
      /* Wordmark EMR no topo da sidebar */
      .stMainBlockContainer > [data-testid="stVerticalBlock"] > .stTabs > div > div > [data-baseweb="tab-list"]::before {
        content: "EMR · R1 2026";
        position: absolute; top: 28px; left: 24px;
        font-size: 17px; font-weight: 800; letter-spacing: -0.01em;
        color: var(--emr-text);
        padding-left: 18px;
        background: linear-gradient(135deg, #6CE190 0%, #B4F900 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        border-left: 4px solid var(--emr-lime);
      }
      .stMainBlockContainer > [data-testid="stVerticalBlock"] > .stTabs > div > div > [data-baseweb="tab-list"] [data-baseweb="tab"] {
        justify-content: flex-start;
        background: transparent !important; color: var(--emr-text-muted) !important;
        padding: 12px 16px !important; border-radius: 12px !important;
        font-weight: 500 !important; font-size: 14px !important;
        transition: background .15s, color .15s;
      }
      .stMainBlockContainer > [data-testid="stVerticalBlock"] > .stTabs > div > div > [data-baseweb="tab-list"] [data-baseweb="tab"]:hover {
        background: var(--emr-card-soft) !important; color: var(--emr-text) !important;
      }
      .stMainBlockContainer > [data-testid="stVerticalBlock"] > .stTabs > div > div > [data-baseweb="tab-list"] [aria-selected="true"] {
        background: var(--emr-lime) !important; color: #264641 !important;
        font-weight: 700 !important;
      }
      .stMainBlockContainer > [data-testid="stVerticalBlock"] > .stTabs > div > div > [data-baseweb="tab-list"] [data-baseweb="tab-highlight"],
      .stMainBlockContainer > [data-testid="stVerticalBlock"] > .stTabs > div > div > [data-baseweb="tab-list"] [data-baseweb="tab-border"] {
        display: none !important;
      }

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
        color: var(--emr-text); font-variant-numeric: tabular-nums;
        margin-top: 6px; letter-spacing: -0.025em;
      }
      .hero-card .sub {font-size: 12.5px; color: var(--emr-text-muted); margin-top: 8px; line-height: 1.45;}
      .hero-card .delta-up {color: var(--emr-approved); font-weight: 600;}
      .hero-card .delta-dn {color: var(--emr-red); font-weight: 600;}
      .hero-card .delta-flat {color: var(--emr-text-muted);}
      .hero-card .pill {
        display: inline-block; width: 8px; height: 8px; border-radius: 50%;
        margin-right: 8px; vertical-align: middle;
      }

      /* === Tabela densa === */
      .gap-table {width: 100%; border-collapse: separate; border-spacing: 0;
        background: var(--emr-card-bg); border-radius: var(--emr-radius-sm);
        overflow: hidden; box-shadow: var(--emr-shadow);
        border: 1px solid var(--emr-line);
      }
      .gap-table td, .gap-table th {padding: 9px 13px; font-size: 12.5px;}
      .gap-table th {
        background: var(--emr-card-soft);
        text-align: left; color: var(--emr-text-muted); font-weight: 600;
        text-transform: uppercase; letter-spacing: .5px; font-size: 10px;
        border-bottom: 1px solid var(--emr-line);
      }
      .gap-table td {
        border-bottom: 1px solid var(--emr-line);
        font-variant-numeric: tabular-nums; color: var(--emr-text);
      }
      .gap-table tbody tr:last-child td {border-bottom: none;}
      .gap-table tbody tr:nth-child(even) td {background: rgba(248,248,248,.02);}
      .gap-table tbody tr:hover td {background: rgba(108, 225, 144, .07);}
      .gap-table td.dim {font-weight: 600; color: var(--emr-text);}
      .gap-table td.val {text-align: right;}
      .gap-table td.status {text-align: center; font-weight: 600;}
      table {font-variant-numeric: tabular-nums;}

      /* === Tabs internas (nível 2+): pills dark estilo referência === */
      .stTabs .stTabs [data-baseweb="tab-list"] {gap: 6px; border-bottom: none;
        background: transparent; padding: 0; margin-bottom: 16px;
      }
      .stTabs .stTabs [data-baseweb="tab"] {
        background: var(--emr-card-bg) !important; color: var(--emr-text-muted) !important;
        padding: 6px 15px !important; border-radius: 999px !important;
        border: 1px solid var(--emr-line) !important;
        font-weight: 500 !important; font-size: 12.5px !important;
        transition: color .15s, background .15s;
      }
      .stTabs .stTabs [data-baseweb="tab"]:hover {color: var(--emr-text) !important; background: var(--emr-card-soft) !important;}
      .stTabs .stTabs [aria-selected="true"] {
        color: #264641 !important; font-weight: 700 !important;
        background: var(--emr-lime) !important; border-color: var(--emr-lime) !important;
      }
      .stTabs .stTabs [data-baseweb="tab-highlight"],
      .stTabs .stTabs [data-baseweb="tab-border"] {display: none !important;}

      /* === Banners (st.info, st.warning) === */
      [data-testid="stAlert"] {border-radius: var(--emr-radius-sm) !important;
        border: 1px solid var(--emr-line) !important; background: var(--emr-card-bg) !important;
      }
      [data-testid="stAlert"][kind="info"] {border-left: 4px solid var(--emr-approved) !important;}
      [data-testid="stAlert"][kind="warning"] {border-left: 4px solid #FFC805 !important;}

      /* === Multiselect / inputs === */
      [data-baseweb="select"] > div {
        border-radius: 10px !important; border-color: var(--emr-line) !important;
        background: var(--emr-card-bg) !important;
      }
      [data-baseweb="tag"] {background: var(--emr-gradient) !important; color: #264641 !important;
        border-radius: 6px !important; font-weight: 600 !important;
      }
      [data-baseweb="tag"] span {color: #264641 !important;}

      /* === Header global EMR (banner gradient — CTA lime da referência) === */
      .emr-hero {
        background: var(--emr-gradient);
        border-radius: var(--emr-radius);
        padding: 28px 36px;
        margin-bottom: 24px;
        box-shadow: var(--emr-shadow);
      }
      .emr-hero h3, .emr-hero p, .emr-hero span, .emr-hero div {color: #264641 !important;}
      .emr-hero h3 {margin: 0 0 6px 0 !important;
        font-weight: 700; font-size: 30px; letter-spacing: -0.02em;
      }
      .emr-hero p {margin: 0; font-size: 14px; opacity: 0.85;}

      /* Esconde header e footer padrão do Streamlit pra visual limpo */
      header[data-testid="stHeader"] {background: transparent; height: 0;}
      [data-testid="stToolbar"], [data-testid="stDecoration"] {display: none !important;}
      footer {visibility: hidden;}
      #MainMenu {visibility: hidden;}

      /* ============================================================
         BENTO DARK v2026 — topbar, kpi, gauge, donut
         ============================================================ */

      /* --- TOPBAR fixa --- */
      .emr-topbar {
        position: fixed; top: 0; left: var(--emr-sidebar-w); right: 0; height: var(--emr-topbar-h); z-index: 998;
        display: flex; align-items: center; justify-content: space-between; padding: 0 40px;
        background: rgba(12,23,18,.82); backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
        border-bottom: 1px solid var(--emr-line);
      }
      .tb-left {display: flex; align-items: center; gap: 14px;}
      .tb-logo {display: grid; place-items: center; width: 38px; height: 38px; border-radius: 11px;
        background: var(--emr-gradient); color: #264641 !important; font-weight: 800; font-size: 15px;
        letter-spacing: -.02em; box-shadow: 0 2px 12px rgba(108, 225, 144,.28);}
      .tb-titles {display: flex; flex-direction: column; line-height: 1.15;}
      .tb-title {font-size: 15px; font-weight: 700; color: var(--emr-text); letter-spacing: -.01em;}
      .tb-sub {font-size: 11.5px; font-weight: 500; color: var(--emr-text-muted);}
      .tb-right {display: flex; align-items: center; gap: 16px;}
      .tb-chip {display: inline-flex; align-items: center; gap: 7px; padding: 6px 13px; border-radius: 999px;
        background: var(--emr-card-bg); border: 1px solid var(--emr-line); font-size: 12px; font-weight: 600; color: var(--emr-text);}
      .tb-chip--live i {width: 7px; height: 7px; border-radius: 50%; background: var(--emr-approved); box-shadow: 0 0 0 3px rgba(108, 225, 144,.18);}
      .tb-snap {font-size: 12.5px; color: var(--emr-text-muted);}
      .tb-snap strong {color: var(--emr-text); font-weight: 600;}

      /* --- SIDEBAR: ícones nos 4 itens de nível 1 --- */
      .stMainBlockContainer > [data-testid="stVerticalBlock"] > .stTabs > div > div > [data-baseweb="tab-list"] [data-baseweb="tab"] {
        display: flex; align-items: center; gap: 11px;
      }
      .stMainBlockContainer > [data-testid="stVerticalBlock"] > .stTabs > div > div > [data-baseweb="tab-list"] [data-baseweb="tab"]::before {
        font-size: 16px; line-height: 1; width: 20px; text-align: center; filter: grayscale(.15); opacity: .9;
      }
      .stMainBlockContainer > [data-testid="stVerticalBlock"] > .stTabs > div > div > [data-baseweb="tab-list"] [data-baseweb="tab"]:nth-child(1)::before {content: "📊";}
      .stMainBlockContainer > [data-testid="stVerticalBlock"] > .stTabs > div > div > [data-baseweb="tab-list"] [data-baseweb="tab"]:nth-child(2)::before {content: "🏥";}
      .stMainBlockContainer > [data-testid="stVerticalBlock"] > .stTabs > div > div > [data-baseweb="tab-list"] [data-baseweb="tab"]:nth-child(3)::before {content: "📈";}
      .stMainBlockContainer > [data-testid="stVerticalBlock"] > .stTabs > div > div > [data-baseweb="tab-list"] [data-baseweb="tab"]:nth-child(4)::before {content: "🎯";}
      .stMainBlockContainer > [data-testid="stVerticalBlock"] > .stTabs > div > div > [data-baseweb="tab-list"] [aria-selected="true"]::before {filter: none; opacity: 1;}

      /* --- KPI card (novo, coexiste com .hero-card legado) --- */
      .kpi {background: var(--emr-card-bg); border: 1px solid var(--emr-line); border-radius: var(--emr-radius);
        padding: 20px 22px; height: 100%; box-shadow: var(--emr-shadow);
        transition: box-shadow .2s, transform .15s, border-color .2s;
        display: flex; align-items: flex-start; justify-content: space-between; gap: 14px;}
      .kpi:hover {box-shadow: var(--emr-shadow-hover); transform: translateY(-1px); border-color: rgba(108, 225, 144,.22);}
      .kpi-main {min-width: 0;}
      .kpi-col {flex-direction: column; align-items: stretch;}
      .kpi-eyebrow {font-size: 11px; text-transform: uppercase; letter-spacing: 1.2px;
        color: var(--emr-text-muted); font-weight: 600; display: flex; align-items: center;}
      .kpi-eyebrow .dot {width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; flex: 0 0 8px;}
      .kpi-val {font-size: 40px; font-weight: 700; line-height: 1; color: var(--emr-text);
        font-variant-numeric: tabular-nums; margin-top: 8px; letter-spacing: -.025em;}
      .kpi-val span {font-size: 15px; font-weight: 600; color: var(--emr-text-muted);}
      .kpi-sub {font-size: 12.5px; color: var(--emr-text-muted); margin-top: 8px; line-height: 1.4;}

      /* anel radial (conic-gradient) */
      .gauge {position: relative; width: 74px; height: 74px; flex: 0 0 74px;}
      .gauge .ring {width: 100%; height: 100%; border-radius: 50%;
        background: conic-gradient(var(--fill) calc(var(--p) * 1%), var(--emr-track) 0);
        -webkit-mask: radial-gradient(farthest-side, #0000 calc(100% - 9px), #000 calc(100% - 8px));
                mask: radial-gradient(farthest-side, #0000 calc(100% - 9px), #000 calc(100% - 8px));}
      .gauge .gnum {position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
        font-size: 18px; font-weight: 700; color: var(--emr-text); font-variant-numeric: tabular-nums; white-space: nowrap;}
      .gauge .gnum span {font-size: 11px; font-weight: 600; color: var(--emr-text-muted); margin-left: 1px;}

      /* barra de progresso */
      .pbar {height: 6px; border-radius: 999px; background: var(--emr-track); overflow: hidden; margin: 12px 0 0;}
      .pbar > i {display: block; height: 100%; border-radius: 999px; width: calc(var(--p) * 1%); background: var(--fill); transition: width .4s;}

      /* delta chip */
      .chip-delta {display: inline-flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 600;
        padding: 3px 9px; border-radius: 999px; margin-top: 10px;}
      .chip-delta.up {color: var(--emr-approved); background: rgba(108, 225, 144,.12);}
      .chip-delta.dn {color: var(--emr-red); background: rgba(255,81,77,.12);}
      .chip-delta.flat {color: var(--emr-text-muted); background: rgba(248,248,248,.06);}

      /* --- DONUT de faixas --- */
      .donut-card {background: var(--emr-card-bg); border: 1px solid var(--emr-line); border-radius: var(--emr-radius);
        padding: 20px; height: 100%; box-shadow: var(--emr-shadow);
        display: flex; flex-direction: column; align-items: center; gap: 16px;}
      .donut {position: relative; width: 132px; height: 132px; border-radius: 50%;}
      .donut-hole {position: absolute; inset: 15px; border-radius: 50%; background: var(--emr-card-bg);
        display: grid; place-items: center; align-content: center;}
      .donut-big {font-size: 26px; font-weight: 700; color: var(--emr-text); line-height: 1;}
      .donut-cap {font-size: 10.5px; font-weight: 600; text-transform: uppercase; letter-spacing: .6px; color: var(--emr-text-muted); margin-top: 3px;}
      .donut-legend {list-style: none; margin: 0; padding: 0; width: 100%;}
      .donut-legend li {display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--emr-text-muted); padding: 4px 0;}
      .donut-legend li span {width: 9px; height: 9px; border-radius: 3px; flex: 0 0 9px;}
      .donut-legend li b {margin-left: auto; color: var(--emr-text); font-weight: 600; font-variant-numeric: tabular-nums;}

    </style>
    """,
    unsafe_allow_html=True,
)

# ============================================================
# CONSTANTES — régua oficial v2, centralizada em prescription_v2.py
# ============================================================

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


def _feedback_critical_pct(feedbacks: pd.DataFrame) -> float:
    """Percentual crítico entre registros com nota válida."""
    notas = pd.to_numeric(feedbacks["nota"], errors="coerce")
    avaliados = notas.between(1, 5)
    if not avaliados.any():
        return 0.0
    return float(notas.loc[avaliados].le(2).mean() * 100)


def _feedbacks_com_nota_valida(feedbacks: pd.DataFrame) -> pd.DataFrame:
    """Mantém somente avaliações com nota inteira de 1 a 5."""
    if feedbacks.empty or "nota" not in feedbacks.columns:
        return feedbacks.iloc[0:0].copy()
    validos = feedbacks.copy()
    validos["nota"] = pd.to_numeric(validos["nota"], errors="coerce")
    validos = validos[validos["nota"].isin([1, 2, 3, 4, 5])].copy()
    validos["nota"] = validos["nota"].astype(int)
    return validos


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
    "Excelência":          "#6CE190",  # Residente Approved — "aprovação · resultado"
    "Proficiência":        "#009B3F",  # complementar verde — mesmo eixo, patamar abaixo
    "Abaixo do canal":     "#FF514D",  # complementar vermelha — alerta
    "Sem acerto canônico": "#8FA39D",  # neutro derivado — ausência de dado, não julgamento
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
# --- Fonte dos dados: release privado (runtime, via DATA_TOKEN) ou disco (dev local).
# Os parquets já vêm com big_area_id resolvido e SEM PII (só account_id + métricas).
def _data_token() -> str | None:
    try:
        return st.secrets.get("DATA_TOKEN") or os.getenv("DATA_TOKEN")
    except Exception:
        return os.getenv("DATA_TOKEN")


@st.cache_data(ttl=3600, show_spinner=False)
def _release_assets(token: str) -> dict[str, dict[str, str]]:
    """Metadados dos assets do release privado, incluindo data real."""
    r = requests.get(
        f"https://api.github.com/repos/{DATA_REPO}/releases/tags/{DATA_TAG}",
        headers={"Authorization": f"token {token}",
                 "Accept": "application/vnd.github+json"},
        timeout=30,
    )
    r.raise_for_status()
    return {
        asset["name"]: {
            "url": asset["url"],
            "updated_at": asset["updated_at"],
        }
        for asset in r.json().get("assets", [])
    }


def _release_asset_urls(token: str) -> dict[str, str]:
    """Mapa asset_name → api_url, preservado para o downloader."""
    return {name: data["url"] for name, data in _release_assets(token).items()}


def _release_asset_snapshot_date(token: str, asset_name: str) -> pd.Timestamp:
    """Data do asset no release; nunca confunde hora do acesso com frescor."""
    asset = _release_assets(token).get(asset_name)
    if not asset or not asset.get("updated_at"):
        raise FileNotFoundError(f"Metadado ausente para o asset {asset_name}.")
    return pd.Timestamp(asset["updated_at"]).tz_convert("America/Sao_Paulo")


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_data_file(asset_name: str) -> str | None:
    """Baixa um asset do release privado para /tmp e retorna o caminho local.
    Retorna None se não houver token (dev local cai no disco)."""
    token = _data_token()
    if not token:
        return None
    urls = _release_asset_urls(token)
    if asset_name not in urls:
        return None
    r = requests.get(
        urls[asset_name],
        headers={"Authorization": f"token {token}",
                 "Accept": "application/octet-stream"},
        timeout=120,
    )
    r.raise_for_status()
    dst = Path(tempfile.gettempdir()) / f"emrdash_{asset_name}"
    dst.write_bytes(r.content)
    return str(dst)


def _read_parquet_source(asset_name: str, local_path: Path) -> pd.DataFrame:
    """Lê um parquet: do release privado (se houver DATA_TOKEN) ou do disco (dev).
    DataFrame vazio se ausente em ambos."""
    remote = _fetch_data_file(asset_name)
    src = Path(remote) if remote else local_path
    if not Path(src).exists():
        return pd.DataFrame()
    return pd.read_parquet(src)


def _read_b2b_parquet(name: str) -> pd.DataFrame:
    """Lê um parquet B2B (release: prefixo 'b2b__'; disco: snapshots/b2b/)."""
    return _read_parquet_source(f"b2b__{name}", SNAPSHOTS_B2B_DIR / name)


def _normalize_canonico(d: pd.DataFrame) -> pd.DataFrame:
    """Compat de esquema: o ETL B2C renomeou as colunas de acerto canônico para
    'm10_*'; o B2B e o app usam 'acerto_canonico_*'. Renomeia m10_* → canônico
    para o app funcionar com qualquer um dos dois esquemas."""
    ren = {
        "m10_acertos": "acerto_canonico_acertos",
        "m10_questao_count": "acerto_canonico_questao_count",
        "m10_pct": "acerto_canonico_pct",
        "m10_acertos_7d": "acerto_canonico_acertos_7d",
        "m10_questao_count_7d": "acerto_canonico_questao_count_7d",
        "m10_pct_7d": "acerto_canonico_pct_7d",
    }
    cols = {k: v for k, v in ren.items() if k in d.columns and v not in d.columns}
    return d.rename(columns=cols) if cols else d


def _validate_snapshot_frames(
    df: pd.DataFrame,
    cohort: pd.DataFrame,
    metrics: pd.DataFrame,
    source_label: str,
) -> None:
    """Converte asset ausente/schema inválido em erro explícito e tratável."""
    required = (
        ("atividade", df, {"account_id", "semana_iso"}),
        ("cohort", cohort, {"account_id"}),
        ("métricas", metrics, {"account_id"}),
    )
    missing = [
        name for name, frame, columns in required
        if not columns.issubset(frame.columns)
    ]
    if missing:
        raise FileNotFoundError(
            f"Snapshot {source_label} incompleto: {', '.join(missing)}."
        )


def load_snapshot(snap_dir: Path = SNAPSHOTS):
    # B2C: asset do release com nome simples; B2B usa _read_b2b_parquet (prefixo b2b__).
    is_b2b = snap_dir == SNAPSHOTS_B2B_DIR
    pref = "b2b__" if is_b2b else ""
    df = _normalize_canonico(_read_parquet_source(f"{pref}latest.parquet", snap_dir / "latest.parquet"))
    cohort = _read_parquet_source(f"{pref}latest_cohort.parquet", snap_dir / "latest_cohort.parquet")
    metrics = _normalize_canonico(_read_parquet_source(f"{pref}latest_cohort_metrics.parquet", snap_dir / "latest_cohort_metrics.parquet"))
    _validate_snapshot_frames(
        df,
        cohort,
        metrics,
        "B2B" if is_b2b else "B2C",
    )
    df["semana_iso"] = pd.to_datetime(df["semana_iso"])
    if "mes_ref" in df.columns:
        df["mes_ref"] = pd.to_datetime(df["mes_ref"])
    for d in (df, cohort, metrics):
        d["account_id"] = d["account_id"].astype(int)
    metrics = deduplicate_account_metrics(metrics)
    # Data do snapshot: mtime local ou updated_at real do asset no release.
    local = snap_dir / "latest.parquet"
    token = _data_token()
    if token:
        snap_date = _release_asset_snapshot_date(token, f"{pref}latest.parquet")
    elif local.exists():
        snap_date = pd.Timestamp(local.resolve().stat().st_mtime, unit="s", tz="UTC").tz_convert("America/Sao_Paulo")
    else:
        raise FileNotFoundError(f"Snapshot local ausente: {local}.")
    return df, cohort, metrics, snap_date


df, cohort, metrics_acum, snapshot_date = load_snapshot()

# Turmas fora do dashboard B2C — trials não são pagantes (decisão 2026-07-13).
# O ETL (queries/01_cohort.sql) também exclui na origem; este filtro defensivo
# cobre parquets gerados antes da mudança. Remove linhas de turma, não contas:
# aluno trial que também tem matrícula paga permanece pela turma paga.
TURMAS_EXCLUIDAS = {"Teste Grátis"}

# Cohort B2C tem 1 linha por (account_id, turma) — alunos em 2+ turmas duplicam.
# `cohort_turma` preserva a coluna `turma` (pra filtros). `cohort` global é
# deduplicado por account_id (mantém a matrícula mais antiga, pra compat com
# aggregate_month e demais funções que esperam 1 linha por aluno).
if "turma" in cohort.columns:
    cohort_turma = cohort[~cohort["turma"].isin(TURMAS_EXCLUIDAS)].copy()
    cohort = deduplicate_accounts(cohort_turma).drop(columns=["turma"])
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


def _filter_by_accounts(df: pd.DataFrame, account_ids: tuple[int, ...]) -> pd.DataFrame:
    if df.empty or "account_id" not in df.columns:
        return df
    return df[df["account_id"].isin(set(account_ids))].copy()


@st.cache_data(ttl=60 * 60, show_spinner="Carregando avaliações de aulas…")
def load_avaliacoes_aulas(account_ids: tuple[int, ...]) -> pd.DataFrame:
    """Avaliações de aulas (mes, big_area_id, rate, account_id) do parquet."""
    return _filter_by_accounts(_read_b2b_parquet("avaliacoes_aulas.parquet"), account_ids)


@st.cache_data(ttl=60 * 60, show_spinner="Carregando avaliações de questões…")
def load_avaliacoes_questoes(account_ids: tuple[int, ...]) -> pd.DataFrame:
    """Avaliações de explicações de questões do parquet."""
    return _filter_by_accounts(_read_b2b_parquet("avaliacoes_questoes.parquet"), account_ids)


@st.cache_data(ttl=60 * 60, show_spinner="Carregando avaliações de materiais…")
def load_avaliacoes_materiais(account_ids: tuple[int, ...]) -> pd.DataFrame:
    """Avaliações de materiais (inclui coluna tipo_material) do parquet."""
    return _filter_by_accounts(_read_b2b_parquet("avaliacoes_materiais.parquet"), account_ids)


@st.cache_data(ttl=60 * 60, show_spinner="Carregando avaliações de flashcards…")
def load_avaliacoes_flashcards(account_ids: tuple[int, ...]) -> pd.DataFrame:
    """Avaliações de decks de flashcards do parquet."""
    return _filter_by_accounts(_read_b2b_parquet("avaliacoes_flashcards.parquet"), account_ids)


@st.cache_data(ttl=60 * 60, show_spinner="Carregando feedbacks…")
def load_feedbacks_organicos() -> pd.DataFrame:
    """Feedbacks sanitizados, sem identificador interno da conta."""
    return _read_b2b_parquet("feedbacks_organicos.parquet").drop(
        columns=["account_id"], errors="ignore"
    )


# Simulados institucionais ENAMED 2026 (Inspirali, FMO, FACAPE, …).
# Fonte de verdade: registro curado em etl/simulados_registry.py, materializado
# 1x/dia pelo ETL em enamed_templates.parquet — 1 linha por EDIÇÃO. Uma edição
# pode agrupar 2+ templates (ex.: 3º Inspirali = Provas 1+2).
# Decisão 2026-07-13: nome de template não é confiável pra descoberta — o ETL
# só ALERTA candidatos não mapeados; quem decide exibição é o registro.
# A lista abaixo é apenas o FALLBACK usado se o parquet faltar.
ENAMED_2026_EDICOES_FALLBACK: list[dict] = [
    {"edicao_id": 1, "nome": "1º Simulado ENAMED — Inspirali 2026", "ies": "Inspirali",
     "data": "2026-02-26", "template_ids": (15798,)},
    {"edicao_id": 2, "nome": "2º Simulado ENAMED — Inspirali 2026", "ies": "Inspirali",
     "data": "2026-04-28", "template_ids": (17948,)},
    {"edicao_id": 3, "nome": "3º Simulado ENAMED — Inspirali 2026 (Provas 1+2)", "ies": "Inspirali",
     "data": "2026-05-30", "template_ids": (19994, 20390)},
    {"edicao_id": 4, "nome": "4º Simulado ENAMED — Inspirali 2026", "ies": "Inspirali",
     "data": "2026-06-17", "template_ids": (21446,)},
    {"edicao_id": 5, "nome": "1º Simulado ENAMED — FACAPE 2026", "ies": "FACAPE",
     "data": "2026-06-01", "template_ids": (20028,)},
    {"edicao_id": 6, "nome": "1º Simulado ENAMED — FMO 2026", "ies": "FMO",
     "data": "2026-06-25", "template_ids": (21644,)},
    {"edicao_id": 7, "nome": "5º Simulado ENAMED — Inspirali 2026", "ies": "Inspirali",
     "data": "2026-07-14", "template_ids": (22535,)},
]


@st.cache_data(ttl=60 * 60)
def load_enamed_2026_templates() -> list[dict]:
    """Edições de simulados institucionais ENAMED 2026, do parquet pré-calculado
    (enamed_templates.parquet), em ordem cronológica.

    Retorna dicts {edicao_id, nome, ies, data, template_ids}. Tolera o schema
    antigo (1 linha por template, sem edicao_id) — cobre a janela de cache de
    até 1h entre o deploy do app e a regeneração do release de dados.
    """
    df = _read_b2b_parquet("enamed_templates.parquet")
    if df.empty:
        return [dict(e) for e in ENAMED_2026_EDICOES_FALLBACK]
    if "edicao_id" not in df.columns:  # schema antigo (pré 2026-07-13)
        return [
            {"edicao_id": i + 1, "nome": str(r.nome), "ies": "Inspirali",
             "data": str(r.data_aplicacao), "template_ids": (int(r.mock_template_id),)}
            for i, r in enumerate(df.itertuples())
        ]
    return [
        {"edicao_id": int(r.edicao_id), "nome": str(r.nome), "ies": str(r.ies),
         "data": str(r.data_aplicacao),
         "template_ids": tuple(int(t) for t in r.mock_template_ids)}
        for r in df.itertuples()
    ]


@st.cache_data(ttl=60 * 60)
def load_enamed_2026_results(account_ids: tuple[int, ...]) -> pd.DataFrame:
    """Resultados ENAMED 2026 por aluno, do parquet pré-calculado
    (enamed_results.parquet), filtrados pelos account_ids do grupo.

    Colunas: account_id, mock_template_id, question_count, acertos, pct
    (+ edicao_id no schema novo; 1 linha por aluno por edição — 1ª tentativa).
    """
    return _filter_by_accounts(_read_b2b_parquet("enamed_results.parquet"), account_ids)


# Mínimo de mock-takers num mês pra confiar nos percentis e recalibrar o canal.
# Abaixo disso, mantém o baseline 2025.
CANAL_MIN_N_MOCK_TAKERS = 100


def recompute_canais_2026(df_w: pd.DataFrame, cohort_df: pd.DataFrame) -> dict[str, dict]:
    """Anota observações empíricas (n_mock_takers, P75/P90 reais) por mês.

    NÃO sobrescreve os canais editoriais v2. Eles vêm dos documentos reais
    prescricao_excelencia_v2.md e prescricao_proficiencia_v2.md, não dos
    percentis empíricos crus.

    Esta função preserva os percentis empíricos pra instrumentação e validação,
    mas eles não alteram a classificação.
    """
    coh_2026 = cohort_df[["account_id", "first_start_date"]].copy()
    coh_2026["first_start_date"] = pd.to_datetime(coh_2026["first_start_date"])
    coh_2026 = coh_2026.dropna(subset=["first_start_date"])
    coh_ids = coh_2026["account_id"].unique()

    out: dict[str, dict] = {}
    activity_month = activity_month_labels(df_w)
    for mes in PRESCRIPTION_ORDER:
        sub = df_w[activity_month == mes]
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
    acerto_canonico_pct = round(float(acerto_canonico_pct), 10)
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
    coh = eligible_cohort_for_month(coh, mes)
    sub = df_weekly[activity_month_labels(df_weekly) == mes]
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
        activity_month_labels(df_).unique()
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
    <div class="emr-topbar">
      <div class="tb-left">
        <span class="tb-logo">EMR</span>
        <div class="tb-titles">
          <span class="tb-title">Dashboard Ensino &amp; Produto</span>
          <span class="tb-sub">Cohort R1 2026</span>
        </div>
      </div>
      <div class="tb-right">
        <span class="tb-chip tb-chip--live"><i></i>{fresh_txt}</span>
        <span class="tb-snap">Snapshot&nbsp;<strong>{snapshot_date:%d/%m %H:%M}</strong></span>
      </div>
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


tab_agora, tab_b2b, tab_evolucao, tab_qualidade, tab_voz = st.tabs(
    [
        "Visão geral B2C",
        "Visão geral B2B",
        "Evolução B2C",
        "Qualidade B2C",
        "Voz do aluno",
    ]
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
            f"<strong>dentro do canal Proficiência v2</strong> "
            f"({ultimo['prof_min']}–{ultimo['prof_max']}%), "
            f"a {ultimo['excel_min'] - ultimo['mediana_turma']:.1f}pp de entrar no canal Excelência v2"
        )
    else:
        posicao = (
            f"<strong>abaixo do canal Proficiência v2</strong> "
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

def _compute_status_geral(
    df_: pd.DataFrame,
    cohort_: pd.DataFrame,
    last_closed_: str,
    reference_date: pd.Timestamp,
) -> dict:
    """Calcula 4 indicadores de status do cohort em 2026:

    - **Matrículas ativas**: tamanho do cohort (inscrição válida — filtrado upstream).
    - **Ativos**: alunos com `dias_ativos > 0` na última semana ISO fechada.
      `dias_ativos` = qualquer atividade (questão, aula/bloco ≥50%, flashcard).
    - **Engajados**: média semanal ≥25 questões E ≥8 blocos desde a primeira
      semana ativa do aluno em 2026 até a última semana fechada.
    - **Meta mínima**: média semanal ≥70 questões na mesma janela.
    """
    cohort_ids = cohort_["account_id"]
    n_pagantes = len(cohort_)

    # --- Ativos na última semana ISO fechada ---
    reference_date = pd.Timestamp(reference_date).normalize()
    _full_monday = reference_date - pd.Timedelta(
        days=int(reference_date.weekday()) + 7
    )
    recent = df_[(df_["semana_iso"] == _full_monday) & (df_["dias_ativos"] > 0)]
    recent = recent[recent["account_id"].isin(cohort_ids)]
    n_ativos_semana = int(recent["account_id"].nunique())

    # --- Engajados / meta mínima (lógica anterior) ---
    week_start = pd.Timestamp("2026-01-05")
    last_mes_end = pd.Timestamp(f"{last_closed_}-01") + pd.offsets.MonthEnd(0)
    last_sunday = last_mes_end - pd.Timedelta(days=(last_mes_end.weekday() + 1) % 7)
    last_monday = last_sunday - pd.Timedelta(days=6)

    sub = df_[(df_["semana_iso"] >= week_start) & (df_["semana_iso"] <= last_monday)]
    sub = sub[sub["account_id"].isin(cohort_ids)]
    if sub.empty:
        return {"n_pagantes": n_pagantes, "n_ativos_semana": n_ativos_semana,
                "n_engajados": 0, "n_meta_min": 0}

    first_active = (
        sub[sub["dias_ativos"] > 0]
        .groupby("account_id")["semana_iso"].min()
        .rename("first_active_week").reset_index()
    )
    if first_active.empty:
        return {"n_pagantes": n_pagantes, "n_ativos_semana": n_ativos_semana,
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
    return {"n_pagantes": n_pagantes, "n_ativos_semana": n_ativos_semana,
            "n_engajados": n_engajados, "n_meta_min": n_meta_min}


def _render_agora_now(
    metrics_df: pd.DataFrame,
    reference_date: pd.Timestamp,
    key_prefix: str = "agora",
):
    # --- Status geral: pagantes, ativos (última semana fechada), engajados, meta mínima ---
    reference_date = pd.Timestamp(reference_date).normalize()
    _status = _compute_status_geral(df, cohort, last_closed, reference_date)
    _stat_pagantes = _status["n_pagantes"]
    _stat_ativos = _status["n_ativos_semana"]
    _stat_engaj = _status["n_engajados"]
    _stat_meta = _status["n_meta_min"]
    _denom = _stat_pagantes if _stat_pagantes else 1
    _pct_ativos = _stat_ativos / _denom * 100
    _pct_engaj = _stat_engaj / _denom * 100
    _pct_meta = _stat_meta / _denom * 100

    st.markdown("&nbsp;")
    sg1, sg2, sg3, sg4 = st.columns(4)
    sg1.markdown(
        f"""<div class="kpi">
          <div class="kpi-main">
            <div class="kpi-eyebrow"><span class="dot" style="background:var(--emr-green-deep)"></span>Matrículas ativas</div>
            <div class="kpi-val">{_stat_pagantes:,}</div>
            <div class="kpi-sub">inscrição ativa</div>
          </div>
        </div>""",
        unsafe_allow_html=True,
    )
    for _col, _lbl, _n, _pct, _fill, _sub in (
        (sg2, "Ativos", _stat_ativos, _pct_ativos, "var(--emr-approved)",
         "≥1 atividade (aula, questão ou flashcard) na última semana fechada"),
        (sg3, "Meta mínima", _stat_meta, _pct_meta, "var(--emr-lime)",
         "≥70 questões/semana (média)"),
        (sg4, "Engajados", _stat_engaj, _pct_engaj, "var(--emr-yellow)",
         "≥25 questões + ≥8 blocos/semana (média)"),
    ):
        _col.markdown(
            f"""<div class="kpi">
              <div class="kpi-main">
                <div class="kpi-eyebrow"><span class="dot" style="background:{_fill}"></span>{_lbl}</div>
                <div class="kpi-val">{_n:,}</div>
                <div class="kpi-sub">{_sub}</div>
              </div>
              <div class="gauge" style="--p:{_pct:.1f}; --fill:{_fill}">
                <div class="ring"></div>
                <div class="gnum">{_pct:.0f}<span>%</span></div>
              </div>
            </div>""",
            unsafe_allow_html=True,
        )

    # --- Distribuição de faixas: donut + cards com barra de progresso ---
    # (Janela fixa: último mês fechado, com Δ vs mês anterior.)
    cur_per = aggregate_month(df, last_closed)
    prev_per = aggregate_month(df, prev_closed) if prev_closed else None
    cur_mes = last_closed
    cur_mes_label = PRESCRIPTION_MONTHLY[last_closed]["label"]
    prev_mes_label = PRESCRIPTION_MONTHLY[prev_closed]["label"] if prev_closed else None

    cur_counts = faixa_counts(cur_per)
    prev_counts = faixa_counts(prev_per) if prev_per is not None else None
    cur_total = len(cur_per)
    prev_total = len(prev_per) if prev_per is not None else 0

    st.markdown("&nbsp;")
    _ch = PRESCRIPTION_MONTHLY[cur_mes]
    _meaning_dyn = {
        "Excelência":          f"acertou ≥{_ch['excel_min']}% no mock de {cur_mes_label}",
        "Proficiência":        f"acertou ≥{_ch['prof_min']}% e <{_ch['excel_min']}% no mock de {cur_mes_label}",
        "Abaixo do canal":     f"acertou <{_ch['prof_min']}% no mock de {cur_mes_label}",
        "Sem acerto canônico": f"não fez mock canônico em {cur_mes_label}",
    }
    _pcts = {
        f: cur_counts[f] / cur_total * 100 if cur_total else 0.0
        for f in PRESCRIPTION_CLASSES
    }
    _c1 = _pcts["Excelência"]
    _c2 = _c1 + _pcts["Proficiência"]
    _c3 = _c2 + _pcts["Abaixo do canal"]
    _donut_grad = (
        f"conic-gradient("
        f"var(--emr-approved) 0 {_c1:.1f}%, "
        f"var(--emr-green-deep) {_c1:.1f}% {_c2:.1f}%, "
        f"var(--emr-red) {_c2:.1f}% {_c3:.1f}%, "
        f"var(--emr-neutral) {_c3:.1f}% 100%)"
    )
    _legend_items = "".join(
        f'<li><span style="background:{PRESCRIPTION_COLORS[f]}"></span>{f}'
        f'<b>{_pcts[f]:.0f}% · {cur_counts[f]:,}</b></li>'
        for f in PRESCRIPTION_CLASSES
    )
    dcol, *fcols = st.columns([1.15, 1, 1, 1])
    dcol.markdown(
        f"""<div class="donut-card">
          <div class="donut" style="background:{_donut_grad}">
            <div class="donut-hole">
              <div class="donut-big">{_pcts['Excelência']:.0f}%</div>
              <div class="donut-cap">Excelência</div>
            </div>
          </div>
          <ul class="donut-legend">{_legend_items}</ul>
        </div>""",
        unsafe_allow_html=True,
    )
    for _fcol, faixa in zip(fcols, PRESCRIPTION_CLASSES[:3]):
        n_cur = cur_counts[faixa]
        pct_cur = _pcts[faixa]
        color = PRESCRIPTION_COLORS[faixa]
        meaning = _meaning_dyn[faixa]

        if prev_counts is not None:
            n_prev = prev_counts[faixa]
            pct_prev = n_prev / prev_total * 100 if prev_total else 0.0
            delta_pp = pct_cur - pct_prev
            if abs(delta_pp) < 0.5:
                delta_html = f'<span class="chip-delta flat">≈ vs {prev_mes_label}</span>'
            elif delta_pp > 0:
                cls = "up" if faixa in ("Excelência", "Proficiência") else "dn"
                delta_html = f'<span class="chip-delta {cls}">+{delta_pp:.1f}pp vs {prev_mes_label}</span>'
            else:
                cls = "dn" if faixa in ("Excelência", "Proficiência") else "up"
                delta_html = f'<span class="chip-delta {cls}">{delta_pp:.1f}pp vs {prev_mes_label}</span>'
        else:
            delta_html = '<span class="chip-delta flat">—</span>'

        _fcol.markdown(
            f"""<div class="kpi kpi-col">
              <div class="kpi-eyebrow"><span class="dot" style="background:{color}"></span>{faixa}</div>
              <div class="kpi-val">{pct_cur:.0f}<span>%</span></div>
              <div class="pbar" style="--p:{pct_cur:.1f}; --fill:{color}"><i></i></div>
              <div class="kpi-sub">{n_cur:,} de {cur_total:,} · {meaning}</div>
              <div>{delta_html}</div>
            </div>""",
            unsafe_allow_html=True,
        )
    st.caption(
        f"Alvos {cur_mes_label}: Proficiência ≥{_ch['prof_min']}% · "
        f"Excelência ≥{_ch['excel_min']}% de acerto no mock. "
        f"Sem acerto canônico: {_meaning_dyn['Sem acerto canônico']}."
    )

    # --- Questões por aluno/semana: média e mediana do grupo filtrado.
    # Denominador = cohort inteiro (zeros incluídos — aluno sem atividade conta 0).
    # Última semana ISO completa = semana anterior à corrente; corrente = parcial.
    _cohort_ids_q = cohort["account_id"].astype(int).tolist()
    _cur_monday = reference_date - pd.Timedelta(days=int(reference_date.weekday()))
    _full_monday = _cur_monday - pd.Timedelta(days=7)

    def _q_semana(monday: pd.Timestamp) -> tuple[float, float] | None:
        sub = df[df["semana_iso"] == monday]
        if sub.empty:
            return None
        per = sub.groupby("account_id")["questoes"].sum().reindex(_cohort_ids_q, fill_value=0)
        return float(per.mean()), float(per.median())

    def _rng_semana(monday: pd.Timestamp) -> str:
        return f"{monday:%d/%m}–{monday + pd.Timedelta(days=6):%d/%m}"

    _q_full = _q_semana(_full_monday)
    _q_part = _q_semana(_cur_monday)
    if _q_full or _q_part:
        st.markdown("&nbsp;")
        qc1, qc2, qc3, qc4 = st.columns(4)
        for _col, _dados, _tit, _monday, _cor in (
            (qc1, _q_full, "Questões/aluno — média", _full_monday, "var(--emr-approved)"),
            (qc2, _q_full, "Questões/aluno — mediana", _full_monday, "var(--emr-approved)"),
            (qc3, _q_part, "Média (semana parcial)", _cur_monday, "var(--emr-text-muted)"),
            (qc4, _q_part, "Mediana (semana parcial)", _cur_monday, "var(--emr-text-muted)"),
        ):
            if _dados is None:
                continue
            _val = _dados[0] if "média" in _tit.lower() else _dados[1]
            _col.markdown(
                f"""<div class="kpi kpi-col">
                  <div class="kpi-eyebrow"><span class="dot" style="background:{_cor}"></span>{_tit}</div>
                  <div class="kpi-val">{_val:.1f}</div>
                  <div class="kpi-sub">semana {_rng_semana(_monday)} · zeros incluídos</div>
                </div>""",
                unsafe_allow_html=True,
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
            f"<br><span style='font-size:11px;color:#93A8A2;font-weight:400'>n={cur_counts[f]:,}</span>"
            f"</th>"
            for f in PRESCRIPTION_CLASSES
        )
        header_cells += (
            f"<th style='text-align:right'>Turma toda"
            f"<br><span style='font-size:11px;color:#93A8A2;font-weight:400'>n={cur_total:,}</span></th>"
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
    _df_acum = df[activity_month_labels(df) <= cur_mes]
    _acum_aluno = _df_acum.groupby("account_id", as_index=False).agg(
        questoes=("questoes", "sum"),
        flashcards=("flashcards", "sum"),
        blocos=("blocos", "sum"),
        dias_ativos=("dias_ativos", "sum"),
    )
    # Cohort elegível no mês com 0 pra quem ainda não fez nada
    _cohort_cur = cohort[cohort["account_id"].isin(cur_per["account_id"])]
    _acum_per = _cohort_cur[["account_id"]].merge(
        _acum_aluno, on="account_id", how="left"
    ).fillna(0)
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
            f"<br><span style='font-size:11px;color:#93A8A2;font-weight:400'>n={cur_counts[f]:,}</span>"
            f"</th>"
            for f in PRESCRIPTION_CLASSES
        )
        header_cells += (
            f"<th style='text-align:right'>Turma toda"
            f"<br><span style='font-size:11px;color:#93A8A2;font-weight:400'>n={cur_total:,}</span></th>"
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
    st.caption(
        "Cada ponto é 1 aluno. Posição = acumulado; cor e pisos = faixa do último mês fechado."
    )

    scatter_df = metrics_df.merge(
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
            "account_id": True, "questoes": ":,", "flashcards": ":,",
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
    fig.add_hline(y=ref["prof_min"], line_dash="dot", line_color="#50BCFF",
                  annotation_text=f"Piso Proficiência {PRESCRIPTION_VERSION} {ref['label']} ({ref['prof_min']:g}%)",
                  annotation_position="bottom right")
    fig.add_hline(y=ref["excel_min"], line_dash="dot", line_color="#6CE190",
                  annotation_text=f"Piso Excelência {PRESCRIPTION_VERSION} {ref['label']} ({ref['excel_min']:g}%)",
                  annotation_position="top right")
    fig.update_layout(
        height=360,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=-0.22),
    )
    st.plotly_chart(fig, width="stretch", key=f"{key_prefix}_scatter_map")


# Aba Agora — 9 sub-abas (Geral + 8 turmas). Reusa _render_agora_now via swap
# de globais (mesma estratégia da aba B2B).
def _render_agora_subtab(cohort_filtrado: pd.DataFrame, label_grupo: str, key_prefix: str):
    """Renderiza os blocos de Agora pra um subset filtrado do cohort B2C."""
    if cohort_filtrado.empty:
        st.warning(f"Sem alunos em {label_grupo}.")
        return
    cohort_uniq = deduplicate_accounts(cohort_filtrado)
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
        # "com Revisão" = flag transversal do cohort (combo, espelho ou standalone).
        # Guard: parquets anteriores a 2026-07 não têm a coluna.
        _rev_txt = ""
        if "tem_revisao" in cohort_filtrado.columns:
            _n_rev = int(cohort_filtrado.loc[
                cohort_filtrado["tem_revisao"].fillna(False).astype(bool), "account_id"
            ].nunique())
            _rev_txt = f" · {_n_rev:,} com Revisão ({_n_rev / total * 100:.0f}%)" if total else ""
        st.caption(f"**{label_grupo}** — {total:,} alunos{_rev_txt}. Snapshot {snapshot_date:%d/%m %H:%M}.")
        metrics_filtrado = metrics_acum[metrics_acum["account_id"].isin(ids)]
        _render_agora_now(
            metrics_filtrado,
            reference_date=today,
            key_prefix=key_prefix,
        )
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
def _render_b2b_subtab(cohort_filtrado: pd.DataFrame, label_grupo: str, key_prefix: str = "b2b",
                       ies_filtro: str | None = None):
    """Renderiza os blocos da aba Agora para um subset filtrado do cohort B2B.

    Faz swap das globais (df, cohort, total, last_closed, etc.) e passa as
    métricas B2B explicitamente. `ies_filtro` restringe a tabela de simulados
    institucionais às edições da IES do grupo (None = todas).
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
        cohort = deduplicate_accounts(cohort_filtrado)
        total = len(cohort)
        b2b_today = pd.Timestamp(snapshot_date_b2b.date())
        months_closed = closed_months(b2b_today, df_filtrado)
        if not months_closed:
            st.info(f"Sem mês fechado com atividade em `{label_grupo}`.")
            return
        last_closed = months_closed[-1]
        prev_closed = months_closed[-2] if len(months_closed) >= 2 else None
        cur_per = aggregate_month(df, last_closed, cohort_df=cohort)
        st.info(
            f"📊 **{label_grupo}** — {total:,} alunos. Mesma régua {PRESCRIPTION_VERSION} aplicada. "
            f"Snapshot {snapshot_date_b2b:%d/%m %H:%M}."
        )
        metrics_filtrado = metrics_acum_b2b[
            metrics_acum_b2b["account_id"].isin(ids_filtrados)
        ]
        _render_agora_now(
            metrics_filtrado,
            reference_date=b2b_today,
            key_prefix=key_prefix,
        )

        # --- Bloco simulados institucionais ENAMED 2026: desempenho do grupo ---
        st.markdown("&nbsp;")
        st.markdown("##### Simulados institucionais ENAMED 2026")
        st.caption(
            "Apenas mocks finalizados; aluno que fez 2 provas da mesma edição "
            "conta 1x (primeira tentativa). % ≥ 60 = alunos com 60%+ de acerto."
        )
        try:
            _enamed_ids = tuple(sorted(int(a) for a in cohort["account_id"].tolist()))
            enamed_df = load_enamed_2026_results(_enamed_ids)
        except Exception as _e:
            st.error(f"Falha ao carregar ENAMED: {_e}")
            enamed_df = pd.DataFrame()

        edicoes = [e for e in load_enamed_2026_templates()
                   if ies_filtro is None or e["ies"] == ies_filtro]
        if not edicoes:
            st.info(f"Nenhum simulado institucional mapeado para `{label_grupo}`.")
        elif enamed_df.empty:
            st.info("Nenhum aluno deste grupo finalizou um simulado institucional ENAMED 2026.")
        else:
            rows_html = []
            for ed in edicoes:
                sub = enamed_df[enamed_df["mock_template_id"].isin(ed["template_ids"])]
                # Defensivo: parquet novo já vem 1 linha por aluno por edição;
                # cobre schema antigo (aluno em 2 provas da mesma edição).
                sub = sub.drop_duplicates("account_id")
                if sub.empty:
                    rows_html.append(
                        f"<tr><td class='dim'>{ed['nome']}</td>"
                        f"<td class='val' style='color:#93A8A2'>{ed['data']}</td>"
                        f"<td class='val' style='color:#93A8A2' colspan='4'>sem participação</td></tr>"
                    )
                    continue
                n = len(sub)
                cov = n / total * 100 if total else 0.0
                media = float(sub["pct"].mean())
                mediana = float(sub["pct"].median())
                pct_60 = float((sub["pct"] >= 60).mean()) * 100
                cor60 = "#6CE190" if pct_60 >= 60 else ("#FFC805" if pct_60 >= 40 else "#FF514D")
                rows_html.append(
                    f"<tr>"
                    f"<td class='dim'>{ed['nome']}</td>"
                    f"<td class='val' style='color:#93A8A2'>{ed['data']}</td>"
                    f"<td class='val'>{n:,}<br><span style='font-size:11px;color:#93A8A2'>{cov:.0f}% do grupo</span></td>"
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
            # Agregado — só sobre as edições exibidas.
            _tpls_exibidos = {t for e in edicoes for t in e["template_ids"]}
            agg = enamed_df[enamed_df["mock_template_id"].isin(_tpls_exibidos)]
            if agg.empty:
                st.caption("Nenhuma participação deste grupo nas edições listadas.")
            else:
                tot_n = len(agg)
                tot_alunos = agg["account_id"].nunique()
                tot_pct_60 = (agg["pct"] >= 60).mean() * 100
                tot_media = agg["pct"].mean()
                st.caption(
                    f"Total: {tot_n:,} resultados · {tot_alunos:,}/{total:,} alunos · "
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
    elif not {"company_name", "ies_name"}.issubset(cohort_b2b.columns):
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
        sub_inspirali, sub_geral = st.tabs(["Inspirali", "Todos os grupos"])

        with sub_inspirali:
            insp_cohort_all = cohort_b2b[
                (cohort_b2b["company_name"] == "Inspirali")
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
                    _render_b2b_subtab(_filtrado, _label, key_prefix=f"b2b_insp_{cat_key}",
                                       ies_filtro="Inspirali")

        with sub_geral:
            # Lista grupos por company_name ÚNICO. O mesmo grupo (ex.: Inspirali, FMO,
            # FARESI) tem 2 company_id — o oficial e o company_id=1 (EMR, "Limbo IES"
            # de alunos atrelados ao parceiro). Deduplicar por (id, nome) repetia o
            # grupo no filtro; deduplicar só por nome lista cada grupo uma vez e o
            # filtro (isin company_name) já agrega os dois company_id.
            companies_list = sorted(
                cohort_b2b["company_name"].dropna().astype(str).unique().tolist()
            )
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
                # 1 grupo selecionado → só as edições da IES dele; vários → todas.
                _ies_unica = sel_co[0] if len(sel_co) == 1 else None
                _render_b2b_subtab(_filtrado, _label, key_prefix="b2b_geral_multi",
                                   ies_filtro=_ies_unica)


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
    coh, df_local, _ = _filter_by_ids(cohort_ids)
    rows = []
    for mes in months:
        per = aggregate_month(df_local, mes, cohort_df=coh)
        total_local = len(per)
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
            "n_elegiveis": len(per),
            "prof_min": PRESCRIPTION_MONTHLY[mes]["prof_min"],
            "prof_max": PRESCRIPTION_MONTHLY[mes]["prof_max"],
            "excel_min": PRESCRIPTION_MONTHLY[mes]["excel_min"],
            "excel_max": PRESCRIPTION_MONTHLY[mes]["excel_max"],
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=60 * 60)
def safra_excelencia(ref_mes: str, cohort_ids: tuple[int, ...] | None = None) -> pd.DataFrame:
    """% em Excelência por mês de entrada (first_start_date)."""
    coh_base, df_local, _ = _filter_by_ids(cohort_ids)
    per = aggregate_month(df_local, ref_mes, cohort_df=coh_base)
    coh = coh_base[coh_base["account_id"].isin(per["account_id"])][
        ["account_id", "first_start_date"]
    ].copy()
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

    month_columns = ["account_id", "semana_iso", "blocos", "questoes", "flashcards"]
    if "mes_ref" in df.columns:
        month_columns.append("mes_ref")
    weeks = df[month_columns].copy()
    weeks["mes"] = activity_month_labels(weeks)
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

    month_columns = ["account_id", "semana_iso", "blocos", "questoes", "flashcards"]
    if "mes_ref" in df_local.columns:
        month_columns.append("mes_ref")
    weeks = df_local[month_columns].copy()
    weeks["mes"] = activity_month_labels(weeks)
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
    _evo_cohort = deduplicate_accounts(_evo_cohort_sel)
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
        f"<div style='margin-top:-8px;margin-bottom:14px;color:#93A8A2;font-size:13px'>"
        f"<b>{_evo_label}</b> · {_evo_total:,} alunos</div>",
        unsafe_allow_html=True,
    )

    st.markdown("##### Distribuição da turma por faixa de desempenho, mês a mês")
    st.caption(
        'Faixas pela régua v2. Verde = Excelência; azul = Proficiência; '
        '"Sem acerto canônico" = não fez simulado válido no mês. Mais verde = turma melhor.'
    )

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
        height=300, plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=10, b=10),
        yaxis=dict(title="% da turma", ticksuffix="%", range=[0, 100]),
        xaxis=dict(title="Mês"),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25),
        hovermode="x unified",
    )
    st.plotly_chart(fig_area, width="stretch")

    # --- Bloco 2: acerto canônico mediano vs meta mensal v2 ---
    st.markdown("&nbsp;")
    st.markdown(f"##### % de acerto da turma vs. meta mensal da régua {PRESCRIPTION_VERSION}")
    st.caption(
        "Linha branca = mediana de acerto em simulados de quem fez simulado no mês "
        "(n no rótulo). Faixas = intervalo-meta mensal: verde = Excelência, "
        "azul = Proficiência."
    )

    ac_df = acerto_canonico_mensal(meses_tuple, _evo_filter)
    fig_line = go.Figure()
    # Bandas (canais) — desenha cada uma como duas linhas com fill='tonexty'
    fig_line.add_trace(go.Scatter(
        x=ac_df["mes_label"], y=ac_df["excel_max"], mode="lines",
        line=dict(width=0, color="#6CE190"), showlegend=False, hoverinfo="skip",
    ))
    fig_line.add_trace(go.Scatter(
        x=ac_df["mes_label"], y=ac_df["excel_min"], mode="lines",
        line=dict(width=0, color="#6CE190"),
        fill="tonexty", fillcolor="rgba(5,252,137,.55)",
        name=f"Canal Excelência {PRESCRIPTION_VERSION}",
        customdata=ac_df["excel_max"],
        hovertemplate=f"Excelência {PRESCRIPTION_VERSION}: %{{y:.1f}}%–%{{customdata:.1f}}%<extra></extra>",
    ))
    fig_line.add_trace(go.Scatter(
        x=ac_df["mes_label"], y=ac_df["prof_max"], mode="lines",
        line=dict(width=0, color="#50BCFF"), showlegend=False, hoverinfo="skip",
    ))
    fig_line.add_trace(go.Scatter(
        x=ac_df["mes_label"], y=ac_df["prof_min"], mode="lines",
        line=dict(width=0, color="#50BCFF"),
        fill="tonexty", fillcolor="rgba(50,87,138,.50)",
        name=f"Canal Proficiência {PRESCRIPTION_VERSION}",
        customdata=ac_df["prof_max"],
        hovertemplate=f"Proficiência {PRESCRIPTION_VERSION}: %{{y:.1f}}%–%{{customdata:.1f}}%<extra></extra>",
    ))
    fig_line.add_trace(go.Scatter(
        x=ac_df["mes_label"], y=ac_df["mediana_turma"],
        mode="lines+markers+text",
        line=dict(color="#F8F8F8", width=3),
        marker=dict(size=9, color="#F8F8F8"),
        name="Mediana turma R1 2026",
        text=[
            f"n={n}/{elegiveis}<br>({n/elegiveis*100:.0f}%)"
            if elegiveis else "n=0/0"
            for n, elegiveis in zip(ac_df["n_com_mock"], ac_df["n_elegiveis"])
        ],
        textposition="top center",
        textfont=dict(size=10, color="#F8F8F8"),
        hovertemplate=(
            "<b>Mediana turma</b>: %{y:.1f}%<br>"
            "%{text}<extra></extra>"
        ),
    ))
    fig_line.update_layout(
        height=340, plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=10, b=10),
        yaxis=dict(title="% acerto canônico", ticksuffix="%", range=[20, 90]),
        xaxis=dict(title="Mês"),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25),
        hovermode="x unified",
    )
    st.plotly_chart(fig_line, width="stretch")

    # --- Bloco 3: % em Excelência por mês de entrada ---
    st.markdown("&nbsp;")
    st.markdown(
        f"##### % de alunos em Excelência por mês de entrada na turma — referência "
        f"{PRESCRIPTION_MONTHLY[last_closed]['label']}"
    )
    st.caption(
        "Cada barra agrupa alunos pelo mês de matrícula. jan/26 inclui também "
        "veteranos de antes de 2026. "
        "Quem entrou antes teve mais tempo de curso — compare com cautela."
    )

    safra_df = safra_excelencia(last_closed, _evo_filter)
    if safra_df.empty:
        st.info("Sem dados para as turmas e meses selecionados.")
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
            height=300, plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=30, b=10),
            barmode="group",
            yaxis=dict(title="% da safra", ticksuffix="%"),
            xaxis=dict(title="Mês de entrada"),
            legend=dict(orientation="h", yanchor="bottom", y=-0.25),
        )
        st.plotly_chart(fig_safra, width="stretch")

    # --- Bloco 3b: % atingindo meta de volume por mês de entrada ---
    st.markdown("&nbsp;")
    st.markdown(
        f"##### % que atingiu a meta de volume de estudo acumulado, por mês de entrada "
        f"— referência {PRESCRIPTION_MONTHLY[last_closed]['label']}"
    )
    st.caption(
        "Volume = aulas assistidas + questões respondidas + flashcards revisados, "
        "somados desde a entrada. Unidades diferentes — leia como aproximação; "
        "não substitui a meta por atividade. Verde = atingiu a meta Excelência "
        f"({EXCEL_VOLUME_PROFILE}); azul = entre as metas Proficiência e Excelência."
    )

    vol_df = safra_volume(last_closed, _evo_filter)
    if vol_df.empty:
        st.info("Sem dados para as turmas e meses selecionados.")
    else:
        fig_vol = go.Figure()
        fig_vol.add_trace(go.Bar(
            x=vol_df["safra_label"], y=vol_df["pct_excel"],
            marker_color=PRESCRIPTION_COLORS["Excelência"],
            name=f"% ≥ Excelência ({EXCEL_VOLUME_PROFILE})",
            text=[f"{p:.0f}%<br>n={t}" for p, t in zip(vol_df["pct_excel"], vol_df["total"])],
            textposition="outside",
            customdata=list(zip(vol_df["total"], vol_df["target_excel"], vol_df["mediana_bqf"])),
            hovertemplate=(
                "Safra %{x}<br>"
                f"%{{y:.1f}}% ≥ target Excelência ({EXCEL_VOLUME_PROFILE})<br>"
                "Target Excelência acum: %{customdata[1]:,.0f}<br>"
                "Mediana B+Q+F acum: %{customdata[2]:,.0f}<br>"
                "n=%{customdata[0]}<extra></extra>"
            ),
        ))
        fig_vol.add_trace(go.Bar(
            x=vol_df["safra_label"], y=vol_df["pct_prof"],
            marker_color=PRESCRIPTION_COLORS["Proficiência"],
            name="% entre Proficiência e Excelência",
            customdata=list(zip(vol_df["total"], vol_df["target_prof"], vol_df["mediana_bqf"])),
            hovertemplate=(
                "Safra %{x}<br>"
                "%{y:.1f}% entre Proficiência e Excelência<br>"
                "Target Proficiência acum: %{customdata[1]:,.0f}<br>"
                "Mediana B+Q+F acum: %{customdata[2]:,.0f}<br>"
                "n=%{customdata[0]}<extra></extra>"
            ),
        ))
        fig_vol.update_layout(
            height=300, plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=30, b=10),
            barmode="group",
            yaxis=dict(title="% da safra", ticksuffix="%"),
            xaxis=dict(title="Mês de entrada"),
            legend=dict(orientation="h", yanchor="bottom", y=-0.25),
        )
        st.plotly_chart(fig_vol, width="stretch")

    # --- Bloco 5: métricas avançadas (collapsible) ---
    st.markdown("&nbsp;")
    st.markdown("##### Métricas semanais avançadas")
    st.caption(
        f"Excelência = alunos na faixa Excelência em {cur_mes_label}. Turma toda = "
        "todas as matrículas ativas. Volumes contam os zeros de quem não estudou "
        "(denominador = todos os pagantes, regra 2026-07-19). % de acerto: só quem "
        "respondeu na semana."
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

        agg_fn = "median" if agg_choice == "Mediana" else "mean"
        # Denominador dos volumes = TODOS os pagantes do grupo (zeros incluídos).
        # Regra 2026-07-19: métrica por aluno nunca compara só com ativos.
        ids_grupo = sorted(account_ids_filtro) if account_ids_filtro is not None else list(_evo_ids_tuple)

        plots = []
        for label in selected:
            col, tipo = METRICAS_AV[label]
            if tipo == "vol":
                serie = (
                    df_closed.pivot_table(index="semana_iso", columns="account_id",
                                          values=col, aggfunc="sum")
                    .reindex(columns=ids_grupo)
                    .fillna(0)
                    .agg(agg_fn, axis=1)
                    .rename("valor")
                    .reset_index()
                )
            else:
                # % de acerto: calcula pct por aluno-semana, depois agrega
                if tipo == "ratio_can":
                    num, den = "acerto_canonico_acertos", "acerto_canonico_questao_count"
                else:
                    num, den = "acertos_simples", "respostas_simples"
                com_resposta = weekly_student_ratio(df_closed, num, den)
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
            height=340, plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=10, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=-0.25),
        )
        st.plotly_chart(fig, width="stretch", key=f"{key_prefix}_chart")
        st.caption(f"{label_grupo}: {n_alunos:,} alunos.")

    sub_excel, sub_geral = st.tabs(["Excelência", "Turma toda"])
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
    st.caption(
        "Notas 1-5 dadas pelos alunos. Resultado oficial = último mês fechado "
        f"({PRESCRIPTION_MONTHLY[last_closed]['label']}); o mês corrente aparece à parte, como parcial."
    )

    _acc_ids = tuple(sorted(int(a) for a in cohort["account_id"].tolist()))
    try:
        aulas = load_avaliacoes_aulas(_acc_ids)
        questoes = load_avaliacoes_questoes(_acc_ids)
        materiais = load_avaliacoes_materiais(_acc_ids)
        flashcards = load_avaliacoes_flashcards(_acc_ids)
    except Exception as e:
        st.error(f"Falha ao carregar avaliações: {e}")
        st.stop()
    ebook = materiais[materiais["tipo_material"] == "EBOOK"].drop(columns=["tipo_material"])
    resumo = materiais[materiais["tipo_material"] == "SUMMARY"].drop(columns=["tipo_material"])
    mapa = materiais[materiais["tipo_material"] == "MIND_MAP"].drop(columns=["tipo_material"])
    apoio = materiais[materiais["tipo_material"] == "SUPPORTING_MATERIAL"].drop(columns=["tipo_material"])

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

    def _render_qualidade_table(d: pd.DataFrame, label: str, alunos_area: bool = False,
                                fonte_completa: pd.DataFrame | None = None,
                                ref: pd.Timestamp | None = None) -> str:
        if d.empty:
            _atip = (
                detect_atypical_collection(fonte_completa, ref)
                if fonte_completa is not None and ref is not None
                else None
            )
            _alerta = " ⚠️" if _atip is not None and _atip[0] else ""
            return (
                f"<tr><td class='dim'>{label}{_alerta}</td>"
                f"<td class='val' style='color:#93A8A2' colspan='6'>sem avaliações no mês</td></tr>"
            )
        # Geral primeiro
        geral_avg = float(d["rate"].mean())
        geral_n = int(len(d))
        geral_alunos = int(d["account_id"].nunique())
        por_area = _agg_area(d)
        cells = []
        # Flag de coleta atípica: volume do mês muito abaixo do histórico recente
        # (regra 2026-07-19/20 — não silenciar mês censurado como se fosse normal;
        # detecção estrutural, não conserto pontual de um mês só).
        _atip = (
            detect_atypical_collection(fonte_completa, ref)
            if fonte_completa is not None and ref is not None
            else None
        )
        _label_html = label
        if _atip is not None and _atip[0]:
            _label_html = (
                f"{label} "
                f"<span title='n={_atip[1]} vs mediana {_atip[2]:.0f} dos 3 meses anteriores "
                f"(&lt;40%) — coleta possivelmente incompleta; não comparar' "
                f"style='cursor:help'>⚠️</span>"
            )
        # Geral
        cells.append(
            f"<td class='val' style='font-weight:600;background:#264641'>"
            f"<span style='font-size:18px;color:#F8F8F8'>★ {geral_avg:.2f}</span>"
            f"<br><span style='font-size:11px;color:#93A8A2'>n={geral_n:,} · {geral_alunos:,} alunos</span></td>"
        )
        for sigla, bid in BIG_AREAS_ORDER:
            info = por_area.get(bid)
            if info is None:
                cells.append(
                    f"<td class='val' style='color:#93A8A2'>—<br><span style='font-size:11px'>sem dados</span></td>"
                )
            else:
                # Cor sutil: 4.5+ verde, 4.0-4.5 amarelo, <4 vermelho
                cor = "#6CE190" if info["avg"] >= 4.5 else ("#FFC805" if info["avg"] >= 4.0 else "#FF514D")
                extra = f" · {info['n_alunos']:,} alunos" if alunos_area else ""
                cells.append(
                    f"<td class='val'>"
                    f"<span style='font-size:16px;color:{cor};font-weight:600'>★ {info['avg']:.2f}</span>"
                    f"<br><span style='font-size:11px;color:#93A8A2'>n={info['n']:,}{extra}</span></td>"
                )
        return (
            f"<tr><td class='dim'>{_label_html}</td>{''.join(cells)}</tr>"
        )

    # Header da tabela combinada
    sigla_to_nome = {sigla: BIG_AREAS_NOMES[bid] for sigla, bid in BIG_AREAS_ORDER}
    header_cols = (
        "<th style='text-align:right;background:#264641'>Geral</th>"
        + "".join(
            f"<th style='text-align:right' title='{sigla_to_nome[sigla]}'>{sigla}</th>"
            for sigla, _ in BIG_AREAS_ORDER
        )
    )

    def _tabela_qualidade(rows_html: str) -> None:
        st.markdown(
            f"<table class='gap-table'>"
            f"<thead><tr><th>Conteúdo</th>{header_cols}</tr></thead>"
            f"<tbody>{rows_html}</tbody></table>",
            unsafe_allow_html=True,
        )

    _CONTEUDOS_TABELA = [
        ("Aulas", aulas),
        ("Explicações de questões", questoes),
        ("E-book", ebook),
        ("Resumo", resumo),
        ("Mapa mental", mapa),
        ("Material de apoio", apoio),
        ("Flashcards", flashcards),
    ]

    def _slice_mes(d: pd.DataFrame, ref: pd.Timestamp) -> pd.DataFrame:
        return d[pd.to_datetime(d["mes"]) == ref] if not d.empty else d

    # Resultado oficial = só o último mês fechado. Parcial = mês mais recente
    # nos dados depois do fechado (em produção, o mês calendário corrente).
    _mes_oficial = pd.Timestamp(f"{last_closed}-01")
    st.markdown(
        f"###### Resultado oficial — {PRESCRIPTION_MONTHLY[last_closed]['label']} (mês fechado)"
    )
    _tabela_qualidade("".join(
        _render_qualidade_table(_slice_mes(d, _mes_oficial), lab,
                                alunos_area=(lab == "Explicações de questões"),
                                fonte_completa=d, ref=_mes_oficial)
        for lab, d in _CONTEUDOS_TABELA
    ))
    if any(
        (_r := detect_atypical_collection(d, _mes_oficial)) is not None and _r[0]
        for _lab, d in _CONTEUDOS_TABELA
    ):
        st.caption(
            "⚠️ Conteúdo(s) com volume de avaliações muito abaixo do histórico recente no mês oficial — "
            "possível falha de coleta; tratar a média como não comparável."
        )

    _meses_all = pd.concat(
        [pd.to_datetime(d["mes"]) for _l, d in _CONTEUDOS_TABELA if not d.empty],
        ignore_index=True,
    ) if any(not d.empty for _l, d in _CONTEUDOS_TABELA) else pd.Series(dtype="datetime64[ns]")
    _ref_parcial = _meses_all.max() if not _meses_all.empty else None
    if _ref_parcial is not None and _ref_parcial > _mes_oficial:
        _lbl_parcial = _format_mes(_ref_parcial.strftime("%Y-%m"))
        st.markdown("&nbsp;")
        st.markdown(f"###### Mês corrente — {_lbl_parcial} (parcial, em andamento)")
        _parciais = {lab: _slice_mes(d, _ref_parcial) for lab, d in _CONTEUDOS_TABELA}
        # Sem flag de coleta atípica no mês parcial: n baixo aqui é só mês
        # incompleto, e o título já diz "(parcial, em andamento)".
        _tabela_qualidade("".join(
            _render_qualidade_table(p, lab, alunos_area=(lab == "Explicações de questões"))
            for lab, p in _parciais.items()
        ))
        _baixos = [
            (lab, float(p["rate"].mean()))
            for lab, p in _parciais.items()
            if not p.empty and float(p["rate"].mean()) <= 4.0
        ]
        if _baixos:
            st.warning(
                "Média parcial ≤ 4,0 em: "
                + " · ".join(f"**{lab}** ({v:.2f})" for lab, v in _baixos)
                + f" — {_lbl_parcial} ainda em andamento, amostra parcial."
            )
    st.caption("Cores: 🟢 ≥4,5 · 🟡 4,0-4,5 · 🔴 <4,0 · n = avaliações. ⚠️ = coleta atípica (n < 40% da mediana dos 3 meses anteriores).")

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
    _CONTEUDOS_QUAL = [
        "Aulas",
        "Explicações de questões",
        "E-book",
        "Resumo",
        "Mapa mental",
        "Material de apoio",
        "Flashcards",
    ]
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
        "Explicações de questões": questoes,
        "E-book": ebook,
        "Resumo": resumo,
        "Mapa mental": mapa,
        "Material de apoio": apoio,
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
                "Aulas": "#50BCFF",
                "Explicações de questões": "#9500DB",
                "E-book": "#6CE190",
                "Resumo": "#FFC805",
                "Mapa mental": "#FF7013",
                "Material de apoio": "#8FA39D",
                "Flashcards": "#B4F900",
            },
        )
        # Anotação do n em cada ponto
        for _, row in series.iterrows():
            fig.add_annotation(
                x=row["mes"], y=row["valor"],
                text=f"n={row['n']}",
                showarrow=False, yshift=12,
                font=dict(size=10, color="#93A8A2"),
            )
        fig.update_layout(
            height=420,
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=10, b=10),
            yaxis=dict(range=[1, 5], dtick=0.5),
            xaxis=dict(tickformat="%b/%y"),
            legend=dict(orientation="h", yanchor="bottom", y=-0.25),
        )
        st.plotly_chart(fig, width="stretch")


# ============================================================
# ABA VOZ DO ALUNO — feedback espontâneo, sem IA
# ============================================================

with tab_voz:
    st.markdown("##### Voz do aluno")
    st.caption(
        "Feedbacks espontâneos de aulas, explicações de questões, "
        "materiais e flashcards. Reportes de erro de flashcards não têm nota e "
        "são tratados no fluxo de correção de conteúdo, fora desta aba."
    )
    st.info(
        "Feedback espontâneo tem viés de autoseleção: responde quem decide avaliar, "
        "muitas vezes após experiências muito boas ou ruins. Os percentuais descrevem "
        "os registros recebidos, não representam todos os alunos."
    )
    st.warning(
        "Uso interno. A sanitização automática remove identificadores comuns, mas "
        "texto livre ainda pode conter nomes ou outros dados pessoais digitados pelo aluno."
    )

    try:
        feedbacks = load_feedbacks_organicos()
        feedbacks = _feedbacks_com_nota_valida(feedbacks)
    except Exception as e:
        st.error(f"Falha ao carregar feedbacks: {e}")
        feedbacks = pd.DataFrame()

    if feedbacks.empty:
        st.info(
            "Nenhum feedback no snapshot atual. A carga é diária — se persistir "
            "por mais de um dia, verificar o ETL."
        )
    else:
        feedbacks = feedbacks.copy()
        feedbacks["data"] = pd.to_datetime(feedbacks["data"], errors="coerce")
        feedbacks = feedbacks.dropna(subset=["data"])
        feedbacks["mes"] = feedbacks["data"].dt.to_period("M").astype(str)

        for coluna in ("segmento", "origem", "classificacao"):
            feedbacks[coluna] = feedbacks[coluna].fillna("Não informado").astype(str)

        segmentos = sorted(feedbacks["segmento"].unique())
        origens = sorted(feedbacks["origem"].unique())
        notas_opcoes = ["1", "2", "3", "4", "5"]
        meses = sorted(feedbacks["mes"].unique(), reverse=True)

        f_segmento, f_origem, f_nota, f_mes = st.columns(4)
        with f_segmento:
            segmentos_sel = st.multiselect(
                "Segmento", segmentos, default=segmentos, key="voz_segmento"
            )
        with f_origem:
            origens_sel = st.multiselect(
                "Origem", origens, default=origens, key="voz_origem"
            )
        with f_nota:
            notas_sel = st.multiselect(
                "Nota",
                notas_opcoes,
                default=notas_opcoes,
                key="voz_nota",
            )
        with f_mes:
            mes_sel = st.selectbox(
                "Mês",
                [None, *meses],
                format_func=lambda mes: "Todos" if mes is None else _format_mes(mes),
                key="voz_mes",
            )

        base_kpi = feedbacks[
            feedbacks["segmento"].isin(segmentos_sel)
            & feedbacks["origem"].isin(origens_sel)
        ].copy()
        if mes_sel is not None:
            base_kpi = base_kpi[base_kpi["mes"] == mes_sel]
        notas_selecionadas = [int(nota) for nota in notas_sel]
        filtrados = base_kpi[base_kpi["nota"].isin(notas_selecionadas)].copy()

        tem_texto = filtrados["tem_texto"].fillna(False).astype(bool)
        registros = len(filtrados)
        comentarios = int(tem_texto.sum())
        alunos = int(filtrados["aluno_hash"].nunique())
        pct_criticos = _feedback_critical_pct(base_kpi)

        k_registros, k_comentarios, k_alunos, k_criticos = st.columns(4)
        k_registros.metric("Registros", f"{registros:,}")
        k_comentarios.metric("Comentários", f"{comentarios:,}")
        k_alunos.metric("Alunos com feedback", f"{alunos:,}")
        k_criticos.metric(
            "% críticos nas notas",
            f"{pct_criticos:.1f}%",
            help=(
                "Notas 1 e 2 sobre o total de notas do recorte (segmento, origem e "
                "mês). O filtro Nota não altera este percentual — ele descreve o "
                "recorte inteiro."
            ),
        )

        graf_notas, graf_mensal = st.columns([1, 2])
        with graf_mensal:
            st.markdown("###### Comentários por mês e origem")
            comentarios_df = filtrados[tem_texto].copy()
            if comentarios_df.empty:
                st.info(
                    "Sem comentários neste recorte. Amplie os filtros de segmento, "
                    "origem, nota ou mês."
                )
            else:
                comentarios_df["mes_data"] = (
                    comentarios_df["data"].dt.to_period("M").dt.to_timestamp()
                )
                serie_comentarios = (
                    comentarios_df.groupby(["mes_data", "origem"])
                    .size()
                    .rename("comentarios")
                    .reset_index()
                )
                fig_comentarios = px.bar(
                    serie_comentarios,
                    x="mes_data",
                    y="comentarios",
                    color="origem",
                    labels={
                        "mes_data": "Mês",
                        "comentarios": "Comentários",
                        "origem": "Origem",
                    },
                )
                fig_comentarios.update_layout(
                    height=360,
                    barmode="stack",
                    margin=dict(l=10, r=10, t=10, b=10),
                    xaxis=dict(tickformat="%b/%y"),
                    legend=dict(orientation="h", yanchor="bottom", y=-0.3),
                )
                st.plotly_chart(fig_comentarios, width="stretch")

        with graf_notas:
            st.markdown("###### Distribuição das notas")
            notas = pd.to_numeric(filtrados["nota"], errors="coerce").dropna()
            notas = notas[notas.between(1, 5)]
            if notas.empty:
                st.info(
                    "Sem notas neste recorte. Amplie os filtros de segmento, origem, "
                    "nota ou mês."
                )
            else:
                dist_notas = (
                    notas.astype(int)
                    .value_counts()
                    .reindex(range(1, 6), fill_value=0)
                    .rename_axis("nota")
                    .rename("registros")
                    .reset_index()
                )
                fig_notas = px.bar(
                    dist_notas,
                    x="nota",
                    y="registros",
                    labels={"nota": "Nota", "registros": "Registros"},
                    color_discrete_sequence=[EMR["approved"]],
                )
                fig_notas.update_layout(
                    height=360,
                    margin=dict(l=10, r=10, t=10, b=10),
                    xaxis=dict(tickmode="array", tickvals=[1, 2, 3, 4, 5]),
                    showlegend=False,
                )
                st.plotly_chart(fig_notas, width="stretch")

        st.markdown("###### Feedbacks recentes")
        busca = st.text_input(
            "Buscar no texto, conteúdo, professor ou origem",
            placeholder="Digite um termo…",
            key="voz_busca",
        ).strip()

        feed = filtrados[tem_texto].copy()
        feed["texto"] = (
            feed["texto"].fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
        )
        if "conteudo_id" not in feed.columns:
            feed["conteudo_id"] = pd.NA
        feed["conteudo_id"] = feed["conteudo_id"].replace("", pd.NA).fillna("—")
        if busca:
            colunas_busca = ["texto", "conteudo", "professor", "origem"]
            haystack = (
                feed[colunas_busca]
                .fillna("")
                .astype(str)
                .agg(" ".join, axis=1)
            )
            feed = feed[haystack.str.contains(busca, case=False, regex=False)]

        feed = feed.sort_values("data", ascending=False).head(500)
        if feed.empty:
            st.info("Nenhum comentário encontrado.")
        else:
            feed_publico = feed[
                [
                    "data",
                    "segmento",
                    "origem",
                    "nota",
                    "texto",
                    "conteudo",
                    "conteudo_id",
                    "professor",
                ]
            ].rename(
                columns={
                    "data": "Data",
                    "segmento": "Segmento",
                    "origem": "Origem",
                    "nota": "Nota",
                    "texto": "Feedback",
                    "conteudo": "Conteúdo",
                    "conteudo_id": "ID",
                    "professor": "Professor",
                }
            )
            st.dataframe(
                feed_publico,
                hide_index=True,
                width="stretch",
                column_config={
                    "Data": st.column_config.DatetimeColumn(format="DD/MM/YYYY"),
                    "Feedback": st.column_config.TextColumn(width="large"),
                    "Conteúdo": st.column_config.TextColumn(width="medium"),
                },
            )
            st.caption(
                f"{len(feed_publico):,} comentário(s) exibido(s), limitados aos "
                "500 mais recentes. Textos sanitizados no ETL e exibidos sem HTML. "
                "Para flashcards, a nota se refere ao baralho (deck), não a uma carta "
                "individual — o ID exibido é o ID do deck."
            )
