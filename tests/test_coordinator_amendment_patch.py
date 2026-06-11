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
            stable_id="phase32-plan01-task01",
            description="existing coordinator",
            tier="low",
            model="low",
            depends_on=[],
            is_coordinator=True,
            produces=["outline"],
        ),
        Subtask(
            id=2,
            stable_id="phase32-plan01-task02",
            description="future worker",
            tier="low",
            model="low",
            depends_on=[],
            consumes=["outline"],
        ),
    ]
    return ExecutionPlan(
        analysis="coordinator-amendment",
        subtasks=subtasks,
        waves=[[1, 2]],
        total_agents=2,
        strategy="parallel",
        topology="dag",
        max_rounds=3,
    )


def test_patch_amendment_accepts_append_and_persists_audit() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
        db = Database(Path(db_file.name))
        try:
            orchestrator = Orchestrator(TGsConfig(), DummyProvider(), DummyPlanner(), db=db)
            current_plan = _base_plan()

            updated_plan, revision, applied = orchestrator.apply_coordinator_amendment_tx(
                current_plan,
                {
                    "reason": "tighten worker handoff",
                    "max_rounds": 5,
                    "patch": {
                        "phase32-plan01-task02": {
                            "description": "future worker refined",
                            "depends_on": ["phase32-plan01-task01"],
                            "produces": ["draft"],
                        }
                    },
                    "append": [
                        {
                            "description": "final reviewer",
                            "tier": "low",
                            "depends_on": ["phase32-plan01-task02"],
                            "consumes": ["draft"],
                        }
                    ],
                },
                proposer_id="coordinator-1",
                execution_id="plan-32",
                plan_revision=1,
                subtask_states={1: "completed", 2: "planned"},
            )

            assert applied is True
            assert revision == 2
            assert updated_plan.max_rounds == 5
            assert [subtask.stable_id for subtask in updated_plan.subtasks] == [
                "phase32-plan01-task01",
                "phase32-plan01-task02",
                "phase32-plan01-task03",
            ]
            assert updated_plan.subtasks[1].description == "future worker refined"
            assert updated_plan.subtasks[1].depends_on == [1]
            assert updated_plan.subtasks[1].produces == ["draft"]
            assert updated_plan.subtasks[2].description == "final reviewer"
            assert updated_plan.subtasks[2].depends_on == [2]
            assert updated_plan.subtasks[2].consumes == ["draft"]

            with db.conn() as conn:
                audit = conn.execute(
                    """
                    SELECT outcome, proposer_id, reason
                    FROM coordinator_amendments
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                revision_count = conn.execute(
                    "SELECT COUNT(*) FROM plan_revisions"
                ).fetchone()[0]

            assert audit == ("accepted", "coordinator-1", "tighten worker handoff")
            assert revision_count == 1
        finally:
            db.close()


def test_patch_amendment_rejects_unknown_stable_id_and_persists_error() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
        db = Database(Path(db_file.name))
        try:
            orchestrator = Orchestrator(TGsConfig(), DummyProvider(), DummyPlanner(), db=db)
            current_plan = _base_plan()

            updated_plan, revision, applied = orchestrator.apply_coordinator_amendment_tx(
                current_plan,
                {
                    "patch": {
                        "phase32-plan01-task99": {
                            "description": "mutated worker",
                        }
                    }
                },
                proposer_id="coordinator-1",
                execution_id="plan-32",
                plan_revision=1,
                subtask_states={1: "completed", 2: "planned"},
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

            assert audit == (
                "rejected",
                "D-12: coordinator amendment targeted unknown stable task id 'phase32-plan01-task99'",
            )
            assert revision_count == 0
        finally:
            db.close()


def test_patch_amendment_rejects_new_coordinator_authority() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
        db = Database(Path(db_file.name))
        try:
            orchestrator = Orchestrator(TGsConfig(), DummyProvider(), DummyPlanner(), db=db)
            current_plan = _base_plan()

            updated_plan, revision, applied = orchestrator.apply_coordinator_amendment_tx(
                current_plan,
                {
                    "patch": {
                        "phase32-plan01-task02": {
                            "is_coordinator": True,
                        }
                    }
                },
                proposer_id="coordinator-1",
                execution_id="plan-32",
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

            assert audit == (
                "rejected",
                "D-12: coordinator amendment cannot grant coordinator authority to a new task",
            )
        finally:
            db.close()


def test_patch_amendment_rejects_appended_coordinator_authority() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
        db = Database(Path(db_file.name))
        try:
            orchestrator = Orchestrator(TGsConfig(), DummyProvider(), DummyPlanner(), db=db)
            current_plan = _base_plan()

            updated_plan, revision, applied = orchestrator.apply_coordinator_amendment_tx(
                current_plan,
                {
                    "append": [
                        {
                            "description": "extra coordinator",
                            "tier": "low",
                            "is_coordinator": True,
                        }
                    ]
                },
                proposer_id="coordinator-1",
                execution_id="plan-32",
                plan_revision=1,
                subtask_states={1: "completed", 2: "planned"},
            )

            assert applied is False
            assert revision == 1
            assert len(updated_plan.subtasks) == 2

            with db.conn() as conn:
                audit = conn.execute(
                    """
                    SELECT outcome, rejection_reason
                    FROM coordinator_amendments
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()

            assert audit == (
                "rejected",
                "D-12: coordinator amendment cannot grant coordinator authority to appended tasks",
            )
        finally:
            db.close()


def test_patch_amendment_rejects_tier_escalation() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
        db = Database(Path(db_file.name))
        try:
            orchestrator = Orchestrator(TGsConfig(), DummyProvider(), DummyPlanner(), db=db)
            current_plan = _base_plan()

            updated_plan, revision, applied = orchestrator.apply_coordinator_amendment_tx(
                current_plan,
                {
                    "append": [
                        {
                            "description": "expensive worker",
                            "tier": "high",
                        }
                    ]
                },
                proposer_id="coordinator-1",
                execution_id="plan-32",
                plan_revision=1,
                subtask_states={1: "completed", 2: "planned"},
            )

            assert applied is False
            assert revision == 1
            assert len(updated_plan.subtasks) == 2

            with db.conn() as conn:
                audit = conn.execute(
                    """
                    SELECT outcome, rejection_reason
                    FROM coordinator_amendments
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()

            assert audit == (
                "rejected",
                "D-12: appended coordinator subtask tier exceeds the plan-approved tier ceiling",
            )
        finally:
            db.close()


def test_patch_amendment_rejects_unknown_numeric_dependency() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
        db = Database(Path(db_file.name))
        try:
            orchestrator = Orchestrator(TGsConfig(), DummyProvider(), DummyPlanner(), db=db)
            current_plan = _base_plan()

            updated_plan, revision, applied = orchestrator.apply_coordinator_amendment_tx(
                current_plan,
                {
                    "patch": {
                        "phase32-plan01-task02": {
                            "depends_on": [999],
                        }
                    }
                },
                proposer_id="coordinator-1",
                execution_id="plan-32",
                plan_revision=1,
                subtask_states={1: "completed", 2: "planned"},
            )

            assert applied is False
            assert revision == 1
            assert updated_plan.subtasks[1].depends_on == []

            with db.conn() as conn:
                audit = conn.execute(
                    """
                    SELECT outcome, rejection_reason
                    FROM coordinator_amendments
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()

            assert audit == (
                "rejected",
                "D-12: coordinator amendment contains an unknown dependency reference",
            )
        finally:
            db.close()
