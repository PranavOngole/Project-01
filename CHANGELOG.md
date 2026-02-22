# CHANGELOG — Project-01: AI-Powered Stock Research Platform

All notable changes to this project are documented here in reverse chronological order.

Format per entry:
- **What** changed and in which file(s)
- **Why** the change was made
- **Impact** on the system

---

## [0.3.4] — 2026-02-22
**Commit (public):** `c144876`
**Commit (private):** `5547cc0`
**Phase:** 3D — Deployment & Automation

### Added (public repo — `Project-01`)

- **`railway.toml`** — Railway deployment configuration
  - Builder: `nixpacks` (auto-detects Python, installs `requirements.txt`)
  - Start command: `streamlit run app/main.py --server.port $PORT --server.address 0.0.0.0 --server.headless true`
  - `$PORT` injected at runtime by Railway — not hardcoded
  - Restart policy: `on_failure`, max 3 retries before Railway alerts

- **`runtime.txt`** — Pins Python 3.11 for nixpacks
  - Without this, nixpacks may select a different Python version
  - One line: `python-3.11`

### Added (private repo — `Project-01-Private`)

- **`n8n/stock_data_refresh.json`** — Import-ready n8n workflow
  - **Two schedule triggers:** 9:15 AM ET + 4:30 PM ET, weekdays only
  - **Timezone:** `America/New_York` — DST handled automatically, no manual UTC conversion needed
  - **HTTP Request node:** GET ping to Railway app URL (stored in n8n variable `RAILWAY_APP_URL`)
  - **Health check branch:** If node splits on HTTP 200 → Log Success, else → Log Failure
  - Both triggers feed into the same HTTP Request node (single pipeline path)
  - `active: false` by default — user activates manually after importing
  - Phase 5 upgrade path noted: replace GET ping with POST to `/api/refresh` once that endpoint exists

- **`docs/RAILWAY_SETUP.md`** — Step-by-step Railway + n8n setup guide
  - Railway account creation and repo connection steps
  - Complete env var table (Phase 3 skeleton vars + Phase 4 prompt vars)
  - `DUCKDB_PATH` set to `/tmp/project01.duckdb` for Railway (ephemeral filesystem)
  - n8n install (`brew install n8n`), workflow import, variable config, activation
  - `brew services start n8n` for auto-start on Mac boot
  - GitHub Projects `gh` CLI commands for Sprint 1 board creation (all 13 Phase 3-4 tasks)
  - Cost projection table: ~$65–80/month total within $100 ceiling

### Pending (manual steps — requires your action)

