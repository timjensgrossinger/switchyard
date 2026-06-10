#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import TGsConfig
from shared.db import Database
from shared.orchestrator import Orchestrator, Provider
from shared.planner import ExecutionPlan, Planner, Subtask


class DummyProvider(Provider):
    def resolve_model(self, tier: str) -> str:
        return f"dummy-{tier}"

    def execute(self, subtask: Subtask, model: str, timeout: int = 120) -> str | None:
        return "worker output"

    def available_tiers(self) -> list[str]:
        return ["low", "medium", "high"]


class DummyPlanner(Planner):
    def __init__(self) -> None:
        self._backend = SimpleNamespace(call=lambda *args, **kwargs: None)

    def plan(self, *args, **kwargs):  # pragma: no cover - not exercised here
        raise NotImplementedError


def _base_plan() -> ExecutionPlan:
    subtasks = [
        Subtask(
            id=1,
            stable_id="phase13-plan01-task01",
            description="existing coordinator",
            tier="low",
            model="low",
            depends_on=[],
            is_coordinator=True,
        ),
        Subtask(
            id=2,
            stable_id="phase13-plan01-task02",
            description="future worker",
            tier="low",
            model="low",
            depends_on=[],
        ),
    ]
    return ExecutionPlan(
        analysis="coordinator-amendment",
        subtasks=subtasks,
        waves=[[1, 2]],
        total_agents=2,
        strategy="parallel",
    )


def test_reject_amend_started_subtask() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "started.db")
        try:
            orchestrator = Orchestrator(TGsConfig(), DummyProvider(), DummyPlanner(), db=db)
            current_plan = _base_plan()

            updated_plan, revision, applied = orchestrator.apply_coordinator_amendment_tx(
                current_plan,
                {"subtask_updates": [{"id": 2, "description": "mutated worker"}]},
                proposer_id="coordinator-1",
                execution_id="plan-13",
                plan_revision=1,
                subtask_states={2: "started"},
            )

            assert applied is False
            assert revision == 1
            assert updated_plan.subtasks[1].description == "future worker"

            with db.conn() as conn:
                audit = conn.execute(
                    """
                    SELECT outcome, rejection_reason
                    FROM coordinator_amendments
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                revision_count = conn.execute(
                    "SELECT COUNT(*) FROM plan_revisions"
                ).fetchone()[0]

            assert audit == ("rejected", "D-03: coordinator amendment cannot modify started subtask 2")
            assert revision_count == 0
        finally:
            db.close()


def test_reject_dynamic_duplicate_coordinator_wave() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "duplicate.db")
        try:
            orchestrator = Orchestrator(TGsConfig(), DummyProvider(), DummyPlanner(), db=db)
            current_plan = _base_plan()

            updated_plan, revision, applied = orchestrator.apply_coordinator_amendment_tx(
                current_plan,
                {"subtask_updates": [{"id": 2, "is_coordinator": True}]},
                proposer_id="coordinator-1",
                execution_id="plan-13",
                plan_revision=1,
                subtask_states={1: "completed", 2: "planned"},
            )

            assert applied is False
            assert revision == 1
            assert updated_plan.subtasks[1].is_coordinator is False

            with db.conn() as conn:
                audit = conn.execute(
                    """
                    SELECT outcome, rejection_reason
                    FROM coordinator_amendments
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                revision_count = conn.execute(
                    "SELECT COUNT(*) FROM plan_revisions"
                ).fetchone()[0]

            assert audit is not None
            assert audit[0] == "rejected"
            assert "D-03" in audit[1]
            assert revision_count == 0
        finally:
            db.close()


def test_legacy_updates_resolve_stable_dependency_ids() -> None:
    orchestrator = Orchestrator(TGsConfig(), DummyProvider(), DummyPlanner())
    current_plan = _base_plan()

    updated_plan, revision, applied = orchestrator.apply_coordinator_amendment_tx(
        current_plan,
        {
            "subtask_updates": [
                {
                    "id": 2,
                    "depends_on": ["phase13-plan01-task01"],
                }
            ]
        },
        proposer_id="coordinator-1",
        execution_id="plan-13",
        plan_revision=1,
        subtask_states={1: "completed", 2: "planned"},
    )

    assert applied is True
    assert revision == 2
    assert updated_plan.subtasks[1].depends_on == [1]


def test_affected_subtree_targets_downstream_dependents_only() -> None:
    current_plan = ExecutionPlan(
        analysis="downstream-targeting",
        subtasks=[
            Subtask(
                id=1,
                stable_id="phase13-plan02-task01",
                description="root coordinator",
                tier="low",
                model="low",
                depends_on=[],
                is_coordinator=True,
            ),
            Subtask(
                id=2,
                stable_id="phase13-plan02-task02",
                description="changed worker",
                tier="low",
                model="low",
                depends_on=[1],
            ),
            Subtask(
                id=3,
                stable_id="phase13-plan02-task03",
                description="downstream dependent",
                tier="low",
                model="low",
                depends_on=[2],
            ),
            Subtask(
                id=4,
                stable_id="phase13-plan02-task04",
                description="unrelated worker",
                tier="low",
                model="low",
                depends_on=[1],
            ),
        ],
        waves=[[1], [2, 4], [3]],
        total_agents=4,
        strategy="dag",
    )

    targeted = Orchestrator._affected_subtree_ids(current_plan, {2})

    assert targeted == {2, 3}
