"""
config/settings.py
Load all environment variables from .env into typed constants.

Every API key and secret MUST come from the environment — nothing hardcoded.
Copy .env.example → .env and fill in real values before running.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (one level above this file)
load_dotenv(Path(__file__).parent.parent / ".env")


# ── AI Provider ───────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")  # Required for Phase 4 agents; optional for Phase 3

# Sprint 2 optional: OpenAI for Finance Researcher / Technical Analyst
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")


# ── Model Assignments (Sprint 1 — all Claude) ─────────────────────────────────
# Locked in BRD v1.0. DO NOT change without updating the decision log.

MANAGER_MODEL: str = os.getenv("MANAGER_MODEL", "claude-opus-4-6")
BUSINESS_ANALYST_MODEL: str = os.getenv("BUSINESS_ANALYST_MODEL", "claude-sonnet-4-6")
DATA_ENGINEER_MODEL: str = os.getenv("DATA_ENGINEER_MODEL", "claude-haiku-4-5-20251001")
FINANCE_RESEARCHER_MODEL: str = os.getenv("FINANCE_RESEARCHER_MODEL", "claude-sonnet-4-6")
TECHNICAL_ANALYST_MODEL: str = os.getenv("TECHNICAL_ANALYST_MODEL", "claude-haiku-4-5-20251001")
FUNDAMENTAL_ANALYST_MODEL: str = os.getenv("FUNDAMENTAL_ANALYST_MODEL", "claude-opus-4-6")
QA_TESTER_MODEL: str = os.getenv("QA_TESTER_MODEL", "claude-haiku-4-5-20251001")
PROJECT_MANAGER_MODEL: str = os.getenv("PROJECT_MANAGER_MODEL", "claude-haiku-4-5-20251001")
AI_ANALYST_MODEL: str = os.getenv("AI_ANALYST_MODEL", "claude-sonnet-4-6")


# ── Database ──────────────────────────────────────────────────────────────────

DUCKDB_PATH: str = os.getenv("DUCKDB_PATH", "data/db/project01.duckdb")


# ── Budget Controls ───────────────────────────────────────────────────────────
# Hard limits. Exceeding these stops the pipeline before it incurs more cost.

MAX_COST_PER_REPORT_USD: float = float(os.getenv("MAX_COST_PER_REPORT_USD", "0.50"))
DAILY_BUDGET_LIMIT_USD: float = float(os.getenv("DAILY_BUDGET_LIMIT_USD", "10.00"))
MAX_TOKENS_PER_AGENT_CALL: int = int(os.getenv("MAX_TOKENS_PER_AGENT_CALL", "4096"))
MAX_TOKENS_PER_PIPELINE_RUN: int = int(os.getenv("MAX_TOKENS_PER_PIPELINE_RUN", "100000"))


# ── Prompt Caching ────────────────────────────────────────────────────────────
# Mandatory from day one — cuts 90% of input costs on repeated agent calls.

ENABLE_PROMPT_CACHING: bool = os.getenv("ENABLE_PROMPT_CACHING", "true").lower() == "true"
PROMPT_CACHE_MIN_CHARS: int = int(os.getenv("PROMPT_CACHE_MIN_CHARS", "1024"))


# ── Prompts ───────────────────────────────────────────────────────────────────
# PROMPT_DIR points to the private repo's agents/prompts/ directory locally.
# On Railway, inject each prompt as an env var instead.
# This public repo contains NO actual agent prompts.

PROMPT_DIR: str = os.getenv("PROMPT_DIR", "")


# ── Data Refresh ──────────────────────────────────────────────────────────────

DATA_REFRESH_CRON: str = os.getenv("DATA_REFRESH_CRON", "30 11 * * 1-5")
HISTORY_DAYS: int = int(os.getenv("HISTORY_DAYS", "730"))
REPORT_CACHE_TTL_MINUTES: int = int(os.getenv("REPORT_CACHE_TTL_MINUTES", "60"))
FORCE_DATA_REFRESH: bool = os.getenv("FORCE_DATA_REFRESH", "false").lower() == "true"


# ── App Settings ──────────────────────────────────────────────────────────────

PORT: int = int(os.getenv("PORT", "8501"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
APP_ENV: str = os.getenv("APP_ENV", "development")
