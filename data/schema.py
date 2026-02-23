"""
data/schema.py
DuckDB schema v2.0 — 7 tables + 3 views + 13 indexes.

Implements DATABASE_SCHEMA_v2.md exactly.

Tables:
  analysis_runs       — Master record per analysis session (VARCHAR PK)
  analysis_results    — Each agent's output per run (sequence PK)
  analysis_cache      — Deduplication by ticker + date (composite PK)
  stock_prices        — Daily OHLCV data (composite PK: ticker + trade_date)
  stock_fundamentals  — Company profile + 40+ financial metrics (composite PK)
  api_usage           — Token-level cost tracking of every API call (sequence PK)
  agent_logs          — Daily accountability log per agent (composite PK)

Views (computed from api_usage — nothing derived ever stored):
  v_cost_daily_summary — Daily cost rollup
  v_cost_by_agent      — Agent-level breakdown for current month
  v_budget_tracker     — Real-time budget status vs $100/mo ceiling

Data type rules (from schema doc):
  Money/costs  → DECIMAL(10,6)
  Stock prices → DECIMAL(12,4)
  Percentages  → DECIMAL(8,4)   stored as decimals (0.1523 = 15.23%)
  Token counts → BIGINT
  Timestamps   → TIMESTAMP (always UTC, converted in application layer)
  Dates        → DATE

Call setup_database() on every app startup — it is idempotent.
"""

import logging
from pathlib import Path

import duckdb

from config import settings

logger = logging.getLogger(__name__)


# ── Sequences ─────────────────────────────────────────────────────────────────

_CREATE_SEQ_ANALYSIS_RESULTS = "CREATE SEQUENCE IF NOT EXISTS seq_analysis_results START 1;"
_CREATE_SEQ_API_USAGE = "CREATE SEQUENCE IF NOT EXISTS seq_api_usage START 1;"


# ── Table 1: analysis_runs ────────────────────────────────────────────────────

