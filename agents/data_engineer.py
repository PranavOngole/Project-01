"""
agents/data_engineer.py
Data Engineer agent — fetches, validates, and caches raw market data.

Model: Sonnet (handles data validation logic and structured output)
Phase 4A: scaffold only — run() returns a stub response.
Phase 4C: real implementation (yfinance fetch → DuckDB cache → context payload).
"""

from __future__ import annotations

from typing import Any

from agents.base_agent import BaseAgent
from config import settings


class DataEngineerAgent(BaseAgent):

    def __init__(self) -> None:
        super().__init__(
            agent_name="data_engineer",
            model=settings.DATA_ENGINEER_MODEL,
        )

    def run(self, ticker: str, context: dict[str, Any]) -> dict[str, Any]:
        """
        Fetch, validate, and cache raw market data for `ticker`.

        Phase 4A stub — always returns skipped.
        Phase 4C will:
          1. Check DuckDB cache freshness (stock_prices, stock_fundamentals)
          2. Fetch from yfinance if stale or missing
          3. Write to DuckDB
          4. Return a data payload for downstream agents

        Args:
            ticker:  Uppercase stock ticker (e.g. 'AAPL').
            context: Shared pipeline dict. Must contain 'run_id'.

        Returns:
            status      — 'success' | 'partial' | 'failed' | 'skipped'
            cost_usd    — 0.0 (no API calls in 4A)
            output      — empty dict in 4A; full data payload in 4C
        """
        return {
            "status": "skipped",
            "cost_usd": 0.0,
            "output": {},
        }
