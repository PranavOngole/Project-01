"""
data/schema.py
DuckDB schema v2.1 — 14 tables + 3 views + indexes + agent seeding.

Sources:
  Tables 1–7   → docs/DATABASE_SCHEMA_v2.md
  Tables 8–11  → docs/decisions/ARCHITECTURE_DECISIONS_MANAGER_REVIEW.md
  Tables 12–14 → docs/decisions/ARCHITECTURE_EVOLUTION_NOTES.md

Tables:
  1.  analysis_runs        — Master record per analysis session (VARCHAR PK)
  2.  analysis_results     — Each agent's output per run (SEQUENCE PK)
  3.  analysis_cache       — Same-day deduplication (composite PK: ticker + date)
  4.  stock_prices         — Daily OHLCV (composite PK: ticker + trade_date)
  5.  stock_fundamentals   — Company profile + financials (composite PK)
  6.  api_usage            — Token-level cost tracking (SEQUENCE PK)
  7.  agent_logs           — Daily agent report (composite PK: log_date + agent_id)
  8.  communication_log    — Inter-agent messages via orchestrator (SEQUENCE PK)
  9.  escalation_alerts    — Failure escalation records (SEQUENCE PK)
  10. agent_registry       — Agent config source of truth (agent_id VARCHAR PK)
  11. learning_log         — Continuous learning entries (SEQUENCE PK)
  12. weekly_universe      — Curated 20 stocks/week (SEQUENCE PK)
  13. stock_requests       — User-submitted ticker requests (SEQUENCE PK)
  14. signal_history       — Signal change audit trail (SEQUENCE PK)

Views (derived from api_usage — nothing computed ever stored):
  v_cost_daily_summary — Daily cost rollup
  v_cost_by_agent      — Agent-level breakdown for current month
  v_budget_tracker     — Real-time budget vs $100/mo ceiling

Data type rules:
  Money/costs  → DECIMAL(10,6)   never DOUBLE or FLOAT
  Stock prices → DECIMAL(12,4)
  Percentages  → DECIMAL(8,4)    stored as decimals (0.1523 = 15.23%)
  Token counts → BIGINT
  Timestamps   → TIMESTAMP       always UTC
  Dates        → DATE

Call init_db() or setup_database() on every app startup — idempotent.
"""

import logging
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from config import settings

logger = logging.getLogger(__name__)


# ── Sequences ─────────────────────────────────────────────────────────────────

_SEQUENCES = [
    "CREATE SEQUENCE IF NOT EXISTS seq_analysis_results START 1;",
    "CREATE SEQUENCE IF NOT EXISTS seq_api_usage START 1;",
    "CREATE SEQUENCE IF NOT EXISTS seq_comm_log START 1;",
    "CREATE SEQUENCE IF NOT EXISTS seq_escalation START 1;",
    "CREATE SEQUENCE IF NOT EXISTS seq_learning_log START 1;",
    "CREATE SEQUENCE IF NOT EXISTS seq_weekly_universe START 1;",
    "CREATE SEQUENCE IF NOT EXISTS seq_stock_requests START 1;",
    "CREATE SEQUENCE IF NOT EXISTS seq_signal_history START 1;",
]


# ── Table 1: analysis_runs ────────────────────────────────────────────────────

