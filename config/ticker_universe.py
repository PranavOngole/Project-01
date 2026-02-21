"""
config/ticker_universe.py
Ticker validation against the Project-01 stock universe criteria.

Locked rules (BRD v1.0, Decision #1):
  - NYSE and NASDAQ listed equities only
  - Market cap >= $500M (mid-cap and above)
  - Minimum 2 years of continuous price history in yfinance
  - Quote type must be EQUITY (no ETFs, funds, ADRs, SPACs, preferreds)

Data comes from yfinance, which provides 15-20 min delayed quotes.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import yfinance as yf

logger = logging.getLogger(__name__)

# ── Universe criteria ─────────────────────────────────────────────────────────

MARKET_CAP_MINIMUM: int = 500_000_000  # $500M

HISTORY_YEARS_MINIMUM: int = 2

# Exchange codes returned by yfinance for NYSE and NASDAQ
VALID_EXCHANGES: frozenset[str] = frozenset(
    {
        "NYQ",    # New York Stock Exchange
        "NYSE",   # NYSE (alternate label)
        "NMS",    # NASDAQ Global Select Market
        "NGM",    # NASDAQ Global Market
        "NCM",    # NASDAQ Capital Market
        "NASDAQ", # NASDAQ (alternate label)
    }
)

# Excluded quote types — only pure equities are in scope
EXCLUDED_QUOTE_TYPES: frozenset[str] = frozenset(
    {"ETF", "MUTUALFUND", "INDEX", "FUTURE", "OPTION", "CRYPTOCURRENCY", "CURRENCY"}
)


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    ticker: str
    is_valid: bool
    reason: str
    company_name: str | None = None
    exchange: str | None = None
    market_cap: float | None = None
    earliest_date: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def market_cap_fmt(self) -> str:
        """Human-readable market cap string."""
        if self.market_cap is None:
            return "N/A"
        if self.market_cap >= 1e12:
            return f"${self.market_cap / 1e12:.1f}T"
        if self.market_cap >= 1e9:
            return f"${self.market_cap / 1e9:.1f}B"
        return f"${self.market_cap / 1e6:.0f}M"


# ── Validation logic ──────────────────────────────────────────────────────────

def validate_ticker(ticker: str) -> ValidationResult:
    """
    Validate a ticker symbol against the Project-01 universe rules.

    Performs three sequential checks:
      1. Ticker format (1-5 uppercase alpha characters)
      2. Exchange + quote type (NYSE/NASDAQ equities only)
      3. Market cap >= $500M and 2+ years of price history

    Returns a ValidationResult. Check .is_valid before proceeding.
    """
    ticker = ticker.strip().upper()

    # ── Format check ──────────────────────────────────────────────────────────
    if not ticker:
        return ValidationResult(
            ticker=ticker,
            is_valid=False,
            reason="Ticker cannot be empty.",
        )

    if not ticker.isalpha() or len(ticker) > 5:
        return ValidationResult(
            ticker=ticker,
            is_valid=False,
            reason=(
                f"'{ticker}' is not a valid ticker format. "
                "Use 1–5 uppercase letters (e.g. AAPL, MSFT)."
            ),
        )

    # ── Fetch info from yfinance ───────────────────────────────────────────────
    try:
        info = yf.Ticker(ticker).info
    except Exception as exc:
        logger.warning("yfinance info fetch failed for %s: %s", ticker, exc)
        return ValidationResult(
            ticker=ticker,
            is_valid=False,
            reason=f"Could not retrieve data for '{ticker}'. The ticker may not exist.",
        )

    # yfinance returns an almost-empty dict for unknown tickers
    if not info or info.get("trailingPegRatio") is None and info.get("marketCap") is None:
        # Check for a more reliable sentinel
        if info.get("symbol") is None and info.get("quoteType") is None:
            return ValidationResult(
                ticker=ticker,
                is_valid=False,
                reason=f"'{ticker}' was not found. Verify the ticker symbol.",
            )

    # ── Quote type check ──────────────────────────────────────────────────────
    quote_type = (info.get("quoteType") or "").upper()
    if quote_type in EXCLUDED_QUOTE_TYPES:
        return ValidationResult(
            ticker=ticker,
            is_valid=False,
            reason=(
                f"'{ticker}' is a {quote_type}. "
                "Project-01 covers NYSE/NASDAQ-listed common equities only."
            ),
        )

    if quote_type and quote_type != "EQUITY":
        return ValidationResult(
            ticker=ticker,
            is_valid=False,
            reason=(
                f"'{ticker}' has quote type '{quote_type}'. "
                "Only EQUITY instruments are supported."
            ),
        )

    # ── Exchange check ────────────────────────────────────────────────────────
    exchange = info.get("exchange", "")
    if exchange not in VALID_EXCHANGES:
        return ValidationResult(
            ticker=ticker,
            is_valid=False,
            reason=(
                f"'{ticker}' trades on '{exchange or 'an unsupported exchange'}'. "
                "Only NYSE and NASDAQ listings are in scope."
            ),
        )

    # ── Market cap check ──────────────────────────────────────────────────────
    market_cap = info.get("marketCap")
    if not market_cap or market_cap < MARKET_CAP_MINIMUM:
        cap_str = f"${market_cap / 1e6:.0f}M" if market_cap else "unknown"
        return ValidationResult(
            ticker=ticker,
            is_valid=False,
            reason=(
                f"'{ticker}' market cap is {cap_str}. "
                f"Minimum required: $500M."
            ),
            exchange=exchange,
            market_cap=market_cap,
        )

    # ── Price history check ───────────────────────────────────────────────────
    cutoff_date = datetime.now(tz=timezone.utc) - timedelta(days=HISTORY_YEARS_MINIMUM * 365)
    try:
        hist = yf.Ticker(ticker).history(period="2y")
        if hist.empty:
            return ValidationResult(
                ticker=ticker,
                is_valid=False,
                reason=f"No price history found for '{ticker}'.",
                exchange=exchange,
                market_cap=market_cap,
            )

        earliest = hist.index[0]
        # Make timezone-aware for comparison
        if earliest.tzinfo is None:
            earliest = earliest.replace(tzinfo=timezone.utc)

        if earliest > cutoff_date:
            return ValidationResult(
                ticker=ticker,
                is_valid=False,
                reason=(
                    f"'{ticker}' has less than 2 years of price history. "
                    f"Earliest available: {earliest.date()}."
                ),
                exchange=exchange,
                market_cap=market_cap,
                earliest_date=str(earliest.date()),
            )

    except Exception as exc:
        logger.warning("yfinance history fetch failed for %s: %s", ticker, exc)
        return ValidationResult(
            ticker=ticker,
            is_valid=False,
            reason=f"Could not verify price history for '{ticker}'.",
            exchange=exchange,
            market_cap=market_cap,
        )

    # ── All checks passed ─────────────────────────────────────────────────────
    return ValidationResult(
        ticker=ticker,
        is_valid=True,
        reason="All universe criteria met.",
        company_name=info.get("longName") or info.get("shortName") or ticker,
        exchange=exchange,
        market_cap=market_cap,
        earliest_date=str(hist.index[0].date()),
    )


def is_valid_ticker(ticker: str) -> bool:
    """Convenience wrapper — returns True/False only."""
    return validate_ticker(ticker).is_valid