_CREATE_ANALYSIS_RUNS = """
CREATE TABLE IF NOT EXISTS analysis_runs (
    -- Primary Key
    run_id                  VARCHAR PRIMARY KEY,        -- 'run_20260221_143022_AAPL'

    -- Stock identification
    ticker                  VARCHAR NOT NULL,
    company_name            VARCHAR,
    exchange                VARCHAR,                    -- 'NASDAQ', 'NYSE'
    sector                  VARCHAR,
    industry                VARCHAR,
    market_cap              BIGINT,

    -- Timing
    started_at              TIMESTAMP NOT NULL,
    completed_at            TIMESTAMP,
    duration_seconds        DECIMAL(8,2),

    -- Core results
    value_conviction_score  INTEGER,                    -- 0-100, NULL if failed
    purchase_price_target   DECIMAL(12,4),
    current_price           DECIMAL(12,4),
    signal                  VARCHAR,                    -- 'BUY', 'HOLD', 'AVOID'

    -- Status
    status                  VARCHAR DEFAULT 'running',  -- 'running','completed','failed','cached'
    failure_reason          VARCHAR,
    used_cache              BOOLEAN DEFAULT FALSE,

    -- Cost totals (summed from api_usage after run completes)
    total_cost_usd          DECIMAL(10,6) DEFAULT 0,
    total_input_tokens      BIGINT DEFAULT 0,
    total_output_tokens     BIGINT DEFAULT 0,
    total_thinking_tokens   BIGINT DEFAULT 0,
    total_cached_tokens     BIGINT DEFAULT 0,
    agent_calls_count       INTEGER DEFAULT 0,

    -- Context
    triggered_by            VARCHAR DEFAULT 'user',     -- 'user', 'scheduled_refresh', 'deep_dive'
    app_version             VARCHAR,

    -- Required date columns
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


# ── Table 2: analysis_results ─────────────────────────────────────────────────

_CREATE_ANALYSIS_RESULTS = """
CREATE TABLE IF NOT EXISTS analysis_results (
    -- Primary Key
    result_id               INTEGER PRIMARY KEY DEFAULT nextval('seq_analysis_results'),

    -- Foreign Key
    run_id                  VARCHAR NOT NULL,
    ticker                  VARCHAR NOT NULL,

    -- Agent identification
    agent_name              VARCHAR NOT NULL,
    agent_model             VARCHAR,
    execution_order         INTEGER,

    -- Timing
    started_at              TIMESTAMP,
    completed_at            TIMESTAMP,
    duration_seconds        DECIMAL(8,2),

    -- Input / Output
    input_summary           VARCHAR,
    output_text             VARCHAR,
    output_json             VARCHAR,

    -- Agent-specific results
    score_value             INTEGER,                    -- Value Conviction Score (fundamental_analyst)
    purchase_price          DECIMAL(12,4),
    signal                  VARCHAR,
    catalysts               VARCHAR,                    -- JSON array (finance_researcher)
    price_movement_context  VARCHAR,
    technical_signals       VARCHAR,                    -- JSON: RSI, MACD, etc. (technical_analyst)

    -- Conflict tracking (manager)
    conflict_detected       BOOLEAN DEFAULT FALSE,
    conflict_description    VARCHAR,
    conflict_resolution     VARCHAR,

    -- QA results
    qa_passed               BOOLEAN,
    qa_issues_found         VARCHAR,
    hallucinations_caught   INTEGER DEFAULT 0,

    -- Status
    status                  VARCHAR DEFAULT 'running',
    error_message           VARCHAR,

    -- Cost for this specific agent call
    cost_usd                DECIMAL(10,6) DEFAULT 0,
    input_tokens            BIGINT DEFAULT 0,
    output_tokens           BIGINT DEFAULT 0,
    thinking_tokens         BIGINT DEFAULT 0,
    cached_tokens           BIGINT DEFAULT 0,

    -- Required date columns
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
);
"""


# ── Table 3: analysis_cache ───────────────────────────────────────────────────

_CREATE_ANALYSIS_CACHE = """
CREATE TABLE IF NOT EXISTS analysis_cache (
    -- Primary Key (composite)
    ticker                  VARCHAR NOT NULL,
    cache_date              DATE NOT NULL,

    -- Cached reference
    run_id                  VARCHAR NOT NULL,

    -- Cached results (denormalized for fast serving)
    value_conviction_score  INTEGER,
    purchase_price_target   DECIMAL(12,4),
    current_price           DECIMAL(12,4),
    signal                  VARCHAR,
    full_report_json        VARCHAR,

    -- Usage tracking
    hit_count               INTEGER DEFAULT 0,
    last_hit_at             TIMESTAMP,
    original_cost_usd       DECIMAL(10,6),

    -- Required date columns
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at              TIMESTAMP NOT NULL,

    PRIMARY KEY (ticker, cache_date),
    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
);
"""


# ── Table 4: stock_prices ─────────────────────────────────────────────────────

_CREATE_STOCK_PRICES = """
CREATE TABLE IF NOT EXISTS stock_prices (
    -- Primary Key (composite)
    ticker                  VARCHAR NOT NULL,
    trade_date              DATE NOT NULL,

    -- OHLCV (core market data)
    open_price              DECIMAL(12,4),
    high_price              DECIMAL(12,4),
    low_price               DECIMAL(12,4),
    close_price             DECIMAL(12,4),
    adj_close               DECIMAL(12,4),
    volume                  BIGINT,

    -- Daily movement
    daily_change_usd        DECIMAL(12,4),
    daily_change_pct        DECIMAL(8,4),

    -- Context window
    fifty_two_week_high     DECIMAL(12,4),
    fifty_two_week_low      DECIMAL(12,4),
    avg_volume_10d          BIGINT,
    avg_volume_30d          BIGINT,

    -- AI-generated context (populated by Finance Researcher in Phase 4)
    price_movement_context  VARCHAR,
    movement_catalysts      VARCHAR,
    movement_sentiment      VARCHAR,

    -- Data quality
    data_source             VARCHAR DEFAULT 'yfinance',
    data_delay_minutes      INTEGER DEFAULT 20,
    pulled_at               TIMESTAMP,
    is_trading_day          BOOLEAN DEFAULT TRUE,

    -- Required date columns
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (ticker, trade_date)
);
"""


# ── Table 5: stock_fundamentals ───────────────────────────────────────────────

_CREATE_STOCK_FUNDAMENTALS = """
CREATE TABLE IF NOT EXISTS stock_fundamentals (
    -- Primary Key (composite)
    ticker                  VARCHAR NOT NULL,
    snapshot_date           DATE NOT NULL,

    -- Company Profile
    company_name            VARCHAR,
    exchange                VARCHAR,
    sector                  VARCHAR,
    industry                VARCHAR,
    market_cap              BIGINT,
    market_cap_category     VARCHAR,                     -- 'mega','large','mid','small'
    employees               INTEGER,
    country                 VARCHAR,
    website                 VARCHAR,
    business_summary        VARCHAR,

    -- Valuation Ratios
    pe_ratio_ttm            DECIMAL(10,4),
    pe_ratio_forward        DECIMAL(10,4),
    pb_ratio                DECIMAL(10,4),
    ps_ratio                DECIMAL(10,4),
    peg_ratio               DECIMAL(10,4),
    ev_to_ebitda            DECIMAL(10,4),
    ev_to_revenue           DECIMAL(10,4),
    price_to_fcf            DECIMAL(10,4),
    enterprise_value        BIGINT,

    -- Profitability (stored as decimals: 0.4523 = 45.23%)
    gross_margin            DECIMAL(8,4),
    operating_margin        DECIMAL(8,4),
    profit_margin           DECIMAL(8,4),
    roe                     DECIMAL(8,4),
    roa                     DECIMAL(8,4),
    roic                    DECIMAL(8,4),

    -- Growth
    revenue_growth_yoy      DECIMAL(8,4),
    earnings_growth_yoy     DECIMAL(8,4),
    revenue_growth_qoq      DECIMAL(8,4),
    earnings_growth_qoq     DECIMAL(8,4),

    -- Income Statement
    total_revenue           BIGINT,
    gross_profit            BIGINT,
    operating_income        BIGINT,
    net_income              BIGINT,
    ebitda                  BIGINT,
    eps_ttm                 DECIMAL(10,4),
    eps_forward             DECIMAL(10,4),

    -- Balance Sheet
    total_cash              BIGINT,
    total_debt              BIGINT,
    net_cash                BIGINT,
    debt_to_equity          DECIMAL(10,4),
    current_ratio           DECIMAL(8,4),
    quick_ratio             DECIMAL(8,4),
    book_value_per_share    DECIMAL(12,4),

    -- Cash Flow
    operating_cash_flow     BIGINT,
    free_cash_flow          BIGINT,
    fcf_per_share           DECIMAL(12,4),
    capex                   BIGINT,

    -- Dividends
    dividend_yield          DECIMAL(8,4),
    dividend_rate           DECIMAL(10,4),
    payout_ratio            DECIMAL(8,4),
    ex_dividend_date        DATE,

    -- Analyst Consensus
    analyst_target_mean     DECIMAL(12,4),
    analyst_target_high     DECIMAL(12,4),
    analyst_target_low      DECIMAL(12,4),
    analyst_recommendation  VARCHAR,
    number_of_analysts      INTEGER,

    -- Data quality
    data_source             VARCHAR DEFAULT 'yfinance',
    pulled_at               TIMESTAMP,
    fiscal_year_end         VARCHAR,
    most_recent_quarter     DATE,

    -- Required date columns
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (ticker, snapshot_date)
);
"""


# ── Table 6: api_usage ────────────────────────────────────────────────────────

_CREATE_API_USAGE = """
CREATE TABLE IF NOT EXISTS api_usage (
    -- Primary Key
    usage_id                INTEGER PRIMARY KEY DEFAULT nextval('seq_api_usage'),

    -- What triggered this call
    run_id                  VARCHAR,                     -- NULL for system/scheduled calls
    ticker                  VARCHAR,
    triggered_by            VARCHAR,                     -- 'user_analysis','scheduled_refresh','system'

    -- Who made the call
    agent_name              VARCHAR NOT NULL,
    agent_role              VARCHAR,

    -- API target
    api_provider            VARCHAR NOT NULL,            -- 'anthropic','google','openai','yfinance','system'
    api_endpoint            VARCHAR,
    model                   VARCHAR,
    model_tier              VARCHAR,                     -- 'premium','standard','economy','free'

    -- Token breakdown: INPUT
    input_tokens            BIGINT DEFAULT 0,
    input_cached_tokens     BIGINT DEFAULT 0,
    input_uncached_tokens   BIGINT DEFAULT 0,
    system_prompt_tokens    BIGINT DEFAULT 0,
    user_content_tokens     BIGINT DEFAULT 0,

    -- Token breakdown: OUTPUT
    output_tokens           BIGINT DEFAULT 0,
    thinking_tokens         BIGINT DEFAULT 0,
    response_tokens         BIGINT DEFAULT 0,

    -- Total tokens
    total_tokens            BIGINT DEFAULT 0,

    -- Cost: RAW FACTS ONLY
    input_cost_usd          DECIMAL(10,6) DEFAULT 0,
    output_cost_usd         DECIMAL(10,6) DEFAULT 0,
    thinking_cost_usd       DECIMAL(10,6) DEFAULT 0,
    total_cost_usd          DECIMAL(10,6) DEFAULT 0,

    -- Prompt caching
    prompt_cache_status     VARCHAR DEFAULT 'none',
    cache_creation_tokens   BIGINT DEFAULT 0,
    cache_read_tokens       BIGINT DEFAULT 0,

    -- Performance
    request_started_at      TIMESTAMP,
    request_completed_at    TIMESTAMP,
    latency_ms              INTEGER,
    time_to_first_token_ms  INTEGER,

    -- Request metadata
    http_status_code        INTEGER,
    request_id              VARCHAR,
    api_version             VARCHAR,

    -- Error tracking
    is_error                BOOLEAN DEFAULT FALSE,
    was_retry               BOOLEAN DEFAULT FALSE,
    retry_count             INTEGER DEFAULT 0,
    error_type              VARCHAR,
    error_message           VARCHAR,

    -- Context
    session_id              VARCHAR,
    environment             VARCHAR DEFAULT 'production',
    app_version             VARCHAR,

    -- Required date columns
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
);
"""


# ── Table 7: agent_logs ───────────────────────────────────────────────────────

_CREATE_AGENT_LOGS = """
CREATE TABLE IF NOT EXISTS agent_logs (
    -- Primary Key (composite)
    log_date                DATE NOT NULL,
    agent_name              VARCHAR NOT NULL,

    -- Daily report
    what_i_did              VARCHAR,
    wins                    VARCHAR,
    losses                  VARCHAR,
    struggles               VARCHAR,
    blockers                VARCHAR,

    -- Daily metrics
    analyses_completed      INTEGER DEFAULT 0,
    api_calls_made          INTEGER DEFAULT 0,
    total_tokens_used       BIGINT DEFAULT 0,
    total_cost_usd          DECIMAL(10,6) DEFAULT 0,
    errors_encountered      INTEGER DEFAULT 0,
    avg_latency_ms          INTEGER,

    -- Quality
    qa_issues_flagged       INTEGER DEFAULT 0,
    hallucinations_caught   INTEGER DEFAULT 0,
    conflicts_resolved      INTEGER DEFAULT 0,

    -- Required date columns
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (log_date, agent_name)
);
"""


# ── View 1: v_cost_daily_summary ─────────────────────────────────────────────

_CREATE_V_COST_DAILY_SUMMARY = """
CREATE OR REPLACE VIEW v_cost_daily_summary AS
SELECT
    created_date                                            AS summary_date,
    SUM(total_cost_usd)                                     AS total_cost_usd,
    COUNT(*)                                                AS total_api_calls,
    COUNT(DISTINCT run_id)                                  AS total_analyses,
    COUNT(CASE WHEN prompt_cache_status = 'hit' THEN 1 END) AS total_cache_hits,
    SUM(input_tokens)                                       AS total_input_tokens,
    SUM(output_tokens)                                      AS total_output_tokens,
    SUM(thinking_tokens)                                    AS total_thinking_tokens,
    SUM(input_cached_tokens)                                AS total_cached_tokens,
    SUM(CASE WHEN api_provider = 'anthropic' THEN total_cost_usd ELSE 0 END) AS anthropic_cost,
    SUM(CASE WHEN api_provider = 'google'    THEN total_cost_usd ELSE 0 END) AS google_cost,
    SUM(CASE WHEN api_provider = 'openai'    THEN total_cost_usd ELSE 0 END) AS openai_cost,
    SUM(CASE WHEN model_tier = 'premium'  THEN total_cost_usd ELSE 0 END)   AS premium_cost,
    SUM(CASE WHEN model_tier = 'standard' THEN total_cost_usd ELSE 0 END)   AS standard_cost,
    SUM(CASE WHEN model_tier = 'economy'  THEN total_cost_usd ELSE 0 END)   AS economy_cost,
    SUM(CASE WHEN agent_name = 'manager'              THEN total_cost_usd ELSE 0 END) AS manager_cost,
    SUM(CASE WHEN agent_name = 'fundamental_analyst'  THEN total_cost_usd ELSE 0 END) AS fundamental_cost,
    SUM(CASE WHEN agent_name = 'technical_analyst'    THEN total_cost_usd ELSE 0 END) AS technical_cost,
    SUM(CASE WHEN agent_name = 'finance_researcher'   THEN total_cost_usd ELSE 0 END) AS researcher_cost,
    SUM(CASE WHEN agent_name = 'business_analyst'     THEN total_cost_usd ELSE 0 END) AS ba_cost,
    SUM(CASE WHEN agent_name = 'project_coordinator'  THEN total_cost_usd ELSE 0 END) AS pc_cost,
    AVG(total_cost_usd)                                     AS avg_cost_per_call,
    AVG(latency_ms)                                         AS avg_latency_ms
