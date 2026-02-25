"""
data/pipeline.py
yfinance data pipeline — validation, OHLCV pull, fundamentals pull, DuckDB storage.

Entry point for Streamlit: call run_full_pipeline(ticker) and check the returned
PipelineResult. If .success is True, .stock_card has everything the UI needs.

Rules (BRD v1.0 locked):
  - NYSE and NASDAQ equities only
  - Market cap >= $500M
  - 2+ years of continuous price history in yfinance
  - Quote type must be EQUITY
  - yfinance is 15-20 min delayed — NEVER say "real-time"

Every pull is logged to api_usage with:
  agent_name='data_engineer', api_provider='yfinance', cost=$0
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

# Ensure project root is importable regardless of working directory
_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from config import settings
from data.schema import get_connection

logger = logging.getLogger(__name__)

# ── Universe rules ────────────────────────────────────────────────────────────

_VALID_EXCHANGES: frozenset[str] = frozenset({
    "NYQ",    # NYSE
    "NYSE",
    "NMS",    # NASDAQ Global Select Market
    "NGM",    # NASDAQ Global Market
    "NCM",    # NASDAQ Capital Market
    "NASDAQ",
})

_MARKET_CAP_MIN: int = 500_000_000      # $500M
_MIN_TRADING_DAYS: int = 480            # ~2 trading years (252 days/yr × 2 ≈ 504; 480 is conservative)

_EXCLUDED_QUOTE_TYPES: frozenset[str] = frozenset({
    "ETF", "MUTUALFUND", "INDEX", "FUTURE", "OPTION", "CRYPTOCURRENCY", "CURRENCY",
})


# ── Return types ──────────────────────────────────────────────────────────────

@dataclass
class StockCard:
    """All data needed to render the Streamlit stock display card."""
    ticker: str
    company_name: str
    exchange: str
    sector: str
    industry: str
    current_price: float | None
    change_usd: float | None
    change_pct: float | None          # as percent already (e.g., 0.67 means +0.67%)
    volume: int | None
    market_cap: int | None
    fifty_two_wk_high: float | None
    fifty_two_wk_low: float | None
    fetched_at: datetime              # UTC; convert to EST in UI

    @property
    def market_cap_fmt(self) -> str:
        if not self.market_cap:
            return "N/A"
        if self.market_cap >= 1e12:
            return f"${self.market_cap / 1e12:.2f}T"
        if self.market_cap >= 1e9:
            return f"${self.market_cap / 1e9:.2f}B"
        return f"${self.market_cap / 1e6:.0f}M"

    @property
    def change_sign(self) -> str:
        if self.change_usd is None:
            return ""
        return "▲" if self.change_usd >= 0 else "▼"


@dataclass
class PipelineResult:
    """
    Returned by run_full_pipeline().

    Check .success first. If False, .error has the user-facing message
    and .error_type describes the category of failure.
    """
    success: bool
    ticker: str
    error: str | None = None
    error_type: str | None = None   # 'format' | 'not_found' | 'exchange' | 'market_cap'
                                    # | 'history' | 'data_error' | 'db_error'
    stock_card: StockCard | None = None


# ── Private helpers ───────────────────────────────────────────────────────────

def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return None if (f != f) else f    # NaN check
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> int | None:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:    # NaN
            return None
        return int(f)
    except (TypeError, ValueError):
        return None


def _parse_ts_to_date(ts) -> date | None:
    """Convert Unix timestamp, datetime, or date → Python date."""
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)) and ts > 0:
            return datetime.fromtimestamp(ts, tz=timezone.utc).date()
        if isinstance(ts, datetime):
            return ts.date()
        if isinstance(ts, date):
            return ts
    except Exception:
        pass
    return None


def _market_cap_category(mc: int | None) -> str | None:
    if mc is None:
        return None
    if mc >= 200_000_000_000:
        return "mega"
    if mc >= 10_000_000_000:
        return "large"
    if mc >= 2_000_000_000:
        return "mid"
    return "small"


# ── DuckDB storage ────────────────────────────────────────────────────────────

def _store_ohlcv(ticker: str, hist: pd.DataFrame, pulled_at: datetime) -> int:
    """
    Upsert 2 years of OHLCV data into stock_prices.
    Returns number of rows processed.
    """
    if hist.empty:
        return 0

    df = hist.copy()

    # Normalize timezone-aware index
    df.index = pd.to_datetime(df.index).tz_localize(None)

    # Column name normalisation (yfinance uses title-case)
    col_map = {
        "Open": "open_price", "High": "high_price", "Low": "low_price",
        "Close": "close_price", "Volume": "volume",
    }
    df = df.rename(columns=col_map)

    adj_col = "Adj Close" if "Adj Close" in df.columns else "close_price"
    if adj_col == "Adj Close":
        df = df.rename(columns={"Adj Close": "adj_close"})
    else:
        df["adj_close"] = df["close_price"]

    # Derived: daily change from previous close
    df["daily_change_usd"] = (df["close_price"] - df["close_price"].shift(1)).round(4)
    df["daily_change_pct"] = (df["daily_change_usd"] / df["close_price"].shift(1)).round(6)

    # Rolling context window
    df["fifty_two_week_high"] = df["high_price"].rolling(252, min_periods=1).max().round(4)
    df["fifty_two_week_low"]  = df["low_price"].rolling(252, min_periods=1).min().round(4)
    df["avg_volume_10d"] = df["volume"].rolling(10, min_periods=1).mean().round(0)
    df["avg_volume_30d"] = df["volume"].rolling(30, min_periods=1).mean().round(0)

    today = date.today()
    now = datetime.now(timezone.utc)

    insert_df = pd.DataFrame({
        "ticker":              ticker,
        "trade_date":          df.index.date,
        "open_price":          df["open_price"].round(4),
        "high_price":          df["high_price"].round(4),
        "low_price":           df["low_price"].round(4),
        "close_price":         df["close_price"].round(4),
        "adj_close":           df["adj_close"].round(4),
        "volume":              pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64"),
        "daily_change_usd":    df["daily_change_usd"],
        "daily_change_pct":    df["daily_change_pct"],
        "fifty_two_week_high": df["fifty_two_week_high"],
        "fifty_two_week_low":  df["fifty_two_week_low"],
        "avg_volume_10d":      pd.to_numeric(df["avg_volume_10d"], errors="coerce").fillna(0).astype("int64"),
        "avg_volume_30d":      pd.to_numeric(df["avg_volume_30d"], errors="coerce").fillna(0).astype("int64"),
        "data_source":         "yfinance",
        "data_delay_minutes":  20,
        "pulled_at":           pulled_at,
        "is_trading_day":      True,
        "created_date":        today,
        "created_at":          now,
        "updated_at":          now,
    })

    conn = get_connection()
    try:
        conn.register("_tmp_ohlcv", insert_df)
        conn.execute("""
            INSERT INTO stock_prices (
                ticker, trade_date, open_price, high_price, low_price,
                close_price, adj_close, volume,
                daily_change_usd, daily_change_pct,
                fifty_two_week_high, fifty_two_week_low,
                avg_volume_10d, avg_volume_30d,
                data_source, data_delay_minutes, pulled_at, is_trading_day,
                created_date, created_at, updated_at
            )
            SELECT
                ticker, trade_date, open_price, high_price, low_price,
                close_price, adj_close, volume,
                daily_change_usd, daily_change_pct,
                fifty_two_week_high, fifty_two_week_low,
                avg_volume_10d, avg_volume_30d,
                data_source, data_delay_minutes, pulled_at, is_trading_day,
                created_date, created_at, updated_at
            FROM _tmp_ohlcv
            ON CONFLICT (ticker, trade_date) DO UPDATE SET
                open_price          = excluded.open_price,
                high_price          = excluded.high_price,
                low_price           = excluded.low_price,
                close_price         = excluded.close_price,
                adj_close           = excluded.adj_close,
                volume              = excluded.volume,
                daily_change_usd    = excluded.daily_change_usd,
                daily_change_pct    = excluded.daily_change_pct,
                fifty_two_week_high = excluded.fifty_two_week_high,
                fifty_two_week_low  = excluded.fifty_two_week_low,
                avg_volume_10d      = excluded.avg_volume_10d,
                avg_volume_30d      = excluded.avg_volume_30d,
                pulled_at           = excluded.pulled_at,
                updated_at          = excluded.updated_at
        """)
        conn.commit()
        return len(insert_df)
    finally:
        conn.close()


def _store_fundamentals(ticker: str, info: dict, pulled_at: datetime) -> None:
    """Upsert one fundamentals snapshot into stock_fundamentals."""
    today = date.today()
    now = datetime.now(timezone.utc)

    mc = _safe_int(info.get("marketCap"))
    total_cash = _safe_int(info.get("totalCash"))
    total_debt = _safe_int(info.get("totalDebt"))
    net_cash = (total_cash - total_debt) if (total_cash is not None and total_debt is not None) else None

    row: dict = {
        "ticker":               ticker,
        "snapshot_date":        today,
        # Company profile
        "company_name":         info.get("longName") or info.get("shortName") or ticker,
        "exchange":             info.get("exchange"),
        "sector":               info.get("sector"),
        "industry":             info.get("industry"),
        "market_cap":           mc,
        "market_cap_category":  _market_cap_category(mc),
        "employees":            _safe_int(info.get("fullTimeEmployees")),
        "country":              info.get("country"),
        "website":              info.get("website"),
        "business_summary":     info.get("longBusinessSummary"),
        # Valuation
        "pe_ratio_ttm":         _safe_float(info.get("trailingPE")),
        "pe_ratio_forward":     _safe_float(info.get("forwardPE")),
        "pb_ratio":             _safe_float(info.get("priceToBook")),
        "ps_ratio":             _safe_float(info.get("priceToSalesTrailing12Months")),
        "peg_ratio":            _safe_float(info.get("pegRatio")),
        "ev_to_ebitda":         _safe_float(info.get("enterpriseToEbitda")),
        "ev_to_revenue":        _safe_float(info.get("enterpriseToRevenue")),
        "price_to_fcf":         None,
        "enterprise_value":     _safe_int(info.get("enterpriseValue")),
        # Profitability (yfinance returns as 0-1 decimals)
        "gross_margin":         _safe_float(info.get("grossMargins")),
        "operating_margin":     _safe_float(info.get("operatingMargins")),
        "profit_margin":        _safe_float(info.get("profitMargins")),
        "roe":                  _safe_float(info.get("returnOnEquity")),
        "roa":                  _safe_float(info.get("returnOnAssets")),
        "roic":                 None,
        # Growth
        "revenue_growth_yoy":   _safe_float(info.get("revenueGrowth")),
        "earnings_growth_yoy":  _safe_float(info.get("earningsGrowth")),
        "revenue_growth_qoq":   None,
        "earnings_growth_qoq":  None,
        # Income statement
        "total_revenue":        _safe_int(info.get("totalRevenue")),
        "gross_profit":         _safe_int(info.get("grossProfits")),
        "operating_income":     None,
        "net_income":           _safe_int(info.get("netIncomeToCommon")),
        "ebitda":               _safe_int(info.get("ebitda")),
        "eps_ttm":              _safe_float(info.get("trailingEps")),
        "eps_forward":          _safe_float(info.get("forwardEps")),
        # Balance sheet
        "total_cash":           total_cash,
        "total_debt":           total_debt,
        "net_cash":             net_cash,
        "debt_to_equity":       _safe_float(info.get("debtToEquity")),
        "current_ratio":        _safe_float(info.get("currentRatio")),
        "quick_ratio":          _safe_float(info.get("quickRatio")),
        "book_value_per_share": _safe_float(info.get("bookValue")),
        # Cash flow
        "operating_cash_flow":  _safe_int(info.get("operatingCashflow")),
        "free_cash_flow":       _safe_int(info.get("freeCashflow")),
        "fcf_per_share":        None,
        "capex":                _safe_int(info.get("capitalExpenditures")),
        # Dividends
        "dividend_yield":       _safe_float(info.get("dividendYield")),
        "dividend_rate":        _safe_float(info.get("dividendRate")),
        "payout_ratio":         _safe_float(info.get("payoutRatio")),
        "ex_dividend_date":     _parse_ts_to_date(info.get("exDividendDate")),
        # Analyst consensus
        "analyst_target_mean":  _safe_float(info.get("targetMeanPrice")),
        "analyst_target_high":  _safe_float(info.get("targetHighPrice")),
        "analyst_target_low":   _safe_float(info.get("targetLowPrice")),
        "analyst_recommendation": info.get("recommendationKey"),
        "number_of_analysts":   _safe_int(info.get("numberOfAnalystOpinions")),
        # Metadata
        "data_source":          "yfinance",
        "pulled_at":            pulled_at,
        "fiscal_year_end":      str(_parse_ts_to_date(info.get("lastFiscalYearEnd"))) if info.get("lastFiscalYearEnd") else None,
        "most_recent_quarter":  _parse_ts_to_date(info.get("mostRecentQuarter")),
        # Audit columns
        "created_date":         today,
        "created_at":           now,
        "updated_at":           now,
    }

    cols = list(row.keys())
    vals = list(row.values())
    placeholders = ", ".join(["?" for _ in cols])
    col_list = ", ".join(cols)
    update_clause = ", ".join([
        f"{c} = excluded.{c}"
        for c in cols if c not in ("ticker", "snapshot_date")
    ])

    sql = f"""
        INSERT INTO stock_fundamentals ({col_list})
        VALUES ({placeholders})
        ON CONFLICT (ticker, snapshot_date) DO UPDATE SET {update_clause}
    """

    conn = get_connection()
    try:
        conn.execute(sql, vals)
        conn.commit()
    finally:
        conn.close()


def _log_data_pull(
    ticker: str,
    triggered_by: str,
    started_at: datetime,
    completed_at: datetime,
    latency_ms: int,
    rows_stored: int,
    is_error: bool = False,
    error_message: str | None = None,
) -> None:
    """Log a yfinance data pull to api_usage (cost = $0)."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO api_usage (
                ticker, triggered_by,
                agent_name, agent_id, agent_role,
                api_provider, api_endpoint, model, model_tier,
                request_started_at, request_completed_at, latency_ms,
                is_error, error_message,
                environment,
                created_date, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ticker, triggered_by,
                "data_engineer", "DE-01", "data_pull",
                "yfinance", "yfinance.history", "yfinance", "free",
                started_at, completed_at, latency_ms,
                is_error, error_message,
                settings.APP_ENV,
                date.today(), datetime.now(timezone.utc),
            ],
        )
        conn.commit()
        logger.debug(
            "Logged data pull: ticker=%s rows=%d latency=%dms error=%s",
            ticker, rows_stored, latency_ms, is_error,
        )
    except Exception as exc:
        logger.error("Failed to log data pull to api_usage: %s", exc)
    finally:
        conn.close()


