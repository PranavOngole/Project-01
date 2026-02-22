"""
app/main.py
Project-01 — AI-Powered Stock Research Platform
Streamlit entry point.

Run: streamlit run app/main.py

Phase 3B: Real stock data from yfinance displayed in a clean card.
Phase 4:  AI agent pipeline (Claude) produces the full research report.

Data delay: yfinance is 15-20 minutes delayed. Never says "real-time."
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Project root on path before any local imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

# ── Page config — MUST be first Streamlit call ────────────────────────────────
st.set_page_config(
    page_title="Project-01 | AI Stock Research",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={
        "Get Help": "https://github.com/PranavOngole/Project-01",
        "Report a bug": "https://github.com/PranavOngole/Project-01/issues",
        "About": "Project-01 — AI-Powered Stock Research Platform by Pranav Ongole",
    },
)

# ── Late imports (after path setup) ───────────────────────────────────────────
from config import settings
from data.schema import setup_database
from data.pipeline import PipelineResult, StockCard, run_full_pipeline

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
EST = ZoneInfo("America/New_York")

_DISCLAIMER_FULL = """
**This platform is for informational and educational purposes only.**

Nothing produced by Project-01 — including the Value Conviction Score, purchase price
recommendations, or any analysis output — constitutes financial advice, investment advice,
or a recommendation to buy, sell, or hold any security.

**Key points:**
- AI-generated analysis may contain errors, hallucinations, or outdated information
- The Value Conviction Score and Purchase Price estimates are **not guarantees** of future performance
- Always conduct your own due diligence (DYOR) before making any investment decisions
- Consult a qualified financial professional before acting on any information here
- Past performance of any stock is not indicative of future results
- Project-01 is **not** a registered investment advisor

Market data is sourced from yfinance and is **15-20 minutes delayed. This is NOT real-time.**
"""

# ── CSS ───────────────────────────────────────────────────────────────────────
_CSS = """
<style>
/* Hide default Streamlit chrome */
#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }
header    { visibility: hidden; }

/* Section labels */
.section-label {
    font-size: 0.70rem;
    font-weight: 600;
    letter-spacing: 1.8px;
    text-transform: uppercase;
    color: #6e7681;
    margin-bottom: 0.3rem;
}

/* Stock card header */
.ticker-badge {
    font-size: 2.4rem;
    font-weight: 800;
    color: #e6edf3;
    letter-spacing: -0.5px;
    line-height: 1;
}
.company-badge {
    font-size: 1.0rem;
    color: #8b949e;
    margin-top: 0.2rem;
}
.exchange-pill {
    display: inline-block;
    background: #21262d;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 0.1rem 0.5rem;
    font-size: 0.72rem;
    font-weight: 600;
    color: #4f8ef7;
    margin-right: 0.4rem;
}
.sector-pill {
    display: inline-block;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 0.1rem 0.5rem;
    font-size: 0.72rem;
    color: #8b949e;
}