FROM api_usage
WHERE is_error = FALSE
GROUP BY created_date
ORDER BY created_date DESC;
"""


# ── View 2: v_cost_by_agent ───────────────────────────────────────────────────

_CREATE_V_COST_BY_AGENT = """
CREATE OR REPLACE VIEW v_cost_by_agent AS
SELECT
    agent_name,
    model,
    model_tier,
    api_provider,
    COUNT(*)                                    AS call_count,
    SUM(total_cost_usd)                         AS total_cost,
    SUM(input_tokens)                           AS total_input_tokens,
    SUM(output_tokens)                          AS total_output_tokens,
    SUM(thinking_tokens)                        AS total_thinking_tokens,
    SUM(input_cached_tokens)                    AS total_cached_tokens,
    AVG(latency_ms)                             AS avg_latency_ms,
    SUM(CASE WHEN is_error THEN 1 ELSE 0 END)  AS error_count,
    COUNT(DISTINCT run_id)                      AS analyses_served
FROM api_usage
WHERE created_date >= DATE_TRUNC('month', CURRENT_DATE)
GROUP BY agent_name, model, model_tier, api_provider
ORDER BY total_cost DESC;
"""


# ── View 3: v_budget_tracker ──────────────────────────────────────────────────

_CREATE_V_BUDGET_TRACKER = """
CREATE OR REPLACE VIEW v_budget_tracker AS
WITH monthly AS (
    SELECT
        SUM(total_cost_usd)          AS monthly_spend,
        COUNT(DISTINCT created_date) AS days_with_activity,
        COUNT(*)                     AS total_calls,
        COUNT(DISTINCT run_id)       AS total_analyses
    FROM api_usage
    WHERE created_date >= DATE_TRUNC('month', CURRENT_DATE)
      AND is_error = FALSE
),
projection AS (
    SELECT
        monthly_spend,
        days_with_activity,
        total_calls,
        total_analyses,
        100.00                                               AS monthly_budget,
        100.00 - monthly_spend                               AS budget_remaining,
        CASE WHEN days_with_activity > 0
             THEN monthly_spend / days_with_activity
             ELSE 0
        END                                                  AS avg_daily_spend,
        EXTRACT(DAY FROM LAST_DAY(CURRENT_DATE))             AS days_in_month,
        EXTRACT(DAY FROM LAST_DAY(CURRENT_DATE))
            - EXTRACT(DAY FROM CURRENT_DATE)                 AS days_remaining
    FROM monthly
)
SELECT
    monthly_spend,
    budget_remaining,
    monthly_budget,
    avg_daily_spend,
    avg_daily_spend * (days_remaining + EXTRACT(DAY FROM CURRENT_DATE)) AS projected_monthly_cost,
    total_calls,
    total_analyses,
    days_with_activity,
    days_remaining,
    CASE
        WHEN avg_daily_spend * (days_remaining + EXTRACT(DAY FROM CURRENT_DATE)) <= 100
        THEN TRUE ELSE FALSE
    END AS on_track_for_budget,
    CASE
        WHEN monthly_spend >= 80 THEN 'CRITICAL'
        WHEN monthly_spend >= 60 THEN 'WARNING'
        ELSE 'HEALTHY'
    END AS budget_status