_CREATE_ANALYSIS_RUNS = """
CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id                  VARCHAR PRIMARY KEY,
    ticker                  VARCHAR NOT NULL,
    company_name            VARCHAR,
    exchange                VARCHAR,
    sector                  VARCHAR,
    industry                VARCHAR,
    market_cap              BIGINT,
    started_at              TIMESTAMP NOT NULL,
    completed_at            TIMESTAMP,
    duration_seconds        DECIMAL(8,2),
    value_conviction_score  INTEGER,
    purchase_price_target   DECIMAL(12,4),
    current_price           DECIMAL(12,4),
    signal                  VARCHAR,
    status                  VARCHAR DEFAULT 'running',
    failure_reason          VARCHAR,
    used_cache              BOOLEAN DEFAULT FALSE,
    total_cost_usd          DECIMAL(10,6) DEFAULT 0,
    total_input_tokens      BIGINT DEFAULT 0,
    total_output_tokens     BIGINT DEFAULT 0,
    total_thinking_tokens   BIGINT DEFAULT 0,
    total_cached_tokens     BIGINT DEFAULT 0,
    agent_calls_count       INTEGER DEFAULT 0,
    triggered_by            VARCHAR DEFAULT 'user',
    app_version             VARCHAR,
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ── Table 2: analysis_results ─────────────────────────────────────────────────

_CREATE_ANALYSIS_RESULTS = """
CREATE TABLE IF NOT EXISTS analysis_results (
    result_id               INTEGER PRIMARY KEY DEFAULT nextval('seq_analysis_results'),
    run_id                  VARCHAR NOT NULL,
    ticker                  VARCHAR NOT NULL,
    agent_name              VARCHAR NOT NULL,
    agent_id                VARCHAR,
    agent_model             VARCHAR,
    execution_order         INTEGER,
    started_at              TIMESTAMP,
    completed_at            TIMESTAMP,
    duration_seconds        DECIMAL(8,2),
    input_summary           VARCHAR,
    output_text             VARCHAR,
    output_json             VARCHAR,
    score_value             INTEGER,
    purchase_price          DECIMAL(12,4),
    signal                  VARCHAR,
    catalysts               VARCHAR,
    price_movement_context  VARCHAR,
    technical_signals       VARCHAR,
    conflict_detected       BOOLEAN DEFAULT FALSE,
    conflict_description    VARCHAR,
    conflict_resolution     VARCHAR,
    qa_passed               BOOLEAN,
    qa_issues_found         VARCHAR,
    hallucinations_caught   INTEGER DEFAULT 0,
    status                  VARCHAR DEFAULT 'running',
    error_message           VARCHAR,
    cost_usd                DECIMAL(10,6) DEFAULT 0,
    input_tokens            BIGINT DEFAULT 0,
    output_tokens           BIGINT DEFAULT 0,
    thinking_tokens         BIGINT DEFAULT 0,
    cached_tokens           BIGINT DEFAULT 0,
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
);
"""

# ── Table 3: analysis_cache ───────────────────────────────────────────────────

_CREATE_ANALYSIS_CACHE = """
CREATE TABLE IF NOT EXISTS analysis_cache (
    ticker                  VARCHAR NOT NULL,
    cache_date              DATE NOT NULL,
    run_id                  VARCHAR NOT NULL,
    value_conviction_score  INTEGER,
    purchase_price_target   DECIMAL(12,4),
    current_price           DECIMAL(12,4),
    signal                  VARCHAR,
    full_report_json        VARCHAR,
    hit_count               INTEGER DEFAULT 0,
    last_hit_at             TIMESTAMP,
    original_cost_usd       DECIMAL(10,6),
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
    ticker                  VARCHAR NOT NULL,
    trade_date              DATE NOT NULL,
    open_price              DECIMAL(12,4),
    high_price              DECIMAL(12,4),
    low_price               DECIMAL(12,4),
    close_price             DECIMAL(12,4),
    adj_close               DECIMAL(12,4),
    volume                  BIGINT,
    daily_change_usd        DECIMAL(12,4),
    daily_change_pct        DECIMAL(8,4),
    fifty_two_week_high     DECIMAL(12,4),
    fifty_two_week_low      DECIMAL(12,4),
    avg_volume_10d          BIGINT,
    avg_volume_30d          BIGINT,
    price_movement_context  VARCHAR,
    movement_catalysts      VARCHAR,
    movement_sentiment      VARCHAR,
    data_source             VARCHAR DEFAULT 'yfinance',
    data_delay_minutes      INTEGER DEFAULT 20,
    pulled_at               TIMESTAMP,
    is_trading_day          BOOLEAN DEFAULT TRUE,
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, trade_date)
);
"""

# ── Table 5: stock_fundamentals ───────────────────────────────────────────────

_CREATE_STOCK_FUNDAMENTALS = """
CREATE TABLE IF NOT EXISTS stock_fundamentals (
    ticker                  VARCHAR NOT NULL,
    snapshot_date           DATE NOT NULL,
    company_name            VARCHAR,
    exchange                VARCHAR,
    sector                  VARCHAR,
    industry                VARCHAR,
    market_cap              BIGINT,
    market_cap_category     VARCHAR,
    employees               INTEGER,
    country                 VARCHAR,
    website                 VARCHAR,
    business_summary        VARCHAR,
    pe_ratio_ttm            DECIMAL(10,4),
    pe_ratio_forward        DECIMAL(10,4),
    pb_ratio                DECIMAL(10,4),
    ps_ratio                DECIMAL(10,4),
    peg_ratio               DECIMAL(10,4),
    ev_to_ebitda            DECIMAL(10,4),
    ev_to_revenue           DECIMAL(10,4),
    price_to_fcf            DECIMAL(10,4),
    enterprise_value        BIGINT,
    gross_margin            DECIMAL(8,4),
    operating_margin        DECIMAL(8,4),
    profit_margin           DECIMAL(8,4),
    roe                     DECIMAL(8,4),
    roa                     DECIMAL(8,4),
    roic                    DECIMAL(8,4),
    revenue_growth_yoy      DECIMAL(8,4),
    earnings_growth_yoy     DECIMAL(8,4),
    revenue_growth_qoq      DECIMAL(8,4),
    earnings_growth_qoq     DECIMAL(8,4),
    total_revenue           BIGINT,
    gross_profit            BIGINT,
    operating_income        BIGINT,
    net_income              BIGINT,
    ebitda                  BIGINT,
    eps_ttm                 DECIMAL(10,4),
    eps_forward             DECIMAL(10,4),
    total_cash              BIGINT,
    total_debt              BIGINT,
    net_cash                BIGINT,
    debt_to_equity          DECIMAL(10,4),
    current_ratio           DECIMAL(8,4),
    quick_ratio             DECIMAL(8,4),
    book_value_per_share    DECIMAL(12,4),
    operating_cash_flow     BIGINT,
    free_cash_flow          BIGINT,
    fcf_per_share           DECIMAL(12,4),
    capex                   BIGINT,
    dividend_yield          DECIMAL(8,4),
    dividend_rate           DECIMAL(10,4),
    payout_ratio            DECIMAL(8,4),
    ex_dividend_date        DATE,
    analyst_target_mean     DECIMAL(12,4),
    analyst_target_high     DECIMAL(12,4),
    analyst_target_low      DECIMAL(12,4),
    analyst_recommendation  VARCHAR,
    number_of_analysts      INTEGER,
    data_source             VARCHAR DEFAULT 'yfinance',
    pulled_at               TIMESTAMP,
    fiscal_year_end         VARCHAR,
    most_recent_quarter     DATE,
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, snapshot_date)
);
"""

# ── Table 6: api_usage ────────────────────────────────────────────────────────

_CREATE_API_USAGE = """
CREATE TABLE IF NOT EXISTS api_usage (
    usage_id                INTEGER PRIMARY KEY DEFAULT nextval('seq_api_usage'),
    run_id                  VARCHAR,
    ticker                  VARCHAR,
    triggered_by            VARCHAR,
    agent_name              VARCHAR NOT NULL,
    agent_id                VARCHAR,
    agent_role              VARCHAR,
    api_provider            VARCHAR NOT NULL,
    api_endpoint            VARCHAR,
    model                   VARCHAR,
    model_tier              VARCHAR,
    input_tokens            BIGINT DEFAULT 0,
    input_cached_tokens     BIGINT DEFAULT 0,
    input_uncached_tokens   BIGINT DEFAULT 0,
    system_prompt_tokens    BIGINT DEFAULT 0,
    user_content_tokens     BIGINT DEFAULT 0,
    output_tokens           BIGINT DEFAULT 0,
    thinking_tokens         BIGINT DEFAULT 0,
    response_tokens         BIGINT DEFAULT 0,
    total_tokens            BIGINT DEFAULT 0,
    input_cost_usd          DECIMAL(10,6) DEFAULT 0,
    output_cost_usd         DECIMAL(10,6) DEFAULT 0,
    thinking_cost_usd       DECIMAL(10,6) DEFAULT 0,
    total_cost_usd          DECIMAL(10,6) DEFAULT 0,
    prompt_cache_status     VARCHAR DEFAULT 'none',
    cache_creation_tokens   BIGINT DEFAULT 0,
    cache_read_tokens       BIGINT DEFAULT 0,
    request_started_at      TIMESTAMP,
    request_completed_at    TIMESTAMP,
    latency_ms              INTEGER,
    time_to_first_token_ms  INTEGER,
    http_status_code        INTEGER,
    request_id              VARCHAR,
    api_version             VARCHAR,
    is_error                BOOLEAN DEFAULT FALSE,
    was_retry               BOOLEAN DEFAULT FALSE,
    retry_count             INTEGER DEFAULT 0,
    error_type              VARCHAR,
    error_message           VARCHAR,
    session_id              VARCHAR,
    environment             VARCHAR DEFAULT 'production',
    app_version             VARCHAR,
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
);
"""

# ── Table 7: agent_logs ───────────────────────────────────────────────────────
# PK changed from (log_date, agent_name) → (log_date, agent_id) in v2.1

_CREATE_AGENT_LOGS = """
CREATE TABLE IF NOT EXISTS agent_logs (
    log_date                DATE NOT NULL,
    agent_id                VARCHAR NOT NULL,
    agent_name              VARCHAR NOT NULL,
    what_i_did              VARCHAR,
    wins                    VARCHAR,
    losses                  VARCHAR,
    struggles               VARCHAR,
    blockers                VARCHAR,
    analyses_completed      INTEGER DEFAULT 0,
    api_calls_made          INTEGER DEFAULT 0,
    total_tokens_used       BIGINT DEFAULT 0,
    total_cost_usd          DECIMAL(10,6) DEFAULT 0,
    errors_encountered      INTEGER DEFAULT 0,
    avg_latency_ms          INTEGER,
    qa_issues_flagged       INTEGER DEFAULT 0,
    hallucinations_caught   INTEGER DEFAULT 0,
    conflicts_resolved      INTEGER DEFAULT 0,
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (log_date, agent_id)
);
"""

# ── Table 8: communication_log ────────────────────────────────────────────────

_CREATE_COMMUNICATION_LOG = """
CREATE TABLE IF NOT EXISTS communication_log (
    log_id                  INTEGER PRIMARY KEY DEFAULT nextval('seq_comm_log'),
    run_id                  VARCHAR NOT NULL,
    ticker                  VARCHAR,
    sender_agent_id         VARCHAR NOT NULL,
    sender_agent_name       VARCHAR NOT NULL,
    receiver_agent_id       VARCHAR NOT NULL,
    receiver_agent_name     VARCHAR NOT NULL,
    message_type            VARCHAR NOT NULL,
    message_content         VARCHAR NOT NULL,
    execution_step          INTEGER,
    confidence_level        DECIMAL(3,2),
    quality_score           DECIMAL(3,2),
    flagged                 BOOLEAN DEFAULT FALSE,
    flag_reason             VARCHAR,
    resolution              VARCHAR,
    resolved_at             TIMESTAMP,
    tokens_used             BIGINT DEFAULT 0,
    cost_usd                DECIMAL(10,6) DEFAULT 0,
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
);
"""

# ── Table 9: escalation_alerts ────────────────────────────────────────────────

_CREATE_ESCALATION_ALERTS = """
CREATE TABLE IF NOT EXISTS escalation_alerts (
    alert_id                INTEGER PRIMARY KEY DEFAULT nextval('seq_escalation'),
    run_id                  VARCHAR NOT NULL,
    ticker                  VARCHAR,
    severity                VARCHAR NOT NULL,
    trigger_reason          VARCHAR NOT NULL,
    agents_affected         VARCHAR NOT NULL,
    data_sources_affected   VARCHAR,
    user_query              VARCHAR NOT NULL,
    manager_assessment      VARCHAR NOT NULL,
    ai_analyst_assessment   VARCHAR,
    resolved                BOOLEAN DEFAULT FALSE,
    resolved_at             TIMESTAMP,
    resolution_notes        VARCHAR,
    email_sent              BOOLEAN DEFAULT FALSE,
    email_sent_at           TIMESTAMP,
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
);
"""

# ── Table 10: agent_registry ──────────────────────────────────────────────────

_CREATE_AGENT_REGISTRY = """
CREATE TABLE IF NOT EXISTS agent_registry (
    agent_id            VARCHAR PRIMARY KEY,
    agent_name          VARCHAR NOT NULL UNIQUE,
    display_name        VARCHAR NOT NULL,
    role_code           VARCHAR NOT NULL,
    model_assignment    VARCHAR,
    model_tier          VARCHAR,
    api_provider        VARCHAR,
    is_active           BOOLEAN DEFAULT TRUE,
    prompt_file         VARCHAR,
    description         VARCHAR,
    created_date        DATE NOT NULL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ── Table 11: learning_log ────────────────────────────────────────────────────

_CREATE_LEARNING_LOG = """
CREATE TABLE IF NOT EXISTS learning_log (
    entry_id                INTEGER PRIMARY KEY DEFAULT nextval('seq_learning_log'),
    run_id                  VARCHAR,
    ticker                  VARCHAR,
    agent_id                VARCHAR NOT NULL,
    agent_name              VARCHAR NOT NULL,
    entry_type              VARCHAR NOT NULL,
    what_happened           VARCHAR NOT NULL,
    what_went_well          VARCHAR,
    what_went_wrong         VARCHAR,
    lesson_learned          VARCHAR,
    summary_covers_runs     VARCHAR,
    compressed_patterns     VARCHAR,
    had_errors              BOOLEAN DEFAULT FALSE,
    had_hallucinations      BOOLEAN DEFAULT FALSE,
    had_conflicts           BOOLEAN DEFAULT FALSE,
    created_date            DATE NOT NULL,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
);
"""

# ── Table 12: weekly_universe ─────────────────────────────────────────────────

_CREATE_WEEKLY_UNIVERSE = """
CREATE TABLE IF NOT EXISTS weekly_universe (
    entry_id            INTEGER PRIMARY KEY DEFAULT nextval('seq_weekly_universe'),
    week_start_date     DATE NOT NULL,
    week_label          VARCHAR NOT NULL,
    ticker              VARCHAR NOT NULL,
    company_name        VARCHAR,
    sector              VARCHAR,
    source              VARCHAR NOT NULL,
    requested_by        VARCHAR,
    selection_reason    VARCHAR,
    status              VARCHAR DEFAULT 'pending',
    run_id              VARCHAR,
    created_date        DATE NOT NULL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(week_start_date, ticker)
);
"""

# ── Table 13: stock_requests ──────────────────────────────────────────────────

_CREATE_STOCK_REQUESTS = """
CREATE TABLE IF NOT EXISTS stock_requests (
    request_id          INTEGER PRIMARY KEY DEFAULT nextval('seq_stock_requests'),
    ticker              VARCHAR NOT NULL,
    company_name        VARCHAR,
    request_source      VARCHAR DEFAULT 'web_form',
    request_reason      VARCHAR,
    status              VARCHAR DEFAULT 'pending',
    selected_for_week   DATE,
    rejection_reason    VARCHAR,
    is_valid_ticker     BOOLEAN,
    validation_message  VARCHAR,
    created_date        DATE NOT NULL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ── Table 14: signal_history ──────────────────────────────────────────────────

_CREATE_SIGNAL_HISTORY = """
CREATE TABLE IF NOT EXISTS signal_history (
    history_id          INTEGER PRIMARY KEY DEFAULT nextval('seq_signal_history'),
    ticker              VARCHAR NOT NULL,
    previous_signal     VARCHAR,
    new_signal          VARCHAR NOT NULL,
    signal_changed      BOOLEAN NOT NULL,
    price_at_signal     DECIMAL(12,4) NOT NULL,
    purchase_target     DECIMAL(12,4),
    conviction_score    INTEGER,
    trigger             VARCHAR,
    run_id              VARCHAR,
    created_date        DATE NOT NULL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
);
"""


# ── View 1: v_cost_daily_summary ──────────────────────────────────────────────

_CREATE_V_COST_DAILY_SUMMARY = """
CREATE OR REPLACE VIEW v_cost_daily_summary AS
SELECT
    created_date                                                                    AS summary_date,
    SUM(total_cost_usd)                                                             AS total_cost_usd,
    COUNT(*)                                                                        AS total_api_calls,
    COUNT(DISTINCT run_id)                                                          AS total_analyses,
    COUNT(CASE WHEN prompt_cache_status = 'hit' THEN 1 END)                        AS total_cache_hits,
    SUM(input_tokens)                                                               AS total_input_tokens,
    SUM(output_tokens)                                                              AS total_output_tokens,
    SUM(thinking_tokens)                                                            AS total_thinking_tokens,
    SUM(input_cached_tokens)                                                        AS total_cached_tokens,
    SUM(CASE WHEN api_provider = 'anthropic' THEN total_cost_usd ELSE 0 END)       AS anthropic_cost,
    SUM(CASE WHEN api_provider = 'google'    THEN total_cost_usd ELSE 0 END)       AS google_cost,
    SUM(CASE WHEN api_provider = 'openai'    THEN total_cost_usd ELSE 0 END)       AS openai_cost,
    SUM(CASE WHEN model_tier = 'premium'     THEN total_cost_usd ELSE 0 END)       AS premium_cost,
    SUM(CASE WHEN model_tier = 'standard'    THEN total_cost_usd ELSE 0 END)       AS standard_cost,
    SUM(CASE WHEN model_tier = 'economy'     THEN total_cost_usd ELSE 0 END)       AS economy_cost,
    SUM(CASE WHEN agent_name = 'manager'             THEN total_cost_usd ELSE 0 END) AS manager_cost,
    SUM(CASE WHEN agent_name = 'fundamental_analyst' THEN total_cost_usd ELSE 0 END) AS fundamental_cost,
    SUM(CASE WHEN agent_name = 'technical_analyst'   THEN total_cost_usd ELSE 0 END) AS technical_cost,
    SUM(CASE WHEN agent_name = 'finance_researcher'  THEN total_cost_usd ELSE 0 END) AS researcher_cost,
    SUM(CASE WHEN agent_name = 'business_analyst'    THEN total_cost_usd ELSE 0 END) AS ba_cost,
    SUM(CASE WHEN agent_name = 'project_coordinator' THEN total_cost_usd ELSE 0 END) AS pc_cost,
    AVG(total_cost_usd)                                                             AS avg_cost_per_call,
    AVG(latency_ms)                                                                 AS avg_latency_ms
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
    COUNT(*)                                        AS call_count,
    SUM(total_cost_usd)                             AS total_cost,
    SUM(input_tokens)                               AS total_input_tokens,
    SUM(output_tokens)                              AS total_output_tokens,
    SUM(thinking_tokens)                            AS total_thinking_tokens,
    SUM(input_cached_tokens)                        AS total_cached_tokens,
    AVG(latency_ms)                                 AS avg_latency_ms,
    SUM(CASE WHEN is_error THEN 1 ELSE 0 END)      AS error_count,
    COUNT(DISTINCT run_id)                          AS analyses_served
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
        SUM(total_cost_usd)             AS monthly_spend,
        COUNT(DISTINCT created_date)    AS days_with_activity,
        COUNT(*)                        AS total_calls,
        COUNT(DISTINCT run_id)          AS total_analyses
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
        100.00                          AS monthly_budget,
        100.00 - monthly_spend          AS budget_remaining,
        CASE WHEN days_with_activity > 0
             THEN monthly_spend / days_with_activity
             ELSE 0
        END                             AS avg_daily_spend,
        EXTRACT(DAY FROM LAST_DAY(CURRENT_DATE))                                AS days_in_month,
        EXTRACT(DAY FROM LAST_DAY(CURRENT_DATE)) - EXTRACT(DAY FROM CURRENT_DATE) AS days_remaining
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
    # communication_log
    "CREATE INDEX IF NOT EXISTS idx_comm_run     ON communication_log(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_comm_flagged ON communication_log(flagged);",
    "CREATE INDEX IF NOT EXISTS idx_comm_sender  ON communication_log(sender_agent_id);",
    "CREATE INDEX IF NOT EXISTS idx_comm_type    ON communication_log(message_type);",
    "CREATE INDEX IF NOT EXISTS idx_comm_date    ON communication_log(created_date);",
    # escalation_alerts
    "CREATE INDEX IF NOT EXISTS idx_esc_unresolved ON escalation_alerts(resolved);",
    "CREATE INDEX IF NOT EXISTS idx_esc_severity   ON escalation_alerts(severity);",
    "CREATE INDEX IF NOT EXISTS idx_esc_date       ON escalation_alerts(created_date);",
    # learning_log
    "CREATE INDEX IF NOT EXISTS idx_learn_agent ON learning_log(agent_id);",
    "CREATE INDEX IF NOT EXISTS idx_learn_type  ON learning_log(entry_type);",
    "CREATE INDEX IF NOT EXISTS idx_learn_date  ON learning_log(created_date);",
    "CREATE INDEX IF NOT EXISTS idx_learn_run   ON learning_log(run_id);",
    # weekly_universe
    "CREATE INDEX IF NOT EXISTS idx_weekly_week   ON weekly_universe(week_start_date);",
    "CREATE INDEX IF NOT EXISTS idx_weekly_status ON weekly_universe(status);",
    "CREATE INDEX IF NOT EXISTS idx_weekly_ticker ON weekly_universe(ticker);",
    # stock_requests
    "CREATE INDEX IF NOT EXISTS idx_req_status ON stock_requests(status);",
    "CREATE INDEX IF NOT EXISTS idx_req_ticker ON stock_requests(ticker);",
    "CREATE INDEX IF NOT EXISTS idx_req_date   ON stock_requests(created_date);",
    # signal_history
    "CREATE INDEX IF NOT EXISTS idx_signal_ticker  ON signal_history(ticker);",
    "CREATE INDEX IF NOT EXISTS idx_signal_date    ON signal_history(created_date);",
    "CREATE INDEX IF NOT EXISTS idx_signal_changed ON signal_history(signal_changed);",
]


