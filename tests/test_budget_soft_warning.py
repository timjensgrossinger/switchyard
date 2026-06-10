#!/usr/bin/env python3
"""Tests for task-budget soft warnings and circuit breaker enforcement."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import TGsConfig
from shared.db import Database
from shared.orchestrator import CircuitBreakerError, Orchestrator, Provider
from shared.planner import ExecutionPlan, Subtask


class BudgetProvider(Provider):
    def __init__(self) -> None:
        self.calls = 0
        self._outputs = [
            "a" * 180,  # 45 tokens
            "b" * 160,  # 40 tokens -> soft warning at 85/100
            "c" * 100,  # 25 tokens -> circuit breaker at 110/100
            "d" * 200,
        ]

    def resolve_model(self, tier: str) -> str:
        return f"budget-{tier}"

    def execute(self, subtask: Subtask, model: str, timeout: int = 120) -> str | None:
        output = self._outputs[self.calls]
        self.calls += 1
        return output

    def available_tiers(self) -> list[str]:
        return ["low", "medium", "high"]


class BudgetPlanner:
    def plan(self, task: str, skip_cache: bool = False) -> ExecutionPlan:
        return ExecutionPlan(
            analysis="budget-test",
            subtasks=[
                Subtask(id=1, description="one", tier="low", model="budget-low"),
                Subtask(id=2, description="two", tier="low", model="budget-low"),
                Subtask(id=3, description="three", tier="low", model="budget-low"),
                Subtask(id=4, description="four", tier="low", model="budget-low"),
            ],
            waves=[[1, 2, 3, 4]],
            total_agents=4,
            strategy="sequential",
        )


def test_budget_warning(caplog) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "budget.db")
        config = TGsConfig()
        config.parallelism.enabled = False
        config.budgets.default_hard_cap_tokens = 100
        config.budgets.default_soft_warning_pct = 0.7

        provider = BudgetProvider()
        orchestrator = Orchestrator(config, provider, BudgetPlanner(), db=db)

        with pytest.raises(CircuitBreakerError):
            orchestrator.run("budget warning test")

        assert provider.calls == 3
        assert "soft token budget warning" in caplog.text
        assert "token circuit breaker" in caplog.text

        with db.conn() as conn:
            reason_rows = conn.execute(
                "SELECT reason FROM telemetry WHERE reason IS NOT NULL ORDER BY id",
            ).fetchall()

        assert [row[0] for row in reason_rows] == [
            "subtask_result",
            "subtask_result",
            "soft_warning",
            "subtask_result",
            "circuit_breaker",
        ]

        db.close()
