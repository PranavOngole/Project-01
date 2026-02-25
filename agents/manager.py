"""
agents/manager.py
The Manager — orchestrates the full research pipeline.

Agent ID:  MGR-01
Model:     Opus 4.6 (highest reasoning — reviews all specialist outputs,
           resolves conflicts, makes final call)
Phase 4A:  scaffold only — run() returns a stub response.
Phase 4B:  real implementation (creates analysis_run record, sequences agents,
           threads context, runs optional follow-up round, enforces budget).

Prompt:    Loaded from PROMPT_DIR/manager.md (private repo) or
           PROMPT_MANAGER_SYSTEM env var (Railway).
"""

from __future__ import annotations

from typing import Any

from agents.base_agent import BaseAgent
from config import settings


class ManagerAgent(BaseAgent):

    AGENT_ID = "MGR-01"

    def __init__(self) -> None:
        super().__init__(
            agent_name="manager",
            model=settings.MANAGER_MODEL,
            agent_id="MGR-01",
        )

    def run(self, ticker: str, context: dict[str, Any]) -> dict[str, Any]:
        """
        Orchestrate the full 9-step research pipeline for `ticker`.

        Phase 4A stub — always returns skipped.
        Phase 4B will:
          1. Create an analysis_run record in DuckDB
          2. Call each specialist agent in order, threading outputs via context
          3. Run optional follow-up round if conflicts detected (V1: max 1 per agent)
          4. Enforce MAX_COST_PER_REPORT_USD budget gate
          5. Write final results to analysis_runs, analysis_results, analysis_cache

        Args:
            ticker:  Uppercase stock ticker (e.g. 'AAPL').
            context: Shared pipeline dict. Populated by Manager before each agent call.

        Returns:
            status      — 'success' | 'partial' | 'failed' | 'skipped'
            cost_usd    — total pipeline cost across all agents (0.0 in 4A)
            output      — assembled report payload (empty dict in 4A)
        """
        return {
            "status": "skipped",
            "cost_usd": 0.0,
            "output": {},
        }