# ── Agent registry seed data ──────────────────────────────────────────────────
# V1 agent roster. ON CONFLICT DO NOTHING — safe to re-run.

_AGENT_SEED = [
    # agent_id, agent_name, display_name, role_code, model, tier, provider, prompt_file
    ("MGR-01",  "manager",              "The Manager",          "MGR",
     "claude-opus-4-6",    "premium",  "anthropic", "manager.md"),
    ("FA-01",   "fundamental_analyst",  "Fundamental Analyst",  "FA",
     "claude-opus-4-6",    "premium",  "anthropic", "fundamental_analyst.md"),
    ("TA-01",   "technical_analyst",    "Technical Analyst",    "TA",
     "claude-sonnet-4-6",  "standard", "anthropic", "technical_analyst.md"),
    ("FR-01",   "finance_researcher",   "Finance Researcher",   "FR",
     "claude-sonnet-4-6",  "standard", "anthropic", "finance_researcher.md"),
    ("BA-01",   "business_analyst",     "Business Analyst",     "BA",
     "claude-sonnet-4-6",  "standard", "anthropic", "business_analyst.md"),
    ("DE-01",   "data_engineer",        "Data Engineer",        "DE",
     "yfinance",           "free",     "system",    None),
    ("QA-01",   "qa_tester",            "QA Tester",            "QA",
     "python",             "free",     "system",    "qa_tester.md"),
    ("PM-01",   "project_coordinator",  "Project Coordinator",  "PC",
     "claude-sonnet-4-6",  "standard", "anthropic", "project_coordinator.md"),
    ("AIA-01",  "ai_analyst",           "AI Analyst + Auditor", "AIA",
     "python",             "free",     "system",    None),
]