/* Price display */
.price-main {
    font-size: 2.2rem;
    font-weight: 700;
    color: #e6edf3;
    line-height: 1.1;
}
.change-positive { color: #3fb950; font-weight: 600; font-size: 1.05rem; }
.change-negative { color: #f85149; font-weight: 600; font-size: 1.05rem; }
.change-neutral  { color: #8b949e; font-weight: 600; font-size: 1.05rem; }

/* Stat boxes */
.stat-card {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 8px;
    padding: 0.9rem 1.1rem;
}
.stat-label { font-size: 0.70rem; color: #6e7681; margin-bottom: 0.25rem; letter-spacing: 0.8px; text-transform: uppercase; }
.stat-value { font-size: 1.15rem; font-weight: 600; color: #e6edf3; }

/* Delay badge */
.delay-badge {
    display: inline-block;
    background: #1c2128;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 0.15rem 0.6rem;
    font-size: 0.72rem;
    color: #e3a520;
    font-weight: 500;
}

/* Disclaimer box */
.disclaimer-inline {
    background: #161b22;
    border: 1px solid #30363d;
    border-left: 4px solid #e3a520;
    border-radius: 6px;
    padding: 0.7rem 1rem;
    font-size: 0.80rem;
    color: #8b949e;
    margin: 0.8rem 0;
}

/* Phase 4 teaser */
.phase4-box {
    background: linear-gradient(135deg, #161b22 0%, #1c2333 100%);
    border: 1px solid #30363d;
    border-left: 4px solid #4f8ef7;
    border-radius: 8px;
    padding: 1.2rem 1.5rem;
    margin: 1.2rem 0;
}

/* Footer */
.footer-bar {
    position: fixed; bottom: 0; left: 0; right: 0;
    background: #0d1117;
    border-top: 1px solid #21262d;
    padding: 0.5rem 2rem;
    font-size: 0.73rem;
    color: #6e7681;
    text-align: center;
    z-index: 999;
}
.main .block-container { padding-bottom: 4rem; }

/* Error card */
.error-card {
    background: #1a1012;
    border: 1px solid #f8514933;
    border-left: 4px solid #f85149;
    border-radius: 8px;
    padding: 1rem 1.2rem;
    margin: 0.8rem 0;
}
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)


# ── DB init ───────────────────────────────────────────────────────────────────

@st.cache_resource
def _init_db() -> None:
    """Initialize DuckDB schema once per process lifetime."""
    setup_database()


_init_db()


# ── Session state ─────────────────────────────────────────────────────────────

def _init_state() -> None:
    defaults = {
        "result": None,          # PipelineResult | None
        "last_ticker": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()


# ── Components ────────────────────────────────────────────────────────────────

def render_disclaimer_banner() -> None:
    with st.expander("⚠️  SEC Disclaimer & Legal Notice — Required Reading", expanded=False):
        st.markdown(_DISCLAIMER_FULL)


def render_footer() -> None:
    st.markdown(
        '<div class="footer-bar">'
        "Not financial advice. AI-generated analysis for educational purposes only. "
        "Data delayed 15-20 min &nbsp;|&nbsp; "
        "Project-01 by Pranav Ongole"
        "</div>",
        unsafe_allow_html=True,
    )


def _fmt_price(p: float | None) -> str:
    if p is None:
        return "N/A"
    return f"${p:,.2f}"


def _fmt_volume(v: int | None) -> str:
    if v is None:
        return "N/A"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return str(v)


def _fmt_change(card: StockCard) -> str:
    if card.change_usd is None or card.change_pct is None:
        return ""
    sign = "+" if card.change_usd >= 0 else ""
    arrow = "▲" if card.change_usd >= 0 else "▼"
    return f"{arrow} {sign}{card.change_usd:,.2f} ({sign}{card.change_pct:.2f}%)"


def _change_class(card: StockCard) -> str:
    if card.change_usd is None:
        return "change-neutral"
    return "change-positive" if card.change_usd >= 0 else "change-negative"


def render_stock_card(card: StockCard) -> None:
    """The main stock data display card."""

    # ── Header row ────────────────────────────────────────────────────────────
    col_info, col_price = st.columns([3, 2])

    with col_info:
        st.markdown(
            f'<div class="ticker-badge">{card.ticker}</div>'
            f'<div class="company-badge">{card.company_name}</div>'
            f'<div style="margin-top:0.5rem;">'
            f'  <span class="exchange-pill">{card.exchange}</span>'
            f'  <span class="sector-pill">{card.sector}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with col_price:
        price_str = _fmt_price(card.current_price)
        change_str = _fmt_change(card)
        change_cls = _change_class(card)
        st.markdown(
            f'<div class="price-main">{price_str}</div>'
            f'<div class="{change_cls}">{change_str}</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Stat row ──────────────────────────────────────────────────────────────
    s1, s2, s3, s4 = st.columns(4)

    with s1:
        st.markdown(
            '<div class="stat-card">'
            '<div class="stat-label">Market Cap</div>'
            f'<div class="stat-value">{card.market_cap_fmt}</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    with s2:
        st.markdown(
            '<div class="stat-card">'
            '<div class="stat-label">Volume</div>'
            f'<div class="stat-value">{_fmt_volume(card.volume)}</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    with s3:
        hi = _fmt_price(card.fifty_two_wk_high)
        lo = _fmt_price(card.fifty_two_wk_low)
        st.markdown(
            '<div class="stat-card">'
            '<div class="stat-label">52-Week Range</div>'
            f'<div class="stat-value" style="font-size:0.95rem;">{lo} – {hi}</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    with s4:
        st.markdown(
            '<div class="stat-card">'
            '<div class="stat-label">Industry</div>'
            f'<div class="stat-value" style="font-size:0.85rem;line-height:1.3;">{card.industry}</div>'
            '</div>',
            unsafe_allow_html=True,
        )

    # ── Timestamp ─────────────────────────────────────────────────────────────
    fetched_est = card.fetched_at.astimezone(EST)
    ts_str = fetched_est.strftime("%I:%M %p EST, %b %d %Y").lstrip("0")
    st.markdown(
        f'<p style="font-size:0.75rem;color:#6e7681;margin-top:0.8rem;">'
        f'Data as of {ts_str} &nbsp;'
        f'<span class="delay-badge">15-20 min delay · Not real-time</span>'
        f'</p>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="disclaimer-inline">'
        "⚠️ <strong>For educational use only.</strong> "
        "Price and change data is 15-20 minutes delayed from yfinance. "
        "Not investment advice."
        "</div>",
        unsafe_allow_html=True,
    )

    # ── Phase 4 teaser ────────────────────────────────────────────────────────
    st.markdown(
        '<div class="phase4-box">'
        "<strong>🤖 AI Research Report</strong> — <em>Phase 4 · Coming Soon</em><br>"
        '<span style="font-size:0.85rem;color:#8b949e;">'
        "Nine Claude AI agents will analyze this stock and produce:<br>"
        "Value Conviction Score (0–100) · Technical Analysis (RSI, MACD, Bollinger) · "
        "Fundamental Deep Dive · SEC Filing Digest · Competitor Comparison · "
        "Purchase Price Recommendation"
        "</span>"
        "</div>",
        unsafe_allow_html=True,
    )


def render_error_card(result: PipelineResult) -> None:
    """Display a clear, specific error message for invalid tickers."""
    icon_map = {
        "format":      "✏️",
        "not_found":   "🔍",
        "exchange":    "🏛️",
        "market_cap":  "📉",
        "history":     "📅",
        "data_error":  "⚡",
        "db_error":    "🗄️",
    }
    icon = icon_map.get(result.error_type or "", "⚠️")
    st.markdown(
        f'<div class="error-card">'
        f'<strong>{icon} Cannot analyze {result.ticker}</strong><br>'
        f'<span style="font-size:0.9rem;color:#8b949e;">{result.error}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_landing() -> None:
    """Shown when no ticker has been searched yet."""
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        '<p style="font-size:2.2rem;font-weight:700;color:#e6edf3;letter-spacing:-0.5px;">'
        "Research any NYSE or NASDAQ stock in seconds."
        "</p>"
        '<p style="font-size:1.05rem;color:#8b949e;margin-bottom:2rem;">'
        "Type a ticker above. Get real market data now — AI research report in Phase 4."
        "</p>",
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**📊 Value Conviction Score**")
        st.caption("0–100 composite score from fundamentals, technicals, sentiment, and competitive position.")
        st.markdown("**📰 SEC Filing Digest**")
        st.caption("Latest 10-K / 10-Q highlights from the Finance Researcher agent.")
    with c2:
        st.markdown("**📈 Technical Analysis**")
        st.caption("RSI · MACD · Bollinger Bands · volume profile · support/resistance — interactive Plotly charts.")
        st.markdown("**🏢 Competitor Comparison**")
        st.caption("Automated peer-set selection with side-by-side KPI table.")
    with c3:
        st.markdown("**💼 Fundamental Deep Dive**")
        st.caption("Revenue trends, margins, FCF, balance sheet health, earnings quality, ratio benchmarking.")
        st.markdown("**✅ QA-Validated Output**")
        st.caption("Every report passes a dedicated QA agent before reaching the UI.")

    st.markdown("---")
    st.caption(
        "**Stock universe:** NYSE and NASDAQ equities · Market cap ≥ $500M · "
        "2+ years price history · No ETFs, ADRs, or SPACs."
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:

    # ── Header ────────────────────────────────────────────────────────────────
    hdr_l, hdr_r = st.columns([4, 1])
    with hdr_l:
        st.markdown(
            '<p style="font-size:1.05rem;font-weight:700;color:#e6edf3;margin:0;">'
            "📊 Project-01 &nbsp;|&nbsp; "
            '<span style="font-weight:400;color:#8b949e;">AI Stock Research</span>'
            "</p>",
            unsafe_allow_html=True,
        )
    with hdr_r:
        st.caption(f"ENV: {settings.APP_ENV.upper()}")

    # ── SEC Disclaimer (always visible) ───────────────────────────────────────
    render_disclaimer_banner()

    # ── Search form ───────────────────────────────────────────────────────────
    # st.form prevents reruns on every keystroke — button is the only trigger
    with st.form("search_form", clear_on_submit=False):
        inp_col, btn_col = st.columns([5, 1])
        with inp_col:
            ticker_raw = st.text_input(
                label="ticker",
                label_visibility="collapsed",
                placeholder="Enter a ticker — AAPL, MSFT, NVDA, JPM...",
                value=st.session_state.last_ticker,
            )
        with btn_col:
            submitted = st.form_submit_button(
                "Analyze →", use_container_width=True, type="primary"
            )

    # ── Pipeline trigger ──────────────────────────────────────────────────────
    if submitted and ticker_raw.strip():
        ticker_clean = ticker_raw.strip().upper()
        st.session_state.last_ticker = ticker_clean

        with st.spinner(f"Fetching data for **{ticker_clean}** from yfinance…"):
            result = run_full_pipeline(ticker_clean)

        st.session_state.result = result

    # ── Render result ─────────────────────────────────────────────────────────
    result: PipelineResult | None = st.session_state.result

    if result is not None:
        if result.success and result.stock_card:
            render_stock_card(result.stock_card)
        else:
            render_error_card(result)
    else:
        render_landing()

    # ── Footer ────────────────────────────────────────────────────────────────
    render_footer()


if __name__ == "__main__":
    main()
