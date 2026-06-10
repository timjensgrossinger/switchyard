"""
Helpers for Phase 15 regression and concurrency tests.
Provides deterministic fixtures to assemble small multi-wave plans and in-memory DB setup.
"""
from __future__ import annotations

import pytest
import sqlite3
from pathlib import Path
from typing import Any, Dict


@pytest.fixture
def temp_sqlite_db(tmp_path: Path) -> str:
    """Create a temporary SQLite DB file path for tests to use.
    Tests must ensure they do not point at developer's persistent DB.
    """
    db_path = str(tmp_path / "phase15_test.db")
    # Caller is responsible for initializing schema where needed.
    return db_path


def make_sample_plan(wave_count: int = 3, *, topology: str = "linear", urgency_score: float = 0.5) -> Dict[str, Any]:
    """Return a small in-memory representation of a multi-wave plan used by the tests.
    This is intentionally lightweight so tests can exercise orchestrator.publish/consume flows.
    """
    return {
        "waves": [
            {"id": i + 1, "tasks": [
                {"name": f"task-{i+1}-a", "topology": topology, "urgency": urgency_score}
            ]}
            for i in range(wave_count)
        ]
    }


# Small helpers to assert telemetry shapes in a lightweight manner

def assert_has_telemetry_row(rows, key):
    for r in rows:
        if key in r:
            return True
    raise AssertionError(f"No telemetry row contained key={key}")


# Lightweight stubs reused by Phase 15 tests to avoid touching prod code.
# These mirror the patterns used in tests/test_orchestrator.py but keep behaviour
# deterministic and fast for unit tests.
from types import SimpleNamespace

class DummyProvider:
    def resolve_model(self, tier: str) -> str:
        return f"dummy-{tier}"

    def execute(self, subtask, model: str, timeout: int = 120) -> str | None:
        # Return a compact output string used by other tests to assert artifact payloads
        return f"{model}:{getattr(subtask, 'id', 'x')}"

    def available_tiers(self) -> list[str]:
        return ["low", "medium", "high"]


class DummyPlanner:
    def __init__(self) -> None:
        self._backend = SimpleNamespace(call=lambda *args, **kwargs: None)

    def plan(self, *args, **kwargs):
        raise NotImplementedError


def run_stubbed_execute_wave(temp_db_path, max_workers: int = 2, *, urgency: float = 0.5, topology: str = "linear"):
    """Run a small wave using a StubOrchestrator and return telemetry rows.
    Uses existing shared.orchestrator.Orchestrator but overrides execute_subtask to avoid
    external provider calls.
    """
    from shared.db import Database
    from shared.orchestrator import Orchestrator, AgentResult
    from shared.config import TGsConfig

    class StubOrchestrator(Orchestrator):
        def __init__(self, config: TGsConfig, db: Database) -> None:
            super().__init__(config, DummyProvider(), DummyPlanner(), db=db)

        def execute_subtask(
            self,
            subtask,
            timeout: int = 120,
            score: float | None = None,
            *,
            execution_id: str | None = None,
            plan_revision: int = 1,
            current_wave: int | None = None,
        ) -> AgentResult:
            assert self._db is not None
            # Write a predictable telemetry row with Phase 15 explainability fields
            self._db.log_agent_result(
                session_id=execution_id or "wave-test",
                task_hash=f"task-{getattr(subtask, 'id', 'x')}",
                agent_id=getattr(subtask, 'id', 'x'),
                tier=getattr(subtask, 'tier', 'low'),
                model="dummy-low",
                urgency_score=getattr(subtask, 'urgency', None),
                selected_topology=getattr(subtask, 'topology', None),
                artifact_publish_count=1,
            )
            return AgentResult(
                subtask_id=getattr(subtask, 'id', None),
                tier=getattr(subtask, 'tier', 'low'),
                model="dummy-low",
                output=f"completed {getattr(subtask, 'id', None)}",
                token_count=1,
            )

    # Initialize DB and run a wave of 3 subtasks
    db = Database(temp_db_path)
    try:
        config = TGsConfig()
        config.parallelism.enabled = True
        config.parallelism.max_workers = max_workers
        orchestrator = StubOrchestrator(config, db)

        # Simple Subtask-like objects expected by Orchestrator. Use SimpleNamespace.
        Subtask = SimpleNamespace
        subtasks = [Subtask(id=i, description=f"s{i}", tier="low", urgency=urgency, topology=topology) for i in (1, 2, 3)]
        results = orchestrator.execute_wave(0, subtasks)
        with db.conn() as conn:
            rows = conn.execute("SELECT * FROM telemetry WHERE session_id = ?", ("wave-test",)).fetchall()
        return db, rows
    finally:
        db.close()
