"""
app/main.py
Project-01 — AI-Powered Stock Research Platform
Streamlit entry point.

Run locally:
    streamlit run app/main.py

Architecture:
    User enters ticker → QA validates → Agent pipeline runs → Report displayed
    All agent system prompts are loaded from PROMPT_DIR (not stored here).

Data delay notice:
    Market data is sourced from yfinance and is 15-20 minutes delayed.
    This platform NEVER claims to provide real-time data.
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on the path regardless of working directory
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

# ── Page config — must be the very first Streamlit call ───────────────────────
st.set_page_config(
    page_title="Project-01 | AI Stock Research",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={
        "Get Help": "https://github.com/PranavOngole/Project-01",
        "Report a bug": "https://github.com/PranavOngole/Project-01/issues",
        "About": "Project-01 — AI-Powered Stock Research Platform",
    },
)


# ── Custom CSS ────────────────────────────────────────────────────────────────
_CSS = """
<style>
/* ── Global ── */
html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

/* ── Hide default Streamlit chrome ── */
#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }
header    { visibility: hidden; }

/* ── Hero title ── */
.hero-title {
    font-size: 2.6rem;
    font-weight: 700;
    letter-spacing: -0.5px;
    color: #e6edf3;
    margin-bottom: 0.2rem;
    line-height: 1.1;
}
.hero-subtitle {
    font-size: 1.05rem;
    color: #8b949e;
    margin-bottom: 2.4rem;
}

/* ── Conviction score badge ── */
.score-badge {
    display: inline-block;
    padding: 0.5rem 1.2rem;
    border-radius: 8px;
    font-size: 2.4rem;
    font-weight: 700;
    background: #1f2937;
    border: 2px solid #374151;
    color: #e6edf3;
}

/* ── Section headers ── */
.section-label {
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: #6e7681;
    margin-bottom: 0.5rem;
}

/* ── Disclaimer banner ── */
.disclaimer-box {
    background: #161b22;
    border: 1px solid #30363d;
    border-left: 4px solid #e3a520;
    border-radius: 6px;
    padding: 0.85rem 1.2rem;
    font-size: 0.83rem;
    color: #8b949e;
    line-height: 1.6;
    margin: 1rem 0;
}

/* ── Footer ── */
.footer-bar {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: #0d1117;
    border-top: 1px solid #21262d;
    padding: 0.55rem 2rem;
    font-size: 0.75rem;
    color: #6e7681;
    text-align: center;
    z-index: 999;
}
.footer-bar a {
    color: #4f8ef7;
    text-decoration: none;
}

/* ── Data delay badge ── */
.delay-badge {
    display: inline-block;
    background: #1c2128;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 0.15rem 0.55rem;
    font-size: 0.72rem;
    color: #e3a520;
    font-weight: 500;
}

/* ── Placeholder card ── */
.placeholder-card {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 8px;
    padding: 1.5rem;
    text-align: center;
    color: #8b949e;
    font-size: 0.9rem;
}

/* ── Bottom padding so footer doesn't overlap content ── */
.main .block-container {
    padding-bottom: 4rem;
}
</style>
"""

st.markdown(_CSS, unsafe_allow_html=True)


# ── SEC Disclaimer text ───────────────────────────────────────────────────────

_DISCLAIMER_FULL = """
**This platform is for informational and educational purposes only.**

Nothing produced by Project-01 — including the Value Conviction Score, purchase price recommendations,
or any analysis output — constitutes financial advice, investment advice, or a recommendation to buy,
sell, or hold any security.

**Key points:**
- AI-generated analysis may contain errors, hallucinations, or outdated information
- The Value Conviction Score and Purchase Price estimates are not guarantees of future performance
- Always conduct your own due diligence (DYOR) before making investment decisions
- Consult a qualified financial professional before acting on any information presented here
- Past performance of any stock referenced is not indicative of future results
- Project-01 is not a registered investment advisor

