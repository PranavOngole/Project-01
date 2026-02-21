"""
agents/base_agent.py
Foundation for all Project-01 research agents.

Every agent (Manager, Business Analyst, Fundamental Analyst, etc.) inherits
from BaseAgent and gets for free:
  - Prompt loading from PROMPT_DIR (no prompts stored in this repo)
  - Anthropic API call wrapper with model selection
  - Token usage tracking: input, output, cache read/write
  - Cost calculation per call (pricing from BRD v1.0)
  - Automatic retry on rate limits and transient API errors
  - Logging to DuckDB: api_usage (per call) + agent_logs (per run)

Usage:
    class MyAgent(BaseAgent):
        def run(self, ticker: str, context: dict) -> dict:
            system = self.load_prompt("my_agent_system")
            result = self.call_api(
                messages=[{"role": "user", "content": f"Analyze {ticker}"}],
                system=system,
                ticker=ticker,
            )
            ...
            return {"output": ...}
"""

import logging
import time
from abc import ABC, abstractmethod
from datetime import date
from typing import Any

import anthropic
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import settings
from config.prompts import load_prompt, load_prompt_from_env
from data.schema import get_connection

logger = logging.getLogger(__name__)


# ── Pricing table (USD per token) ─────────────────────────────────────────────
# Source: BRD v1.0 API Cost Reference. Update if Anthropic changes pricing.
# Cache write = 1.25x input. Cache read = 0.10x input.

PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-6": {
        "input":        5.00 / 1_000_000,
        "output":      25.00 / 1_000_000,
        "cache_write":  6.25 / 1_000_000,  # 1.25 × input
        "cache_read":   0.50 / 1_000_000,  # 0.10 × input
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

# Fallback pricing for unknown/new model IDs — use Sonnet rates
_FALLBACK_PRICING = PRICING["claude-sonnet-4-6"]


# ── Base agent ────────────────────────────────────────────────────────────────

