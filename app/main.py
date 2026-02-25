"""
app/main.py
POV Research Platform — Streamlit application.

Phase 3B: Stock data display skeleton.
Phase 4:  AI agent pipeline wired in (reports, conviction score, signals).

Run: streamlit run app/main.py

Data: yfinance (15-20 min delayed). Never says "real-time."
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

# ── Page config — must be FIRST Streamlit call ────────────────────────────────
st.set_page_config(
    page_title="POV Research Platform",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={
        "Get Help":    "https://github.com/PranavOngole/Project-01",
        "Report a bug":"https://github.com/PranavOngole/Project-01/issues",
        "About":       "POV Research Platform — AI-Powered Stock Analysis by Pranav Ongole",
    },
)

from config import settings
from data.schema import init_db, get_connection
from data.pipeline import run_full_pipeline, PipelineResult, StockCard

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

EST = ZoneInfo("America/New_York")

# ── DB init (idempotent — safe every startup) ─────────────────────────────────

@st.cache_resource
def _init_db() -> None:
    init_db()

_init_db()


# ── CSS ───────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }
header    { visibility: hidden; }

/* ── Header ── */
.pov-header {
    padding: 0.6rem 0 0.4rem 0;
    border-bottom: 1px solid #21262d;
    margin-bottom: 1rem;
}
.pov-title {
    font-size: 1.5rem;
    font-weight: 800;
    color: #e6edf3;
    letter-spacing: -0.3px;
    line-height: 1;
    margin: 0;
}
.pov-tagline {
    font-size: 0.82rem;
    color: #8b949e;
    margin-top: 0.15rem;
}

/* ── Disclaimer ── */
.disclaimer-bar {
    background: #1a1012;
    border: 1px solid #f8514922;
    border-left: 3px solid #e3a520;
    border-radius: 6px;
    padding: 0.5rem 1rem;
    font-size: 0.78rem;
    color: #8b949e;
    margin-bottom: 1rem;
}

/* ── Section labels ── */
.section-label {
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 1.8px;
    text-transform: uppercase;
    color: #6e7681;
    margin-bottom: 0.4rem;
}

/* ── Stock name block ── */
.ticker-name {
    font-size: 2rem;
    font-weight: 800;
    color: #e6edf3;
    letter-spacing: -0.5px;
    line-height: 1;
}
.company-name {
    font-size: 1rem;
    color: #8b949e;
    margin-top: 0.2rem;
}
.pill {
    display: inline-block;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 0.1rem 0.5rem;
    font-size: 0.70rem;
    font-weight: 600;
    margin-right: 0.3rem;
    margin-top: 0.4rem;
}
.pill-exchange { background: #161b22; color: #4f8ef7; }
.pill-sector   { background: #161b22; color: #8b949e; }

/* ── Data cards ── */
.data-card {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 8px;
    padding: 0.85rem 1rem;
    height: 100%;
}
.card-label {
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: #6e7681;
    margin-bottom: 0.2rem;
}
.card-value {
    font-size: 1.1rem;
    font-weight: 600;
    color: #e6edf3;
}
.card-value-sm {
    font-size: 0.88rem;
    font-weight: 500;
    color: #e6edf3;
}
.card-sub {
    font-size: 0.72rem;
    color: #6e7681;
    margin-top: 0.1rem;
}

/* ── Timestamp ── */
.ts-row {
    font-size: 0.73rem;
    color: #6e7681;
    margin: 0.8rem 0 0.3rem 0;
}
.delay-tag {
    display: inline-block;
    background: #1c2128;
    border: 1px solid #30363d;
    border-radius: 3px;
    padding: 0.08rem 0.45rem;
    font-size: 0.68rem;
    color: #e3a520;
    font-weight: 500;
}

/* ── Error card ── */
.error-card {
    background: #1a1012;
    border: 1px solid #f8514933;
    border-left: 4px solid #f85149;
    border-radius: 8px;
    padding: 1rem 1.2rem;
    margin-top: 1rem;
}

/* ── Phase 4 teaser ── */
.phase4-box {
    background: linear-gradient(135deg, #161b22 0%, #1c2333 100%);
    border: 1px solid #30363d;
    border-left: 4px solid #4f8ef7;
    border-radius: 8px;
    padding: 1.1rem 1.4rem;
    margin-top: 1.2rem;
}

/* ── Footer ── */
.footer-bar {
    position: fixed; bottom: 0; left: 0; right: 0;
    background: #0d1117;
    border-top: 1px solid #21262d;
    padding: 0.45rem 2rem;
    font-size: 0.70rem;
    color: #6e7681;
    text-align: center;
    z-index: 999;
}
.main .block-container { padding-bottom: 4rem; }
</style>
""", unsafe_allow_html=True)