_TABLES_EXPECTED = [
    "analysis_runs", "analysis_results", "analysis_cache",
    "stock_prices", "stock_fundamentals", "api_usage", "agent_logs",
    "communication_log", "escalation_alerts", "agent_registry", "learning_log",
    "weekly_universe", "stock_requests", "signal_history",
]

_VIEWS_EXPECTED = [
    "v_cost_daily_summary", "v_cost_by_agent", "v_budget_tracker",
]


# ── Connection helper ─────────────────────────────────────────────────────────

def get_connection() -> duckdb.DuckDBPyConnection:
    """Return a new connection to the project DuckDB file."""
    db_path = Path(settings.DUCKDB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


# ── Schema verification ───────────────────────────────────────────────────────

def verify_schema() -> bool:
    """
    Check all 14 tables and 3 views exist. Print a status report.

    Returns True if everything is present, False if anything is missing.
    """
    conn = get_connection()
    try:
        existing_tables = {
            row[0] for row in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
            ).fetchall()
        }
        existing_views = {
            row[0] for row in conn.execute(
                "SELECT table_name FROM information_schema.views "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }

        print("\n── Schema Verification ──────────────────────────────────")
        all_good = True

        for t in _TABLES_EXPECTED:
            status = "✓" if t in existing_tables else "✗ MISSING"
            if t not in existing_tables:
                all_good = False
            print(f"  {'TABLE':<6} {t:<30} {status}")

        for v in _VIEWS_EXPECTED:
            status = "✓" if v in existing_views else "✗ MISSING"
            if v not in existing_views:
                all_good = False
            print(f"  {'VIEW':<6} {v:<30} {status}")

        # Agent registry count
        agent_count = conn.execute("SELECT COUNT(*) FROM agent_registry").fetchone()[0]
        print(f"\n  agent_registry: {agent_count}/9 agents seeded")

        print(f"\n  Result: {'ALL GOOD ✓' if all_good else 'ISSUES FOUND ✗'}")
        print("─" * 55)
        return all_good

    finally:
        conn.close()


# ── Agent seeding ─────────────────────────────────────────────────────────────

def _seed_agents(conn: duckdb.DuckDBPyConnection) -> None:
    """Insert 9 V1 agents into agent_registry. ON CONFLICT DO NOTHING — idempotent."""
    today = date.today()
    now = datetime.now(timezone.utc)

    for (agent_id, agent_name, display_name, role_code,
         model, tier, provider, prompt_file) in _AGENT_SEED:
        conn.execute(
            """
            INSERT INTO agent_registry (
                agent_id, agent_name, display_name, role_code,
                model_assignment, model_tier, api_provider,
                is_active, prompt_file,
                created_date, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?, ?, ?)
            ON CONFLICT (agent_id) DO NOTHING
            """,
            [agent_id, agent_name, display_name, role_code,
             model, tier, provider, prompt_file,
             today, now, now],
        )


# ── Init ──────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """
    Single entry point. Creates all 14 tables, 3 views, indexes, seeds agents.

    Order: sequences → tables → views → indexes → seed agents → verify.
    Idempotent — safe to call on every app startup.
    """
    db_path = Path(settings.DUCKDB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection()
    try:
        # 1. Sequences
        for sql in _SEQUENCES:
            conn.execute(sql)

        # 2. Tables (order matters — analysis_runs must exist before FK tables)
        for ddl in [
            _CREATE_ANALYSIS_RUNS,
            _CREATE_ANALYSIS_RESULTS,
            _CREATE_ANALYSIS_CACHE,
            _CREATE_STOCK_PRICES,
            _CREATE_STOCK_FUNDAMENTALS,
            _CREATE_API_USAGE,
            _CREATE_AGENT_LOGS,
            _CREATE_COMMUNICATION_LOG,
            _CREATE_ESCALATION_ALERTS,
            _CREATE_AGENT_REGISTRY,
            _CREATE_LEARNING_LOG,
            _CREATE_WEEKLY_UNIVERSE,
            _CREATE_STOCK_REQUESTS,
            _CREATE_SIGNAL_HISTORY,
        ]:
            conn.execute(ddl)

        # 3. Views
        for ddl in [
            _CREATE_V_COST_DAILY_SUMMARY,
            _CREATE_V_COST_BY_AGENT,
            _CREATE_V_BUDGET_TRACKER,
        ]:
            conn.execute(ddl)

        # 4. Indexes
        for sql in _INDEXES:
            conn.execute(sql)

        # 5. Seed agents
        _seed_agents(conn)

        conn.commit()
        logger.info("Database v2.1 initialised: %s", db_path.resolve())

    except Exception as exc:
        logger.exception("Database init failed: %s", exc)
        raise
    finally:
        conn.close()

    # 6. Verify (uses its own connection)
    verify_schema()


def setup_database() -> None:
    """Backward-compatible alias for init_db(). Called by app/main.py."""
    init_db()


# ── Health check ──────────────────────────────────────────────────────────────

def get_table_row_counts() -> dict[str, int]:
    """Return row counts for all 14 tables. Used for health checks."""
    conn = get_connection()
    try:
        return {
            t: (conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone() or [0])[0]
            for t in _TABLES_EXPECTED
        }
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
    init_db()
    counts = get_table_row_counts()
    print("\nRow counts:")
    for name, count in counts.items():
        print(f"  {name:<30} {count:>6} rows")
