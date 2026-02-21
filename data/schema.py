"""
data/schema.py
DuckDB schema — 5 tables for Project-01.

Tables:
  stock_prices       — Daily OHLCV data (15-20 min delayed via yfinance)
  stock_fundamentals — Company info, financial ratios, balance sheet
  analysis_cache     — Full reports keyed on ticker + date (daily expiry)
  api_usage          — Token counts and costs per agent per call
  agent_logs         — Per-agent daily activity: wins, losses, blockers

Call setup_database() on app startup. It is idempotent — safe to call every time.
"""

import logging
from pathlib import Path

import duckdb

from config import settings

logger = logging.getLogger(__name__)


# ── Table DDL ─────────────────────────────────────────────────────────────────

_CREATE_STOCK_PRICES = """
CREATE TABLE IF NOT EXISTS stock_prices (
    id          BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker      VARCHAR     NOT NULL,
    price_date  DATE        NOT NULL,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    adj_close   DOUBLE,
    volume      BIGINT,
    -- yfinance provides 15-20 min delayed data. This is NOT real-time.
    source      VARCHAR     DEFAULT 'yfinance',
    delay_note  VARCHAR     DEFAULT '15-20 min delayed. Not real-time.',
    fetched_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (ticker, price_date)
);
"""

_CREATE_STOCK_FUNDAMENTALS = """
CREATE TABLE IF NOT EXISTS stock_fundamentals (
    id                  BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker              VARCHAR     NOT NULL,
    report_date         DATE        NOT NULL,
    -- Company metadata
    company_name        VARCHAR,
    exchange            VARCHAR,
    sector              VARCHAR,
    industry            VARCHAR,
    -- Valuation
    market_cap          DOUBLE,
    pe_ratio            DOUBLE,
    forward_pe          DOUBLE,
    pb_ratio            DOUBLE,
    ps_ratio            DOUBLE,
    peg_ratio           DOUBLE,
    -- Financial health
    debt_to_equity      DOUBLE,
    current_ratio       DOUBLE,
    quick_ratio         DOUBLE,
    -- Profitability
    roe                 DOUBLE,
    roa                 DOUBLE,
    profit_margin       DOUBLE,
    operating_margin    DOUBLE,
    -- Growth
    revenue_growth      DOUBLE,
    earnings_growth     DOUBLE,
    -- Other
    dividend_yield      DOUBLE,
    beta                DOUBLE,
    shares_outstanding  BIGINT,
    float_shares        BIGINT,
    fifty_two_wk_high   DOUBLE,
    fifty_two_wk_low    DOUBLE,
    -- Full yfinance info payload for fields added later without schema migration
    raw_json            JSON,
    fetched_at          TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (ticker, report_date)
);
"""

_CREATE_ANALYSIS_CACHE = """
CREATE TABLE IF NOT EXISTS analysis_cache (
    id                      BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker                  VARCHAR     NOT NULL,
    -- One report per ticker per calendar day. Expires at midnight ET.
    analysis_date           DATE        NOT NULL,
    report_json             JSON,
    value_conviction_score  DOUBLE,
    total_cost_usd          DOUBLE,
    total_input_tokens      INTEGER,
    total_output_tokens     INTEGER,
    -- Comma-separated list of models used, e.g. 'claude-opus-4-6,claude-sonnet-4-6'
    model_mix               VARCHAR,
    created_at              TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    expires_at              TIMESTAMP,
    UNIQUE (ticker, analysis_date)
);
"""

_CREATE_API_USAGE = """
CREATE TABLE IF NOT EXISTS api_usage (
    id                  BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent_name          VARCHAR     NOT NULL,
    model               VARCHAR     NOT NULL,
    ticker              VARCHAR,
    analysis_date       DATE,
    -- Token breakdown per call
    input_tokens        INTEGER     DEFAULT 0,
    output_tokens       INTEGER     DEFAULT 0,
    -- thinking_tokens: estimated from response content blocks (not reported separately by API)
    thinking_tokens     INTEGER     DEFAULT 0,
    -- Prompt caching counters (Anthropic cache_creation / cache_read tokens)
    cache_write_tokens  INTEGER     DEFAULT 0,
    cache_read_tokens   INTEGER     DEFAULT 0,
    -- Cost in USD for this single call
    cost_usd            DOUBLE      DEFAULT 0.0,
    call_duration_ms    INTEGER,
    created_at          TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_AGENT_LOGS = """
CREATE TABLE IF NOT EXISTS agent_logs (
    id          BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent_name  VARCHAR     NOT NULL,
    log_date    DATE        NOT NULL,
    ticker      VARCHAR,
    -- 'success' | 'partial' | 'failed' | 'skipped'
    status      VARCHAR,
    -- Free-text fields: what worked, what didn't, what's blocking progress
    wins        TEXT,
    losses      TEXT,
    blockers    TEXT,
    notes       TEXT,
    created_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
);
"""

# Ordered list for setup — order matters for dependency clarity
_ALL_TABLES: list[tuple[str, str]] = [
    ("stock_prices",        _CREATE_STOCK_PRICES),
    ("stock_fundamentals",  _CREATE_STOCK_FUNDAMENTALS),
    ("analysis_cache",      _CREATE_ANALYSIS_CACHE),
    ("api_usage",           _CREATE_API_USAGE),
    ("agent_logs",          _CREATE_AGENT_LOGS),
]


# ── Connection helper ─────────────────────────────────────────────────────────

def get_connection() -> duckdb.DuckDBPyConnection:
    """
    Return a connection to the project DuckDB file.

    Creates the parent directory if it doesn't exist. Callers are responsible
    for closing the connection when done.
    """
    db_path = Path(settings.DUCKDB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


# ── Setup ─────────────────────────────────────────────────────────────────────

def setup_database() -> None:
    """
    Create the DuckDB file and all 5 tables if they don't already exist.

    Idempotent — safe to call on every app startup. Uses IF NOT EXISTS on
    every CREATE TABLE so repeated calls are harmless.
    """
    db_path = Path(settings.DUCKDB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection()
    try:
        for table_name, ddl in _ALL_TABLES:
            conn.execute(ddl)
            logger.info("Table ready: %s", table_name)

        conn.commit()
        logger.info("Database setup complete: %s", db_path.resolve())

    except Exception as exc:
        logger.exception("Database setup failed: %s", exc)
        raise

    finally:
        conn.close()


def get_table_row_counts() -> dict[str, int]:
    """Return row counts for all tables. Useful for health checks and the UI."""
    conn = get_connection()
    try:
        counts: dict[str, int] = {}
        for table_name, _ in _ALL_TABLES:
            result = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
            counts[table_name] = result[0] if result else 0
        return counts
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
    setup_database()
    counts = get_table_row_counts()
    print("\nDatabase initialized. Table row counts:")
    for name, count in counts.items():
        print(f"  {name:<25} {count:>6} rows")
    sys.exit(0)