_DISCLAIMER_FULL = """
**This platform is for informational and educational purposes only.**

Nothing produced by POV Research Platform — including any stock analysis, scores, price targets,
or signals — constitutes financial advice, investment advice, or a recommendation to buy, sell,
or hold any security.

- AI-generated analysis may contain errors or outdated information
- Market data is sourced from Yahoo Finance and is **15-20 minutes delayed — NOT real-time**
- Always conduct your own due diligence before making any investment decisions
- Consult a qualified financial professional before acting on any information here
- Past performance of any stock is not indicative of future results

POV Research Platform is **not** a registered investment advisor.
"""


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_price(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"${v:,.2f}"


def _fmt_large(v: int | float | None, prefix: str = "$") -> str:
    if v is None:
        return "N/A"
    v = float(v)
    if abs(v) >= 1e12:
        return f"{prefix}{v / 1e12:.2f}T"
    if abs(v) >= 1e9:
        return f"{prefix}{v / 1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"{prefix}{v / 1e6:.1f}M"
    return f"{prefix}{v:,.0f}"


def _fmt_pct(v: float | None) -> str:
    """Format a decimal ratio as a percentage (0.2704 → '27.04%')."""
    if v is None:
        return "N/A"
    return f"{float(v) * 100:.2f}%"


def _fmt_volume(v: int | None) -> str:
    if v is None:
        return "N/A"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return str(v)


def _change_delta(card: StockCard) -> str:
    if card.change_usd is None or card.change_pct is None:
        return ""
    sign = "+" if card.change_usd >= 0 else ""
    return f"{sign}${card.change_usd:,.2f}  ({sign}{card.change_pct:.2f}%)"


def _exchange_display(code: str) -> str:
    return {"NYQ": "NYSE", "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ"}.get(code, code)


# ── Data layer ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=900, show_spinner=False)
def _fetch(ticker: str) -> PipelineResult:
    """Pull from yfinance + store in DuckDB. Cached 15 min per ticker."""
    return run_full_pipeline(ticker)


def _get_fundamentals(ticker: str) -> dict | None:
    """Read latest fundamentals snapshot from DuckDB (stored by pipeline)."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT
                pe_ratio_ttm, eps_ttm,
                profit_margin, roe, debt_to_equity, free_cash_flow,
                analyst_recommendation, number_of_analysts,
                analyst_target_mean
            FROM stock_fundamentals
            WHERE ticker = ?
            ORDER BY snapshot_date DESC
            LIMIT 1
        """, [ticker]).fetchone()
        if not row:
            return None
        return {
            "pe_ratio":            row[0],
            "eps":                 row[1],
            "profit_margin":       row[2],
            "roe":                 row[3],
            "debt_to_equity":      row[4],
            "free_cash_flow":      row[5],
            "analyst_rec":         (row[6] or "").upper(),
            "analyst_count":       row[7],
            "analyst_target_mean": row[8],
        }
    except Exception as exc:
        logger.warning("Fundamentals DB read failed for %s: %s", ticker, exc)
        return None
    finally:
        conn.close()


# ── UI components ─────────────────────────────────────────────────────────────

def render_header() -> None:
    st.markdown("""
    <div class="pov-header">
        <div class="pov-title">📊 POV Research Platform</div>
        <div class="pov-tagline">AI-Powered Stock Analysis · Pranav Ongole's Vision</div>
    </div>
    """, unsafe_allow_html=True)


