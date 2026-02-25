"""
agents/base_agent.py
Foundation for all Project-01 research agents.

Every agent (Manager, Business Analyst, Fundamental Analyst, etc.) inherits
from BaseAgent and gets for free:
  - Prompt loading from PROMPT_DIR (no prompts stored in this repo)
  - Anthropic API call wrapper with model selection + extended thinking support
  - Full token tracking: input, output, thinking, cached — logged to api_usage
  - Split cost calculation: input / output / thinking / total (DECIMAL precision)
  - Retry logic on rate limits and timeouts (tenacity, exponential backoff)
  - Daily agent log UPSERT to agent_logs (composite PK: log_date + agent_name)

Schema target: DATABASE_SCHEMA_v2.md
  - api_usage: full column set (run_id, triggered_by, model_tier, agent_role,
               request timestamps, split costs, prompt_cache_status, etc.)
  - agent_logs: composite PK (log_date, agent_name) — one row per agent per day,
                daily metrics auto-aggregated from api_usage

Usage:
    class MyAgent(BaseAgent):
        def __init__(self):
            super().__init__(agent_name="my_agent", model=settings.SONNET_MODEL)

        def run(self, ticker: str, context: dict) -> dict:
            system = self.load_prompt("my_agent_system")
            result = self.call_api(
                messages=[{"role": "user", "content": f"Analyze {ticker}"}],
                system=system,
                ticker=ticker,
                run_id=context.get("run_id"),
            )
            self.log_activity(wins="Analysis completed cleanly.")
            return {"status": "success", "cost_usd": result["cost"]["total_cost_usd"]}
"""

from __future__ import annotations

import logging
import sys
import time
from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from config import settings
from config.prompts import load_prompt, load_prompt_from_env
from data.schema import get_connection

logger = logging.getLogger(__name__)


# ── Pricing (USD per token) ───────────────────────────────────────────────────
# Source: BRD v1.0 API Cost Reference.
# Cache write = 1.25× input rate. Cache read = 0.10× input rate.
# Thinking tokens billed at the same rate as output tokens.

PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-6": {
        "input":        5.00 / 1_000_000,
        "output":      25.00 / 1_000_000,   # includes thinking
        "cache_write":  6.25 / 1_000_000,
        "cache_read":   0.50 / 1_000_000,
    },
    "claude-sonnet-4-6": {
        "input":        3.00 / 1_000_000,
        "output":      15.00 / 1_000_000,
        "cache_write":  3.75 / 1_000_000,
        "cache_read":   0.30 / 1_000_000,
    },
    "claude-haiku-4-5-20251001": {
        "input":        0.80 / 1_000_000,
        "output":       4.00 / 1_000_000,
        "cache_write":  1.00 / 1_000_000,
        "cache_read":   0.08 / 1_000_000,
    },
}