FROM projection;
"""


# ── Indexes ───────────────────────────────────────────────────────────────────

_INDEXES = [
    # api_usage — highest query volume
    "CREATE INDEX IF NOT EXISTS idx_api_date     ON api_usage(created_date);",
    "CREATE INDEX IF NOT EXISTS idx_api_agent    ON api_usage(agent_name);",
    "CREATE INDEX IF NOT EXISTS idx_api_run      ON api_usage(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_api_provider ON api_usage(api_provider);",
    "CREATE INDEX IF NOT EXISTS idx_api_model    ON api_usage(model);",
    "CREATE INDEX IF NOT EXISTS idx_api_ticker   ON api_usage(ticker);",
    # analysis_runs
    "CREATE INDEX IF NOT EXISTS idx_runs_ticker  ON analysis_runs(ticker);",
    "CREATE INDEX IF NOT EXISTS idx_runs_date    ON analysis_runs(created_date);",
    "CREATE INDEX IF NOT EXISTS idx_runs_status  ON analysis_runs(status);",
    # analysis_results
    "CREATE INDEX IF NOT EXISTS idx_results_run   ON analysis_results(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_results_agent ON analysis_results(agent_name);",
    "CREATE INDEX IF NOT EXISTS idx_results_date  ON analysis_results(created_date);",
    # stock_prices
    "CREATE INDEX IF NOT EXISTS idx_prices_ticker ON stock_prices(ticker);",
    "CREATE INDEX IF NOT EXISTS idx_prices_date   ON stock_prices(trade_date);",
    # stock_fundamentals
    "CREATE INDEX IF NOT EXISTS idx_fund_ticker ON stock_fundamentals(ticker);",
    "CREATE INDEX IF NOT EXISTS idx_fund_date   ON stock_fundamentals(snapshot_date);",
]


# ── Setup sequence ────────────────────────────────────────────────────────────

_SETUP_STEPS: list[tuple[str, str]] = [
    ("sequence: seq_analysis_results", _CREATE_SEQ_ANALYSIS_RESULTS),
    ("sequence: seq_api_usage",        _CREATE_SEQ_API_USAGE),
    ("table: analysis_runs",           _CREATE_ANALYSIS_RUNS),
    ("table: analysis_results",        _CREATE_ANALYSIS_RESULTS),
    ("table: analysis_cache",          _CREATE_ANALYSIS_CACHE),
    ("table: stock_prices",            _CREATE_STOCK_PRICES),
    ("table: stock_fundamentals",      _CREATE_STOCK_FUNDAMENTALS),
    ("table: api_usage",               _CREATE_API_USAGE),
    ("table: agent_logs",              _CREATE_AGENT_LOGS),
    ("view: v_cost_daily_summary",     _CREATE_V_COST_DAILY_SUMMARY),
    ("view: v_cost_by_agent",          _CREATE_V_COST_BY_AGENT),
    ("view: v_budget_tracker",         _CREATE_V_BUDGET_TRACKER),
]

_TABLE_NAMES = [
    "analysis_runs", "analysis_results", "analysis_cache",
    "stock_prices", "stock_fundamentals", "api_usage", "agent_logs",
]


# ── Connection helper ─────────────────────────────────────────────────────────

def get_connection() -> duckdb.DuckDBPyConnection:
    """Return a persistent connection to the project DuckDB file."""
    db_path = Path(settings.DUCKDB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


# ── Setup ─────────────────────────────────────────────────────────────────────

def setup_database() -> None:
    """
    Create the DuckDB file + all 7 tables, 3 views, and 13 indexes.

    Idempotent — safe to call on every app startup.
    Uses IF NOT EXISTS / CREATE OR REPLACE so repeated calls are harmless.
    """
    db_path = Path(settings.DUCKDB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection()
    try:
        for label, ddl in _SETUP_STEPS:
            conn.execute(ddl)
            logger.debug("Ready: %s", label)

        for idx_sql in _INDEXES:
            conn.execute(idx_sql)

        conn.commit()
        logger.info("Database v2 setup complete: %s", db_path.resolve())

    except Exception as exc:
        logger.exception("Database setup failed: %s", exc)
        raise
    finally:
        conn.close()


def get_table_row_counts() -> dict[str, int]:
    """Return row counts for all 7 tables. Used for health checks."""
    conn = get_connection()
    try:
        return {
            t: (conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone() or [0])[0]
            for t in _TABLE_NAMES
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
    setup_database()
    counts = get_table_row_counts()
    print("\nDatabase v2 initialized. Row counts:")
    for name, count in counts.items():
        print(f"  {name:<25} {count:>6} rows")
    sys.exit(0)