def render_disclaimer() -> None:
    st.markdown("""
    <div class="disclaimer-bar">
        ⚠️ <strong>Not financial advice.</strong>
        AI-generated analysis for educational purposes only.
        Data delayed 15-20 min from Yahoo Finance.
    </div>
    """, unsafe_allow_html=True)
    with st.expander("Full SEC Disclaimer & Legal Notice"):
        st.markdown(_DISCLAIMER_FULL)


def render_stock_card(card: StockCard, fund: dict | None) -> None:
    """Render the full data display for a valid ticker."""

    # ── Identity row ──────────────────────────────────────────────────────────
    st.markdown(
        f'<div class="ticker-name">{card.ticker}</div>'
        f'<div class="company-name">{card.company_name}</div>'
        f'<div>'
        f'  <span class="pill pill-exchange">{_exchange_display(card.exchange)}</span>'
        f'  <span class="pill pill-sector">{card.sector}</span>'
        f'  <span class="pill pill-sector">{card.industry}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Price row (st.metric handles +/- coloring automatically) ─────────────
    p1, p2, p3, p4 = st.columns(4)
    with p1:
        st.metric(
            label="Current Price",
            value=_fmt_price(card.current_price),
            delta=_change_delta(card) or None,
        )
    with p2:
        st.metric(label="Volume", value=_fmt_volume(card.volume))
    with p3:
        st.metric(label="Market Cap", value=_fmt_large(card.market_cap))
    with p4:
        if fund and fund.get("analyst_rec"):
            rec = fund["analyst_rec"]
            count = fund.get("analyst_count") or ""
            target = _fmt_price(float(fund["analyst_target_mean"])) if fund.get("analyst_target_mean") else "N/A"
            st.metric(
                label=f"Analyst Consensus ({count} analysts)",
                value=rec,
                delta=f"Target: {target}",
            )
        else:
            st.metric(label="Analyst Consensus", value="N/A")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Market data row ───────────────────────────────────────────────────────
    st.markdown('<div class="section-label">Market Data</div>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)

    hi = _fmt_price(card.fifty_two_wk_high)
    lo = _fmt_price(card.fifty_two_wk_low)

    _card(c1, "52-Week High", hi)
    _card(c2, "52-Week Low",  lo)

    if fund:
        pe  = f"{float(fund['pe_ratio']):.2f}x" if fund.get("pe_ratio") else "N/A"
        eps = f"${float(fund['eps']):.2f}"       if fund.get("eps")      else "N/A"
        _card(c3, "P/E Ratio (TTM)", pe)
        _card(c4, "EPS (TTM)",       eps)
    else:
        _card(c3, "P/E Ratio (TTM)", "N/A")
        _card(c4, "EPS (TTM)",       "N/A")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Fundamentals preview row ───────────────────────────────────────────────
    if fund:
        st.markdown('<div class="section-label">Fundamentals Preview</div>', unsafe_allow_html=True)
        f1, f2, f3, f4 = st.columns(4)

        pm  = _fmt_pct(fund.get("profit_margin"))
        roe = _fmt_pct(fund.get("roe"))
        de  = f"{float(fund['debt_to_equity']):.1f}x" if fund.get("debt_to_equity") else "N/A"
        fcf = _fmt_large(fund.get("free_cash_flow"))

        _card(f1, "Profit Margin",    pm,  "Net income / Revenue")
        _card(f2, "Return on Equity", roe, "Net income / Equity")
        _card(f3, "Debt / Equity",    de,  "Lower is healthier")
        _card(f4, "Free Cash Flow",   fcf, "Operating CF − CapEx")

        st.markdown("<br>", unsafe_allow_html=True)

    # ── Timestamp ─────────────────────────────────────────────────────────────
    fetched_est = card.fetched_at.astimezone(EST)
    ts = fetched_est.strftime("%-I:%M %p EST, %b %-d %Y")
    st.markdown(
        f'<div class="ts-row">'
        f'Data as of {ts} &nbsp;'
        f'<span class="delay-tag">15-20 min delay · Source: Yahoo Finance</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Phase 4 teaser ────────────────────────────────────────────────────────
    st.markdown("""
    <div class="phase4-box">
        <strong>🤖 AI Research Report</strong> — <em>Phase 4 · Coming Soon</em><br>
        <span style="font-size:0.83rem;color:#8b949e;">
        Value Conviction Score (0–100) · BUY / HOLD / SELL / AVOID / WAIT signal ·
        Fundamental deep-dive · Technical analysis · Market context · QA-validated output
        </span>
    </div>
    """, unsafe_allow_html=True)


def _card(col, label: str, value: str, sub: str = "") -> None:
    """Render a data card in a column."""
    sub_html = f'<div class="card-sub">{sub}</div>' if sub else ""
    col.markdown(
        f'<div class="data-card">'
        f'  <div class="card-label">{label}</div>'
        f'  <div class="card-value">{value}</div>'
        f'  {sub_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_error(result: PipelineResult) -> None:
    icon_map = {
        "format":     "✏️",
        "not_found":  "🔍",
        "exchange":   "🏛️",
        "market_cap": "📉",
        "history":    "📅",
        "data_error": "⚡",
        "db_error":   "🗄️",
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
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        '<p style="font-size:1.8rem;font-weight:700;color:#e6edf3;letter-spacing:-0.5px;">'
        "Institutional-grade research.<br>One ticker at a time."
        "</p>"
        '<p style="font-size:0.95rem;color:#8b949e;max-width:520px;">'
        "Enter a NYSE or NASDAQ ticker above. We pull real market data, "
        "run it through nine AI research agents, and return a full conviction report in seconds."
        "</p>",
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**📊 Value Conviction Score**")
        st.caption("0–100 composite score from fundamentals, technicals, and market sentiment.")
        st.markdown("**📰 Finance Research**")
        st.caption("SEC filings, earnings highlights, analyst estimates — surfaced automatically.")
    with c2:
        st.markdown("**📈 Technical Analysis**")
        st.caption("RSI · MACD · Bollinger Bands · support/resistance — interactive charts.")
        st.markdown("**🏢 Competitor Comparison**")
        st.caption("Automated peer-set with side-by-side KPI table.")
    with c3:
        st.markdown("**💼 Fundamental Deep Dive**")
        st.caption("Revenue trends, margins, FCF, balance sheet health, ratio benchmarking.")
        st.markdown("**✅ QA-Validated Output**")
        st.caption("Every report passes a dedicated QA agent before reaching you.")

    st.markdown("---")
    st.caption(
        "**Universe:** NYSE and NASDAQ equities · Market cap ≥ $500M · "
        "2+ years price history · No ETFs, ADRs, or SPACs."
    )


def render_footer() -> None:
    st.markdown(
        '<div class="footer-bar">'
        "POV Research Platform &nbsp;·&nbsp; Built by Pranav Ongole &nbsp;·&nbsp; "
        "Not financial advice &nbsp;·&nbsp; Data delayed 15-20 min"
        "</div>",
        unsafe_allow_html=True,
    )


# ── Session state ─────────────────────────────────────────────────────────────

def _init_state() -> None:
    for k, v in {"result": None, "last_ticker": ""}.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    render_header()
    render_disclaimer()

    # ── Search form ───────────────────────────────────────────────────────────
    with st.form("search_form", clear_on_submit=False):
        inp_col, btn_col = st.columns([5, 1])
        with inp_col:
            raw = st.text_input(
                label="ticker",
                label_visibility="collapsed",
                placeholder="Enter a ticker — AAPL, MSFT, NVDA, JPM...",
                value=st.session_state.last_ticker,
            )
        with btn_col:
            submitted = st.form_submit_button("Analyze →", use_container_width=True, type="primary")

    # ── Trigger pipeline ──────────────────────────────────────────────────────
    if submitted and raw.strip():
        ticker = raw.strip().upper()
        st.session_state.last_ticker = ticker
        with st.spinner(f"Pulling market data for **{ticker}**…"):
            st.session_state.result = _fetch(ticker)

    # ── Render result ─────────────────────────────────────────────────────────
    result: PipelineResult | None = st.session_state.result

    if result is not None:
        if result.success and result.stock_card:
            fund = _get_fundamentals(result.ticker)
            render_stock_card(result.stock_card, fund)
        else:
            render_error(result)
    else:
        render_landing()

    render_footer()


if __name__ == "__main__":
    main()