_FALLBACK_PRICING = PRICING["claude-sonnet-4-6"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _model_tier(model: str) -> str:
    if "opus" in model:
        return "premium"
    if "sonnet" in model:
        return "standard"
    if "haiku" in model:
        return "economy"
    return "standard"


_AGENT_ROLES: dict[str, str] = {
    "manager":              "orchestrator",
    "fundamental_analyst":  "analyst",
    "business_analyst":     "analyst",
    "finance_researcher":   "analyst",
    "technical_analyst":    "analyst",
    "qa_tester":            "validator",
    "project_coordinator":  "tracker",
    "data_engineer":        "data_pull",
    "ai_analyst":           "monitor",
}


def _cache_status(cache_read: int, cache_write: int) -> str:
    """Derive prompt_cache_status from token counts."""
    if cache_read > 0 and cache_write == 0:
        return "hit"
    if cache_write > 0 and cache_read == 0:
        return "miss"
    if cache_read > 0 and cache_write > 0:
        return "partial"
    return "none"


def _estimate_thinking_tokens(content: list) -> int:
    """
    Estimate thinking token count from response content blocks.

    The Anthropic API includes thinking tokens in output_tokens but does not
    report them separately. We estimate from the character length of thinking
    blocks (~4 chars per token is a rough but consistent heuristic).
    """
    total_chars = sum(
        len(getattr(block, "thinking", "") or "")
        for block in content
        if getattr(block, "type", "") == "thinking"
    )
    return max(0, total_chars // 4)


# ── Base agent ────────────────────────────────────────────────────────────────

class BaseAgent(ABC):
    """
    Abstract base class for all Project-01 research agents.

    Subclass and implement run(). API calls, token tracking, cost calculation,
    retry logic, and DB logging are all handled here.
    """

    def __init__(self, agent_name: str, model: str | None = None, agent_id: str | None = None) -> None:
        """
        Args:
            agent_name: Snake_case name matching agent_logs.agent_name
                        (e.g. 'fundamental_analyst', 'manager').
            model:      Anthropic model ID. Defaults to MANAGER_MODEL from settings.
                        Each concrete agent should pass its own model from settings.
            agent_id:   Registry ID (e.g. 'MGR-01', 'FA-01'). Used as the PK
                        in agent_logs and linked in api_usage rows.
        """
        self.agent_name = agent_name
        self.agent_id = agent_id
        self.model = model or settings.MANAGER_MODEL
        self.agent_role = _AGENT_ROLES.get(agent_name, "analyst")
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        logger.debug("Agent '%s' initialised → model=%s tier=%s",
                     agent_name, self.model, _model_tier(self.model))

    # ── Prompt loading ────────────────────────────────────────────────────────

    def load_prompt(self, prompt_key: str) -> str:
        """
        Load a prompt .md file from PROMPT_DIR by key.

        Prompts are stored in the private repo. Set PROMPT_DIR in .env to point
        at Project-01-Private/agents/prompts/. No prompts live in this repo.

        Args:
            prompt_key: File name without .md extension (e.g. 'manager_system').
        """
        return load_prompt(prompt_key)

    def load_prompt_env(self, env_key: str, fallback_key: str | None = None) -> str:
        """
        Load a prompt from an environment variable (Railway deployment pattern).

        Args:
            env_key:      Env var name (e.g. 'PROMPT_MANAGER_SYSTEM').
            fallback_key: Prompt key to try via PROMPT_DIR if env var is absent.
        """
        return load_prompt_from_env(env_key, fallback_key)

    # ── API call wrapper ──────────────────────────────────────────────────────

    def call_api(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        ticker: str | None = None,
        run_id: str | None = None,
        triggered_by: str = "user_analysis",
        enable_thinking: bool = False,
        thinking_budget_tokens: int = 8_000,
    ) -> dict[str, Any]:
        """
        Call the Anthropic Messages API with full tracking and retry.

        Args:
            messages:               Conversation in Anthropic message format.
            system:                 System prompt string, or list of content blocks
                                    with cache_control for prompt caching.
            max_tokens:             Output token cap. Defaults to MAX_TOKENS_PER_AGENT_CALL.
            ticker:                 Stock ticker — associates this call in api_usage.
            run_id:                 Analysis run ID from analysis_runs — links cost
                                    back to the originating analysis session.
            triggered_by:           'user_analysis' | 'scheduled_refresh' | 'deep_dive'
            enable_thinking:        Enable extended thinking. Opus 4.6 only.
            thinking_budget_tokens: Token budget for the thinking block.

        Returns:
            dict with keys:
                content     — list of Anthropic content blocks (text + thinking)
                usage       — raw token counts from API
                cost        — breakdown: input_cost_usd, output_cost_usd,
                              thinking_cost_usd, total_cost_usd
                duration_ms — wall-clock time in milliseconds
                request_id  — Anthropic request ID (useful for debugging)
        """
        effective_max = max_tokens or settings.MAX_TOKENS_PER_AGENT_CALL

        kwargs: dict[str, Any] = {
            "model":      self.model,
            "max_tokens": effective_max,
            "messages":   messages,
        }
        if system is not None:
            kwargs["system"] = system
        if enable_thinking:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget_tokens,
            }

        # ── Call + timing ──────────────────────────────────────────────────────
        request_started_at = datetime.now(timezone.utc)
        t0 = time.monotonic()
        response = self._call_with_retry(**kwargs)
        duration_ms = int((time.monotonic() - t0) * 1000)
        request_completed_at = datetime.now(timezone.utc)

        # ── Token extraction ───────────────────────────────────────────────────
        usage = response.usage
        input_tokens  = usage.input_tokens
        output_tokens = usage.output_tokens
        cache_read    = getattr(usage, "cache_read_input_tokens",   0) or 0
        cache_write   = getattr(usage, "cache_creation_input_tokens", 0) or 0

        thinking_tokens = (
            _estimate_thinking_tokens(response.content) if enable_thinking else 0
        )
        response_tokens = max(0, output_tokens - thinking_tokens)
        total_tokens    = input_tokens + output_tokens

        # ── Cost breakdown ─────────────────────────────────────────────────────
        cost = self.calculate_cost(
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )

        logger.info(
            "[%s] %s | ticker=%s in=%d out=%d think=%d "
            "cache_r=%d cache_w=%d cost=$%.4f dur=%dms",
            self.agent_name, self.model, ticker or "—",
            input_tokens, output_tokens, thinking_tokens,
            cache_read, cache_write, cost["total_cost_usd"], duration_ms,
        )

        self._log_api_usage(
            run_id=run_id,
            ticker=ticker,
            triggered_by=triggered_by,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens,
            response_tokens=response_tokens,
            total_tokens=total_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost=cost,
            request_started_at=request_started_at,
            request_completed_at=request_completed_at,
            latency_ms=duration_ms,
            request_id=getattr(response, "id", None),
        )

        return {
            "content": response.content,
            "usage": {
                "input_tokens":    input_tokens,
                "output_tokens":   output_tokens,
                "thinking_tokens": thinking_tokens,
                "response_tokens": response_tokens,
                "total_tokens":    total_tokens,
                "cache_read_tokens":  cache_read,
                "cache_write_tokens": cache_write,
            },
            "cost":        cost,
            "duration_ms": duration_ms,
            "request_id":  getattr(response, "id", None),
        }

    def _call_with_retry(self, **kwargs: Any) -> anthropic.types.Message:
        """
        Internal: Anthropic API call with tenacity retry.
        Retries 3× on rate limits and transient errors, exponential backoff 2→30s.
        """
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type((
                anthropic.RateLimitError,
                anthropic.APITimeoutError,
                anthropic.InternalServerError,
            )),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        def _make_call() -> anthropic.types.Message:
            return self._client.messages.create(**kwargs)

        return _make_call()

    # ── Cost calculation ──────────────────────────────────────────────────────

    @staticmethod
    def calculate_cost(
        model: str,
        input_tokens: int,
        output_tokens: int,
        thinking_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> dict[str, float]:
        """
        Calculate USD cost breakdown for one API call.

        Billing logic:
          - Uncached input tokens  → input rate
          - Cache write tokens     → 1.25× input rate
          - Cache read tokens      → 0.10× input rate
          - Response tokens        → output rate
          - Thinking tokens        → output rate (same billing tier as output)

        Returns:
            dict with input_cost_usd, output_cost_usd, thinking_cost_usd, total_cost_usd
            All values rounded to 6 decimal places.
        """
        rates = PRICING.get(model, _FALLBACK_PRICING)

        uncached_input  = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
        regular_output  = max(0, output_tokens - thinking_tokens)

        input_cost    = (uncached_input      * rates["input"]
                         + cache_read_tokens  * rates["cache_read"]
                         + cache_write_tokens * rates["cache_write"])
        output_cost   = regular_output  * rates["output"]
        thinking_cost = thinking_tokens * rates["output"]  # same rate as output
        total_cost    = input_cost + output_cost + thinking_cost

        return {
            "input_cost_usd":    round(input_cost,    6),
            "output_cost_usd":   round(output_cost,   6),
            "thinking_cost_usd": round(thinking_cost, 6),
            "total_cost_usd":    round(total_cost,    6),
        }

    # ── DuckDB: api_usage ─────────────────────────────────────────────────────

    def _log_api_usage(
        self,
        run_id: str | None,
        ticker: str | None,
        triggered_by: str,
        input_tokens: int,
        output_tokens: int,
        thinking_tokens: int,
        response_tokens: int,
        total_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        cost: dict[str, float],
        request_started_at: datetime,
        request_completed_at: datetime,
        latency_ms: int,
        request_id: str | None,
    ) -> None:
        """
        Write one row to api_usage matching the v2 schema exactly.
        Silently swallows DB errors — a logging failure must never crash the pipeline.
        """
        input_cached_tokens   = cache_read_tokens
        input_uncached_tokens = max(0, input_tokens - cache_read_tokens - cache_write_tokens)

        try:
            conn = get_connection()
            conn.execute(
                """
                INSERT INTO api_usage (
                    run_id, ticker, triggered_by,
                    agent_name, agent_id, agent_role,
                    api_provider, api_endpoint, model, model_tier,
                    input_tokens, input_cached_tokens, input_uncached_tokens,
                    output_tokens, thinking_tokens, response_tokens, total_tokens,
                    input_cost_usd, output_cost_usd, thinking_cost_usd, total_cost_usd,
                    prompt_cache_status, cache_creation_tokens, cache_read_tokens,
                    request_started_at, request_completed_at, latency_ms,
                    request_id, is_error, environment,
                    created_date, created_at
                ) VALUES (
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?
                )
                """,
                [
                    run_id, ticker, triggered_by,
                    self.agent_name, self.agent_id, self.agent_role,
                    "anthropic", "/v1/messages", self.model, _model_tier(self.model),
                    input_tokens, input_cached_tokens, input_uncached_tokens,
                    output_tokens, thinking_tokens, response_tokens, total_tokens,
                    cost["input_cost_usd"], cost["output_cost_usd"],
                    cost["thinking_cost_usd"], cost["total_cost_usd"],
                    _cache_status(cache_read_tokens, cache_write_tokens),
                    cache_write_tokens, cache_read_tokens,
                    request_started_at, request_completed_at, latency_ms,
                    request_id, False, settings.APP_ENV,
                    date.today(), datetime.now(timezone.utc),
                ],
            )
            conn.commit()
        except Exception as exc:
            logger.error("Failed to write api_usage: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ── DuckDB: agent_logs ────────────────────────────────────────────────────

    def log_activity(
        self,
        what_i_did: str | None = None,
        wins: str | None = None,
        losses: str | None = None,
        struggles: str | None = None,
        blockers: str | None = None,
    ) -> None:
        """
        UPSERT a daily activity record into agent_logs.

        Call at the end of each agent run. One row per agent per day (composite
        PK: log_date + agent_name). Daily metrics auto-aggregated from api_usage.

        Args:
            what_i_did: Brief description of what the agent did this session.
            wins:       What worked well.
            losses:     What produced low-quality or unexpected output.
            struggles:  Difficult parts that slowed things down.
            blockers:   Anything that prevented full completion.
        """
        today = date.today()
        now   = datetime.now(timezone.utc)

        conn = get_connection()
        try:
            # Aggregate daily metrics from api_usage (source of truth)
            row = conn.execute(
                """
                SELECT
                    COUNT(DISTINCT run_id)                          AS analyses_completed,
                    COUNT(*)                                        AS api_calls_made,
                    COALESCE(SUM(total_tokens), 0)                 AS total_tokens_used,
                    COALESCE(SUM(total_cost_usd), 0.0)             AS total_cost_usd,
                    COALESCE(SUM(CASE WHEN is_error THEN 1 END), 0) AS errors_encountered,
                    CAST(AVG(latency_ms) AS INTEGER)               AS avg_latency_ms
                FROM api_usage
                WHERE agent_name = ? AND created_date = ?
                """,
                [self.agent_name, today],
            ).fetchone()

            analyses_completed = row[0] or 0
            api_calls_made     = row[1] or 0
            total_tokens_used  = row[2] or 0
            total_cost_usd     = row[3] or 0.0
            errors_encountered = row[4] or 0
            avg_latency_ms     = row[5]

            conn.execute(
                """
                INSERT INTO agent_logs (
                    log_date, agent_id, agent_name,
                    what_i_did, wins, losses, struggles, blockers,
                    analyses_completed, api_calls_made,
                    total_tokens_used, total_cost_usd,
                    errors_encountered, avg_latency_ms,
                    created_date, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (log_date, agent_id) DO UPDATE SET
                    what_i_did         = excluded.what_i_did,
                    wins               = excluded.wins,
                    losses             = excluded.losses,
                    struggles          = excluded.struggles,
                    blockers           = excluded.blockers,
                    analyses_completed = excluded.analyses_completed,
                    api_calls_made     = excluded.api_calls_made,
                    total_tokens_used  = excluded.total_tokens_used,
                    total_cost_usd     = excluded.total_cost_usd,
                    errors_encountered = excluded.errors_encountered,
                    avg_latency_ms     = excluded.avg_latency_ms,
                    updated_at         = excluded.updated_at
                """,
                [
                    today, self.agent_id or self.agent_name, self.agent_name,
                    what_i_did, wins, losses, struggles, blockers,
                    analyses_completed, api_calls_made,
                    total_tokens_used, total_cost_usd,
                    errors_encountered, avg_latency_ms,
                    today, now, now,
                ],
            )
            conn.commit()
            logger.debug(
                "agent_logs updated: %s | calls=%d cost=$%.4f",
                self.agent_name, api_calls_made, total_cost_usd,
            )
        except Exception as exc:
            logger.error("Failed to write agent_logs: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def run(self, ticker: str, context: dict[str, Any]) -> dict[str, Any]:
        """
        Execute this agent's primary analysis task.

        The Manager calls run() on each specialist in order and threads their
        outputs forward via the shared context dict.

        Args:
            ticker:  Uppercase stock ticker (e.g. 'AAPL').
            context: Shared pipeline dict. Must contain 'run_id'. Read upstream
                     outputs, write your output key for downstream agents.

        Returns:
            Dict with at minimum:
                status      — 'success' | 'partial' | 'failed' | 'skipped'
                cost_usd    — total_cost_usd for this agent's calls (for AI Analyst)
                output      — agent-specific payload consumed by downstream agents
        """
        raise NotImplementedError