class BaseAgent(ABC):
    """
    Abstract base class for all Project-01 research agents.

    Subclass this and implement run(). Everything else — API calls, cost
    tracking, retry logic, DB logging — is handled here.
    """

    def __init__(self, agent_name: str, model: str | None = None) -> None:
        """
        Args:
            agent_name: Human-readable name for logging (e.g. 'FundamentalAnalyst').
            model:      Anthropic model ID. Defaults to the model set in settings
                        for this agent type — override only in tests.
        """
        self.agent_name = agent_name
        self.model = model or settings.MANAGER_MODEL
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        logger.debug("Initialized agent '%s' using model '%s'", agent_name, self.model)

    # ── Prompt loading ────────────────────────────────────────────────────────

    def load_prompt(self, prompt_key: str) -> str:
        """
        Load a prompt from PROMPT_DIR by key.

        Prompt content is stored in the private repo — NOT in this file.
        Set PROMPT_DIR in .env to point at Project-01-Private/agents/prompts/.

        Args:
            prompt_key: File name without .md extension (e.g. 'manager_system').

        Returns:
            Prompt text as a string.
        """
        return load_prompt(prompt_key)

    def load_prompt_env(self, env_key: str, fallback_key: str | None = None) -> str:
        """
        Load a prompt from an environment variable (Railway deployment pattern).

        Args:
            env_key:      Env var name (e.g. 'PROMPT_MANAGER_SYSTEM').
            fallback_key: Optional prompt_key to try via PROMPT_DIR if env var missing.
        """
        return load_prompt_from_env(env_key, fallback_key)

    # ── API call wrapper ──────────────────────────────────────────────────────

    def call_api(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        ticker: str | None = None,
        enable_thinking: bool = False,
        thinking_budget_tokens: int = 8_000,
    ) -> dict[str, Any]:
        """
        Call the Anthropic Messages API with full tracking and retry.

        Args:
            messages:               Conversation in Anthropic message format.
            system:                 System prompt — pass a plain string, or a list of
                                    content blocks if using prompt caching (cache_control).
            max_tokens:             Output token limit. Defaults to MAX_TOKENS_PER_AGENT_CALL.
            ticker:                 Stock ticker for associating this call in api_usage.
            enable_thinking:        Enable extended thinking (Opus 4.6 only).
            thinking_budget_tokens: Token budget allocated for the thinking block.

        Returns:
            dict with keys:
                content      — list of Anthropic content blocks
                usage        — dict: input_tokens, output_tokens, cache_read_tokens,
                               cache_write_tokens
                cost_usd     — float, USD cost for this call
                duration_ms  — int, wall-clock time in milliseconds
        """
        effective_max_tokens = max_tokens or settings.MAX_TOKENS_PER_AGENT_CALL

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": effective_max_tokens,
            "messages": messages,
        }

        if system is not None:
            kwargs["system"] = system

        if enable_thinking:
            # Extended thinking — Opus 4.6 only. Adds reasoning transparency + cost.
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget_tokens,
            }

        start = time.monotonic()
        response = self._call_with_retry(**kwargs)
        duration_ms = int((time.monotonic() - start) * 1000)

        # ── Extract usage ─────────────────────────────────────────────────────
        usage = response.usage
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

        cost_usd = self.calculate_cost(
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )

        logger.info(
            "[%s] %s | ticker=%s in=%d out=%d cache_r=%d cache_w=%d "
            "cost=$%.4f dur=%dms",
            self.agent_name,
            self.model,
            ticker or "—",
            input_tokens,
            output_tokens,
            cache_read,
            cache_write,
            cost_usd,
            duration_ms,
        )

        self._log_api_usage(
            ticker=ticker,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=cost_usd,
            call_duration_ms=duration_ms,
        )

        return {
            "content": response.content,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
            },
            "cost_usd": cost_usd,
            "duration_ms": duration_ms,
        }

    def _call_with_retry(self, **kwargs: Any) -> anthropic.types.Message:
        """
        Internal: Anthropic API call wrapped in tenacity retry logic.

        Retries up to 3 times on RateLimitError, APITimeoutError, and
        InternalServerError with exponential backoff (2s → 4s → 30s cap).
        """
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type(
                (
                    anthropic.RateLimitError,
                    anthropic.APITimeoutError,
                    anthropic.InternalServerError,
                )
            ),
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
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """
        Calculate the USD cost for a single API call.

        Uses pricing from BRD v1.0. Falls back to Sonnet rates for unknown models.

        Returns:
            Cost in USD, rounded to 6 decimal places.
        """
        rates = PRICING.get(model, _FALLBACK_PRICING)

        cost = (
            input_tokens       * rates["input"]
            + output_tokens    * rates["output"]
            + cache_read_tokens  * rates["cache_read"]
            + cache_write_tokens * rates["cache_write"]
        )
        return round(cost, 6)

    # ── DuckDB logging ────────────────────────────────────────────────────────

    def _log_api_usage(
        self,
        ticker: str | None,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        cost_usd: float,
        call_duration_ms: int,
    ) -> None:
        """Write one row to api_usage. Silently swallows DB errors so a logging
        failure never breaks the agent pipeline."""
        try:
            conn = get_connection()
            conn.execute(
                """
                INSERT INTO api_usage (
                    agent_name, model, ticker, analysis_date,
                    input_tokens, output_tokens, thinking_tokens,
                    cache_write_tokens, cache_read_tokens,
                    cost_usd, call_duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    self.agent_name,
                    self.model,
                    ticker,
                    date.today(),
                    input_tokens,
                    output_tokens,
                    0,  # thinking_tokens — not separately reported by API; tracked as 0
                    cache_write_tokens,
                    cache_read_tokens,
                    cost_usd,
                    call_duration_ms,
                ],
            )
            conn.commit()
        except Exception as exc:
            logger.error("Failed to write api_usage row: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def log_activity(
        self,
        status: str,
        ticker: str | None = None,
        wins: str | None = None,
        losses: str | None = None,
        blockers: str | None = None,
        notes: str | None = None,
    ) -> None:
        """
        Write an entry to agent_logs documenting what this agent did.

        Call this at the end of every run() to maintain the daily activity log.

        Args:
            status:   'success' | 'partial' | 'failed' | 'skipped'
            ticker:   Stock ticker this run was for.
            wins:     What worked well.
            losses:   What didn't work or produced low-quality output.
            blockers: Anything that prevented full completion.
            notes:    Any other context worth recording.
        """
        try:
            conn = get_connection()
            conn.execute(
                """
                INSERT INTO agent_logs (
                    agent_name, log_date, ticker,
                    status, wins, losses, blockers, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    self.agent_name,
                    date.today(),
                    ticker,
                    status,
                    wins,
                    losses,
                    blockers,
                    notes,
                ],
            )
            conn.commit()
        except Exception as exc:
            logger.error("Failed to write agent_logs row: %s", exc)
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

        All concrete agents must implement this method. The Manager agent calls
        run() on each specialist and passes their outputs forward via context.

        Args:
            ticker:  Uppercase stock ticker (e.g. 'AAPL').
            context: Shared pipeline dict. Populated by upstream agents and the
                     data pipeline — read what you need, write your output key.

        Returns:
            Dict with this agent's outputs. Keys are consumed by downstream agents.
            Always include 'cost_usd' and 'status' for the AI Analyst to track.

        Example return shape:
            {
                "status": "success",
                "cost_usd": 0.023,
                "output": { ... }   # agent-specific payload
            }
        """
        raise NotImplementedError