| Step | Where | What to do |
|---|---|---|
| Railway account | [railway.app](https://railway.app) | Sign up with GitHub, connect `Project-01` repo |
| Railway env vars | Railway dashboard → Variables | Copy values from `RAILWAY_SETUP.md` |
| n8n install | Terminal | `brew install n8n` then `n8n start` |
| n8n workflow | `localhost:5678` | Import `n8n/stock_data_refresh.json`, set `RAILWAY_APP_URL`, toggle Active |
| GitHub Projects | Terminal | `gh auth login` then run commands from `RAILWAY_SETUP.md` Step 6 |

---

## [0.3.3] — 2026-02-21
**Commit:** `481a94b`
**Phase:** 3C — Agent Base Class

### Changed
- **`agents/base_agent.py`** — Complete rewrite for v2 schema compatibility

#### New helpers
| Helper | Purpose |
|---|---|
| `_model_tier(model)` | Maps model name → `"premium"` / `"standard"` / `"economy"` |
| `_AGENT_ROLES` dict | Maps agent name → role string (e.g. `"manager"` → `"orchestrator"`) |
| `_cache_status(read, write)` | Derives `prompt_cache_status` string from token counts |
| `_estimate_thinking_tokens(content)` | Estimates thinking tokens from response content blocks (~4 chars/token) since the API does not report them separately |

#### `calculate_cost()` — Return type changed
- **Before:** returned a single `float` (total cost only)
- **After:** returns `dict[str, float]` with keys `input_cost_usd`, `output_cost_usd`, `thinking_cost_usd`, `total_cost_usd`
- Added correct Anthropic prompt caching billing: cache writes at 1.25× input rate, cache reads at 0.10× input rate
- Thinking tokens now tracked and billed separately at output rate

#### `call_api()` — New parameters and richer return
- Added `run_id: str | None` — links every API call back to its `analysis_runs` row
- Added `triggered_by: str` — `"user_analysis"` / `"scheduled_refresh"` / `"deep_dive"`
- Now captures `request_started_at` and `request_completed_at` in UTC
- Separates `thinking_tokens` from `response_tokens` in the usage breakdown
- Return dict expanded to: `content`, `usage` (7 fields), `cost` (4 fields), `duration_ms`, `request_id`

#### `_log_api_usage()` — Rewritten to match v2 schema
- **Before:** inserted ~10 wrong column names (`analysis_date`, `cost_usd`, `call_duration_ms`) — none exist in v2
- **After:** correctly writes all 31 v2 `api_usage` columns including `model_tier`, `agent_role`, `api_provider`, `input_cached_tokens`, `input_uncached_tokens`, `prompt_cache_status`, `cache_creation_tokens`, `request_started_at`, `request_completed_at`

#### `log_activity()` — UPSERT + auto-aggregation
- **Before:** plain `INSERT` — would throw a PK violation on the second call for the same agent on the same day
- **After:** `ON CONFLICT (log_date, agent_name) DO UPDATE SET ...` (composite PK safe)
- Daily metrics (`api_calls_made`, `total_tokens_used`, `total_cost_usd`, `errors_encountered`, `avg_latency_ms`) now auto-aggregated live from `api_usage` instead of being manually passed in

#### `run()` abstract method
- Standardised return contract documented: every agent must return `status`, `cost_usd`, `output`

---

## [0.3.2] — 2026-02-21
**Commit:** `a46acd5`
**Phase:** Branding update

### Changed
- **`README.md`** — Replaced "DataForge365" with "Pranav Ongole's Vision (POV)" in 3 locations:
  1. Series badge (shields.io URL text)
  2. Section header: `## Part of Pranav Ongole's Vision (POV) Series`
  3. Project table row: `POV Series — Kickoff & Manifesto`

---

## [0.3.1] — 2026-02-21
**Commit:** `f002519`
**Phase:** 3A (Data Layer) + 3B (Application Skeleton) — full rebuild

### Changed
- **`data/schema.py`** — Complete rewrite from v1 (5 tables, DOUBLE types) to v2 spec

  **v2 schema additions:**
  - 7 tables total: `analysis_runs`, `stock_prices`, `fundamentals`, `api_usage`, `agent_logs`, `analysis_results`, `ticker_universe`
  - DECIMAL types throughout: `DECIMAL(10,6)` for money, `DECIMAL(12,4)` for prices, `DECIMAL(8,4)` for percentages, `BIGINT` for token counts
  - 2 integer sequences: `seq_api_usage`, `seq_analysis_results` (auto-increment PKs for high-volume tables)
  - Composite natural PKs on `stock_prices` (`ticker`, `price_date`) and `fundamentals` (`ticker`, `period_end_date`, `period_type`)
  - Composite PK on `agent_logs` (`log_date`, `agent_name`) — enforces one row per agent per day
  - 3 views: `v_cost_daily_summary`, `v_cost_by_agent`, `v_budget_tracker` (all derived from `api_usage`)
  - 16 indexes for query performance

- **`data/pipeline.py`** — New file. Full yfinance data pull + validation + DuckDB storage

  **Validation sequence:**
  1. Ticker format (letters only, 1–5 chars)
  2. Quote type (equity only, excludes ETFs/mutual funds)
  3. Exchange (NYSE/NASDAQ only: NYQ, NMS, NGM, NCM, NYSE, NASDAQ codes)
  4. Market cap ≥ $500M
  5. Price history ≥ 480 trading days (≈2 years)

  **Data stored:**
  - OHLCV via pandas DataFrame + DuckDB bulk upsert with rolling 52wk high/low and 10/30-day volume averages
  - Fundamentals via dynamic dict-based INSERT with `ON CONFLICT DO UPDATE`
  - Every pull logged to `api_usage` (agent_name=`"data_engineer"`, api_provider=`"yfinance"`, cost=$0)

  **Return types:**
  - `StockCard` dataclass — all data for the UI card
  - `PipelineResult` dataclass — wraps success/failure with `error_type` for UI routing (`"format"` / `"not_found"` / `"exchange"` / `"market_cap"` / `"history"` / `"data_error"` / `"db_error"`)

- **`app/main.py`** — Complete rewrite from skeleton to functional data display

  **Key patterns:**
  - `st.form("search_form")` — prevents Streamlit reruns on every keystroke
  - `@st.cache_resource` for DB init — runs once per process
  - Session state: `result` (PipelineResult | None), `last_ticker` (str)
  - SEC disclaimer always visible as collapsible expander above search bar
  - Data timestamp shows EST + "15-20 min delay · Not real-time" badge
  - Phase 4 teaser section rendered below stock card
  - Error card routing varies by `error_type`

### Added
- **`config/settings.py`** — Fix: `ANTHROPIC_API_KEY` changed from `os.environ["ANTHROPIC_API_KEY"]` (crashes without .env) to `os.getenv("ANTHROPIC_API_KEY", "")` so Phase 3 app starts without a real API key

---

## [0.2.0] — 2026-02-21
**Commit:** `ba799ec`
**Phase:** 3 initial scaffold

### Added
- **`data/schema.py`** — v1 schema (5 tables, DOUBLE types) — superseded by [0.3.1]
- **`app/main.py`** — Skeleton Streamlit app — superseded by [0.3.1]
- **`agents/base_agent.py`** — Initial base class — superseded by [0.3.3]
- **`config/settings.py`** — All env var loading (ANTHROPIC_API_KEY, model names, DuckDB path, PROMPT_DIR, budget caps, APP_ENV)
- **`config/ticker_universe.py`** — NYSE/NASDAQ validation helpers, market cap filter ($500M), history length check (480 days), exclusion lists (ETFs, ADRs, SPACs, preferred shares)
- **`config/prompts.py`** — Prompt loader: `load_prompt(key)` reads `<PROMPT_DIR>/<key>.md`; `load_prompt_from_env(env_key, fallback_key)` for Railway pattern; `list_available_prompts()` lists .md files
- **`requirements.txt`** — `anthropic>=0.39.0`, `streamlit>=1.32.0`, `plotly>=5.20.0`, `duckdb>=1.0.0`, `yfinance>=0.2.40`, `pandas>=2.2.0`, `python-dotenv>=1.0.0`, `tenacity>=8.2.0`
- **`agents/__init__.py`**, **`config/__init__.py`**, **`data/__init__.py`**, **`app/__init__.py`** — Package init files
- **`.streamlit/config.toml`** — Theme config (primary colour, font, layout)
- **`data/db/.gitkeep`** — Placeholder so DuckDB directory is tracked without storing the DB file

---

## [0.1.0] — 2026-02-21
**Commit:** `c814044`

### Added
- **`README.md`** — Full project README: capabilities table, architecture diagram, agent roster, tech stack, project structure, stock universe criteria, POV series table, SEC disclaimer
- **`.gitignore`** — Project-specific ignores: `.env`, `data/db/*.duckdb`, `__pycache__`, `.DS_Store`, virtual envs
- **`.env.example`** — Template of all required environment variables with descriptions and safe defaults

---

## [0.0.1] — 2026-02-21
**Commit:** `d48237a`

### Added
- Initial commit — empty repository scaffold