# ── Main pipeline entry point ─────────────────────────────────────────────────

def run_full_pipeline(ticker: str, triggered_by: str = "user_analysis") -> PipelineResult:
    """
    Validate ticker, pull data from yfinance, store in DuckDB, return StockCard.

    This is the single function Streamlit calls. All validation, storage, and
    logging happens here. No LLM calls — data layer only.

    Args:
        ticker:       Raw ticker string from user input (will be cleaned).
        triggered_by: 'user_analysis' | 'scheduled_refresh'

    Returns:
        PipelineResult with .success=True and .stock_card populated on success,
        or .success=False and .error set to a user-facing message.
    """
    ticker = ticker.strip().upper()

    # ── 1. Format check ───────────────────────────────────────────────────────
    if not ticker or not ticker.isalpha() or len(ticker) > 5:
        return PipelineResult(
            success=False, ticker=ticker,
            error=f"'{ticker}' isn't a valid ticker. Use 1–5 letters (e.g. AAPL, MSFT).",
            error_type="format",
        )

    # ── 2. Fetch yfinance info ────────────────────────────────────────────────
    started_at = datetime.now(timezone.utc)
    t0 = time.monotonic()

    try:
        yf_ticker = yf.Ticker(ticker)
        info = yf_ticker.info
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("yfinance info fetch failed for %s: %s", ticker, exc)
        _log_data_pull(ticker, triggered_by, started_at,
                       datetime.now(timezone.utc), latency_ms, 0,
                       is_error=True, error_message=str(exc))
        return PipelineResult(
            success=False, ticker=ticker,
            error=f"Couldn't retrieve data for '{ticker}'. Check your connection and try again.",
            error_type="data_error",
        )

    # Detect empty / unknown ticker (yfinance returns sparse dict for unknowns)
    if not info or (info.get("quoteType") is None and info.get("regularMarketPrice") is None):
        return PipelineResult(
            success=False, ticker=ticker,
            error=f"'{ticker}' wasn't found. Double-check the ticker symbol.",
            error_type="not_found",
        )

    # ── 3. Quote type check ───────────────────────────────────────────────────
    quote_type = (info.get("quoteType") or "").upper()
    if quote_type in _EXCLUDED_QUOTE_TYPES:
        return PipelineResult(
            success=False, ticker=ticker,
            error=(
                f"'{ticker}' is a {quote_type.title()}, not a stock. "
                "Project-01 covers NYSE and NASDAQ common equities only."
            ),
            error_type="exchange",
        )

    # ── 4. Exchange check ─────────────────────────────────────────────────────
    exchange = info.get("exchange", "")
    if exchange not in _VALID_EXCHANGES:
        exch_label = exchange or "an unsupported exchange"
        return PipelineResult(
            success=False, ticker=ticker,
            error=(
                f"'{ticker}' trades on {exch_label}. "
                "Project-01 covers NYSE and NASDAQ stocks only."
            ),
            error_type="exchange",
        )

    # ── 5. Market cap check ───────────────────────────────────────────────────
    market_cap = _safe_int(info.get("marketCap"))
    if not market_cap or market_cap < _MARKET_CAP_MIN:
        cap_str = f"${market_cap / 1e6:.0f}M" if market_cap else "unknown"
        return PipelineResult(
            success=False, ticker=ticker,
            error=(
                f"'{ticker}' market cap is {cap_str}. "
                "Minimum required: $500M (mid-cap and above)."
            ),
            error_type="market_cap",
        )

    # ── 6. Pull price history ─────────────────────────────────────────────────
    try:
        hist = yf_ticker.history(period="2y", auto_adjust=False)
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        _log_data_pull(ticker, triggered_by, started_at,
                       datetime.now(timezone.utc), latency_ms, 0,
                       is_error=True, error_message=str(exc))
        return PipelineResult(
            success=False, ticker=ticker,
            error=f"Couldn't fetch price history for '{ticker}'. Try again in a moment.",
            error_type="data_error",
        )

    # ── 7. History length check ───────────────────────────────────────────────
    if len(hist) < _MIN_TRADING_DAYS:
        years = round(len(hist) / 252, 1)
        return PipelineResult(
            success=False, ticker=ticker,
            error=(
                f"'{ticker}' only has ~{years} year(s) of price history. "
                "Minimum required: 2 years. Try a more established company."
            ),
            error_type="history",
        )

    # ── 8. Store in DuckDB ────────────────────────────────────────────────────
    fetched_at = datetime.now(timezone.utc)
    rows_stored = 0

    try:
        rows_stored = _store_ohlcv(ticker, hist, fetched_at)
        _store_fundamentals(ticker, info, fetched_at)
        logger.info("Stored %d OHLCV rows + fundamentals for %s", rows_stored, ticker)
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.error("DuckDB write failed for %s: %s", ticker, exc)
        _log_data_pull(ticker, triggered_by, started_at,
                       datetime.now(timezone.utc), latency_ms, rows_stored,
                       is_error=True, error_message=str(exc))
        return PipelineResult(
            success=False, ticker=ticker,
            error="Data fetched but database write failed. Try again.",
            error_type="db_error",
        )

    latency_ms = int((time.monotonic() - t0) * 1000)
    _log_data_pull(ticker, triggered_by, started_at,
                   datetime.now(timezone.utc), latency_ms, rows_stored)

    # ── 9. Build stock card ───────────────────────────────────────────────────
    # Prefer regularMarketPrice (15-20 min delayed) over close
    current_price = _safe_float(
        info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose")
    )
    if current_price is None and not hist.empty:
        current_price = _safe_float(hist["Close"].iloc[-1])

    # Daily change
    change_usd = _safe_float(info.get("regularMarketChange"))
    change_pct  = _safe_float(info.get("regularMarketChangePercent"))  # already in %, e.g. 0.67

    # Volume
    volume = _safe_int(
        info.get("regularMarketVolume") or info.get("volume")
    )
    if volume is None and not hist.empty:
        volume = _safe_int(hist["Volume"].iloc[-1])

    stock_card = StockCard(
        ticker=ticker,
        company_name=info.get("longName") or info.get("shortName") or ticker,
        exchange=_exchange_display(exchange),
        sector=info.get("sector") or "N/A",
        industry=info.get("industry") or "N/A",
        current_price=current_price,
        change_usd=change_usd,
        change_pct=change_pct,
        volume=volume,
        market_cap=market_cap,
        fifty_two_wk_high=_safe_float(info.get("fiftyTwoWeekHigh")),
        fifty_two_wk_low=_safe_float(info.get("fiftyTwoWeekLow")),
        fetched_at=fetched_at,
    )

    logger.info(
        "Pipeline complete: %s | price=$%.2f | latency=%dms | rows=%d",
        ticker, current_price or 0, latency_ms, rows_stored,
    )

    return PipelineResult(success=True, ticker=ticker, stock_card=stock_card)


def _exchange_display(exchange_code: str) -> str:
    """Map yfinance exchange code to clean display name."""
    return {
        "NYQ": "NYSE", "NYSE": "NYSE",
        "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NASDAQ": "NASDAQ",
    }.get(exchange_code, exchange_code)