Market data is sourced from yfinance and is **15-20 minutes delayed. This is NOT real-time data.**
"""


# ── Session state initialisation ──────────────────────────────────────────────

def _init_session_state() -> None:
    defaults = {
        "ticker": "",
        "company_name": "",
        "is_analyzing": False,
        "analysis_ready": False,
        "last_updated": None,
        "error_msg": "",
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


# ── Reusable components ───────────────────────────────────────────────────────

def render_disclaimer_banner() -> None:
    """SEC disclaimer — visible on every page as required."""
    with st.expander("⚠️  SEC Disclaimer & Legal Notice — Click to Read", expanded=False):
        st.markdown(_DISCLAIMER_FULL)


def render_sec_disclaimer_inline() -> None:
    """Compact inline disclaimer for tight spaces."""
    st.markdown(
        '<div class="disclaimer-box">'
        "⚠️ <strong>Educational use only. Not financial advice.</strong> "
        "AI-generated analysis may contain errors. "
        "The Value Conviction Score and purchase price estimates are not guarantees. "
        "Always do your own research. "
        "Market data is 15-20 min delayed — not real-time."
        "</div>",
        unsafe_allow_html=True,
    )


def render_footer() -> None:
    """Persistent footer pinned to the bottom of every page."""
    st.markdown(
        '<div class="footer-bar">'
        "Not financial advice. AI-generated analysis for educational purposes only. "
        "Data delayed 15-20 min. &nbsp;|&nbsp; "
        '<a href="#sec-disclaimer">See full disclaimer</a>'
        "</div>",
        unsafe_allow_html=True,
    )


def render_last_updated(ts: datetime) -> None:
    """Timestamp display with mandatory delay notice."""
    formatted = ts.strftime("%b %d, %Y at %I:%M %p UTC")
    col1, col2 = st.columns([3, 1])
    with col1:
        st.caption(f"Last updated: {formatted}")
    with col2:
        st.markdown(
            '<span class="delay-badge">Data: 15-20 min delayed</span>',
            unsafe_allow_html=True,
        )


def render_loading_state(ticker: str) -> None:
    """Full-page loading placeholder while the agent pipeline runs."""
    st.markdown("---")
    with st.container():
        st.markdown(
            f'<p class="section-label">Running analysis pipeline for {ticker}</p>',
            unsafe_allow_html=True,
        )

        # Progress steps
        steps = [
            ("🔍", "QA Validator",         "Verifying ticker & data quality..."),
            ("📰", "Finance Researcher",   "Pulling SEC filings & news..."),
            ("📈", "Technical Analyst",    "Computing RSI, MACD, Bollinger bands..."),
            ("💼", "Fundamental Analyst",  "Calculating Value Conviction Score..."),
            ("📝", "Business Analyst",     "Structuring report..."),
            ("✅", "Manager Review",       "Final conflict resolution & QA pass..."),
        ]

        progress_bar = st.progress(0)
        status_text = st.empty()

        for i, (icon, name, detail) in enumerate(steps):
            progress_bar.progress((i + 1) / len(steps))
            status_text.markdown(
                f'<p class="section-label">{icon} {name} — {detail}</p>',
                unsafe_allow_html=True,
            )
            time.sleep(0.4)  # Visual pacing — replace with real await in production

        status_text.markdown(
            '<p class="section-label">✅ Analysis complete</p>',
            unsafe_allow_html=True,
        )
        time.sleep(0.3)

    st.session_state.is_analyzing = False
    st.session_state.analysis_ready = True
    st.session_state.last_updated = datetime.now(tz=timezone.utc)
    st.rerun()


# ── Analysis placeholder page ─────────────────────────────────────────────────

def render_analysis_placeholder(ticker: str, company_name: str) -> None:
    """
    Placeholder analysis layout — full structure, no real data yet.
    Replace placeholder values with real agent outputs in Phase 4.
    """
    st.markdown("---")

    # ── Header ────────────────────────────────────────────────────────────────
    col_left, col_right = st.columns([3, 1])
    with col_left:
        exchange_badge = "NYSE"  # Placeholder — will come from ticker_universe validation
        st.markdown(
            f'<p class="section-label">Analysis · {exchange_badge}</p>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<p class="hero-title">{ticker}</p>'
            f'<p class="hero-subtitle">{company_name}</p>',
            unsafe_allow_html=True,
        )
    with col_right:
        if st.session_state.last_updated:
            render_last_updated(st.session_state.last_updated)

    # ── Conviction score + quick metrics ──────────────────────────────────────
    st.markdown("#### Value Conviction Score")

    score_col, m1, m2, m3, m4 = st.columns([1.2, 1, 1, 1, 1])
    with score_col:
        st.markdown(
            '<div class="score-badge">— / 100</div>'
            '<p style="font-size:0.78rem;color:#6e7681;margin-top:0.4rem;">'
            "Agent pipeline required</p>",
            unsafe_allow_html=True,
        )
    with m1:
        st.metric("Current Price", "—", help="15-20 min delayed")
    with m2:
        st.metric("Fair Value Est.", "—", help="Fundamental Analyst output")
    with m3:
        st.metric("52-Week Range", "—")
    with m4:
        st.metric("Market Cap", "—")

    st.markdown("---")

    # ── Deep Dive toggle ──────────────────────────────────────────────────────
    col_btn, _ = st.columns([1, 3])
    with col_btn:
        show_deep_dive = st.button("🔬  Deep Dive Mode", use_container_width=True)

    # ── Four analysis sections ────────────────────────────────────────────────
    tab_technical, tab_fundamental, tab_finance, tab_competitors = st.tabs(
        ["📈  Technical", "💼  Fundamental", "📰  Finance / News", "🏢  Competitors"]
    )

    with tab_technical:
        render_sec_disclaimer_inline()
        st.markdown("##### Technical Analysis")
        st.markdown(
            '<div class="placeholder-card">'
            "📈 Price chart and indicator suite will render here.<br>"
            "Indicators: RSI · MACD · Bollinger Bands · Volume Profile · Support/Resistance"
            "</div>",
            unsafe_allow_html=True,
        )
        if show_deep_dive:
            st.info("Deep Dive mode activated — extended technical commentary will appear here.")

    with tab_fundamental:
        render_sec_disclaimer_inline()
        st.markdown("##### Fundamental Analysis")

        fcol1, fcol2, fcol3 = st.columns(3)
        with fcol1:
            st.markdown('<p class="section-label">Valuation</p>', unsafe_allow_html=True)
            st.metric("P/E Ratio",  "—")
            st.metric("P/B Ratio",  "—")
            st.metric("P/S Ratio",  "—")
        with fcol2:
            st.markdown('<p class="section-label">Profitability</p>', unsafe_allow_html=True)
            st.metric("Profit Margin",     "—")
            st.metric("Operating Margin",  "—")
            st.metric("ROE",               "—")
        with fcol3:
            st.markdown('<p class="section-label">Financial Health</p>', unsafe_allow_html=True)
            st.metric("Debt / Equity",   "—")
            st.metric("Current Ratio",   "—")
            st.metric("Revenue Growth",  "—")

        st.markdown("---")
        st.markdown(
            '<div class="placeholder-card">'
            "💼 Fundamental Analyst narrative and earnings quality assessment will appear here."
            "</div>",
            unsafe_allow_html=True,
        )

    with tab_finance:
        render_sec_disclaimer_inline()
        st.markdown("##### Finance Research & News")
        st.markdown(
            '<div class="placeholder-card">'
            "📰 SEC filings digest, earnings call highlights, and analyst estimates will appear here."
            "</div>",
            unsafe_allow_html=True,
        )

    with tab_competitors:
        st.markdown("##### Competitor Comparison")
        st.markdown(
            '<div class="placeholder-card">'
            "🏢 Peer-set KPI comparison table will appear here."
            "</div>",
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # ── Purchase price recommendation ─────────────────────────────────────────
    st.markdown("#### Purchase Price Recommendation")
    render_sec_disclaimer_inline()
    p1, p2, p3, _ = st.columns([1, 1, 1, 2])
    with p1:
        st.metric("Entry Price",  "—", help="Fundamental Analyst estimate")
    with p2:
        st.metric("Target Price", "—")
    with p3:
        st.metric("Stop-Loss",    "—")

    st.caption(
        "Purchase price estimates are AI-generated from public financial data. "
        "These are not recommendations to buy or sell. Always consult a financial professional."
    )


# ── Landing page ──────────────────────────────────────────────────────────────

def render_landing() -> None:
    """Homepage — shown when no ticker has been analyzed yet."""
    st.markdown("---")

    st.markdown(
        '<p class="hero-title">Research any stock in seconds.</p>'
        '<p class="hero-subtitle">'
        "Nine AI agents — one comprehensive report. "
        "Fundamentals · Technicals · SEC filings · Value Conviction Score."
        "</p>",
        unsafe_allow_html=True,
    )

    # Capability grid
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**📊 Value Conviction Score**")
        st.caption("0–100 composite score weighing fundamentals, technicals, sentiment, and competitive position.")
        st.markdown("**📈 Technical Analysis**")
        st.caption("RSI · MACD · Bollinger Bands · volume profile · support/resistance rendered with interactive Plotly charts.")
    with c2:
        st.markdown("**💼 Fundamental Deep Dive**")
        st.caption("Revenue trends, margins, FCF, balance sheet health, earnings quality, and ratio benchmarking.")
        st.markdown("**📰 SEC Filing Digest**")
        st.caption("Latest 10-K / 10-Q highlights surfaced by the Finance Researcher agent.")
    with c3:
        st.markdown("**🏢 Competitor Comparison**")
        st.caption("Automated peer-set selection and side-by-side KPI table.")
        st.markdown("**✅ QA-Validated Output**")
        st.caption("Every report passes a dedicated QA agent before reaching the UI.")

    st.markdown("---")
    st.caption(
        "**Stock universe:** NYSE and NASDAQ equities with market cap ≥ $500M "
        "and at least 2 years of price history. "
        "ADRs, ETFs, SPACs, and preferreds are excluded."
    )


# ── Main app ──────────────────────────────────────────────────────────────────

def main() -> None:
    _init_session_state()

    # ── Header ────────────────────────────────────────────────────────────────
    header_left, header_right = st.columns([3, 1])
    with header_left:
        st.markdown(
            '<p style="font-size:1.1rem;font-weight:700;color:#e6edf3;margin:0;">'
            "📊 Project-01 &nbsp;|&nbsp; "
            '<span style="font-weight:400;color:#8b949e;">AI Stock Research</span>'
            "</p>",
            unsafe_allow_html=True,
        )
    with header_right:
        from config import settings as _s
        env_label = _s.APP_ENV.upper()
        st.caption(f"ENV: {env_label}")

    # ── SEC Disclaimer (always visible) ───────────────────────────────────────
    render_disclaimer_banner()

    # ── Search bar ────────────────────────────────────────────────────────────
    search_col, btn_col = st.columns([4, 1])
    with search_col:
        ticker_input = st.text_input(
            label="Ticker",
            label_visibility="collapsed",
            placeholder="Enter a ticker — AAPL, MSFT, TSLA, NVDA...",
            value=st.session_state.ticker,
            key="ticker_input_field",
            help=(
                "NYSE and NASDAQ equities only. "
                "Market cap ≥ $500M. 2+ years of price history required."
            ),
        )
    with btn_col:
        analyze_clicked = st.button(
            "Analyze →",
            use_container_width=True,
            type="primary",
            disabled=st.session_state.is_analyzing,
        )

    # Trigger analysis on button click or Enter (input changed + non-empty)
    should_analyze = analyze_clicked and ticker_input.strip()

    if should_analyze:
        raw = ticker_input.strip().upper()

        # Basic format guard before sending to full validator
        if not raw.isalpha() or len(raw) > 5:
            st.error(f"'{raw}' doesn't look like a valid ticker. Use 1–5 letters (e.g. AAPL).")
        else:
            st.session_state.ticker = raw
            st.session_state.company_name = f"{raw} Inc."  # Placeholder — replace with real lookup
            st.session_state.is_analyzing = True
            st.session_state.analysis_ready = False
            st.session_state.error_msg = ""
            st.rerun()

    # ── Error display ──────────────────────────────────────────────────────────
    if st.session_state.error_msg:
        st.error(st.session_state.error_msg)

    # ── State machine ──────────────────────────────────────────────────────────
    if st.session_state.is_analyzing and st.session_state.ticker:
        render_loading_state(st.session_state.ticker)

    elif st.session_state.analysis_ready and st.session_state.ticker:
        render_analysis_placeholder(
            ticker=st.session_state.ticker,
            company_name=st.session_state.company_name,
        )

    else:
        render_landing()

    # ── Footer ────────────────────────────────────────────────────────────────
    render_footer()


if __name__ == "__main__":
    main()
