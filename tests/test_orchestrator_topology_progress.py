#!/usr/bin/env python3
"""Regression tests for Phase 34 topology runner progress events."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import shared.orchestrator as orchestrator_module
from shared.config import TGsConfig
from shared.db import Database
from shared.orchestrator import AgentResult, Orchestrator, Provider
from shared.planner import CLIBackend, ExecutionPlan, Planner, Subtask


class DummyBackend(CLIBackend):
    def call(
        self,
        prompt: str,
        model: str | None = None,
        timeout: int = 120,
    ) -> str | None:
        return None


class DummyProvider(Provider):
    def resolve_model(self, tier: str) -> str:
        return f"dummy-{tier}"

    def execute(self, subtask: Subtask, model: str, timeout: int = 120) -> str | None:
        return f"{model}:{subtask.id}"

    def available_tiers(self) -> list[str]:
        return ["low", "medium", "high"]

    def provider_info(self) -> dict:
        return {"primary": "dummy-provider"}


class DummyPlanner(Planner):
    def __init__(self) -> None:
        self._backend = DummyBackend()

    def plan(self, *args, **kwargs):  # pragma: no cover - not exercised here
        raise NotImplementedError


class ProgressStubOrchestrator(Orchestrator):
    def __init__(self, config: TGsConfig, db: Database) -> None:
        super().__init__(config, DummyProvider(), DummyPlanner(), db=db)

    def execute_subtask(
        self,
        subtask: Subtask,
        timeout: int = 120,
        score: float | None = None,
        *,
        execution_id: str | None = None,
        plan_revision: int = 1,
        current_wave: int | None = None,
        prefetched_artifacts: list[dict[str, object]] | None = None,
    ) -> AgentResult:
        assert self._db is not None
        if execution_id is not None:
            self._db.log_agent_result(
                session_id="topology-progress",
                task_hash=execution_id,
                agent_id=subtask.id,
                tier=subtask.tier,
                model=subtask.model,
            )
        if self._db is not None and execution_id is not None and current_wave is not None:
            for artifact_type in subtask.produces:
                artifact_ref = f"{artifact_type}-{subtask.id}"
                self._db.save_artifact(
                    execution_id,
                    plan_revision,
                    current_wave,
                    str(subtask.id),
                    artifact_type,
                    f"payload for {subtask.id}",
                    {
                        "summary_text": f"subtask {subtask.id}",
                        "length_chars": len(f"subtask {subtask.id}"),
                        "artifact_ref": artifact_ref,
                    },
                )
        return AgentResult(
            subtask_id=subtask.id,
            tier=subtask.tier,
            model=subtask.model,
            output=f"completed {subtask.id}",
            token_count=1,
        )


def _build_config() -> TGsConfig:
    config = TGsConfig()
    config.parallelism.enabled = False
    return config


def _build_progress_plan() -> ExecutionPlan:
    subtasks = [
        Subtask(id=1, description="wave one", tier="low", model="dummy-low", produces=["result"]),
        Subtask(id=2, description="wave two", tier="low", model="dummy-low", depends_on=[1], produces=["result"]),
        Subtask(id=3, description="wave three", tier="low", model="dummy-low", depends_on=[2], produces=["result"]),
    ]
    return ExecutionPlan(
        analysis="progress",
        subtasks=subtasks,
        waves=[[1], [2], [3]],
        total_agents=3,
        strategy="dag",
        topology="dag",
    )


def _build_fallback_plan() -> ExecutionPlan:
    subtasks = [
        Subtask(id=1, description="root", tier="low", model="dummy-low", produces=["result"]),
        Subtask(id=2, description="child", tier="low", model="dummy-low", depends_on=[1], produces=["result"]),
        Subtask(id=3, description="grandchild", tier="low", model="dummy-low", depends_on=[2], produces=["result"]),
    ]
    return ExecutionPlan(
        analysis="fallback",
        subtasks=subtasks,
        waves=[[1], [2], [3]],
        total_agents=3,
        strategy="dag",
        topology="star",
        _topology_explicit=True,
    )


def test_wave_progress_events() -> None:
    """D-01..D-07: each completed wave should emit one stable progress event."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "swarm-progress.db")
        try:
            orchestrator = ProgressStubOrchestrator(_build_config(), db)
            orchestrator._execute_dag_runner(
                _build_progress_plan(),
                execution_id="swarm-progress",
                plan_revision=1,
            )

            with db.conn() as conn:
                rows = conn.execute(
                    """
                    SELECT payload
                    FROM swarm_events
                    WHERE swarm_id = ? AND event_type = 'wave_progress'
                    ORDER BY id
                    """,
                    ("swarm-progress",),
                ).fetchall()
                swarm_row = conn.execute(
                    "SELECT topology FROM swarm_runs WHERE swarm_id = ?",
                    ("swarm-progress",),
                ).fetchone()

            assert len(rows) == 3
            payloads = [json.loads(row[0]) for row in rows]
            assert [payload["wave"] for payload in payloads] == [1, 2, 3]
            assert [payload["completed_subtasks"] for payload in payloads] == [1, 1, 1]
            assert [payload["pending_subtasks"] for payload in payloads] == [2, 1, 0]
            assert [payload["artifacts_produced"] for payload in payloads] == [1, 1, 1]
            assert all(payload["round"] == 0 for payload in payloads)
            assert swarm_row is not None
            assert swarm_row[0] == "dag"
        finally:
            db.close()


def test_runner_fallback_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-09..D-12: fallback should be separate and round stays 0 in Phase 34."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "swarm-fallback.db")
        try:
            orchestrator = ProgressStubOrchestrator(_build_config(), db)
            monkeypatch.setattr(
                orchestrator_module,
                "PLANNER_ALLOW_TOPOLOGY_FALLBACK",
                True,
            )
            orchestrator._execute_star_runner(
                _build_fallback_plan(),
                execution_id="swarm-fallback",
                plan_revision=1,
            )

            with db.conn() as conn:
                fallback_row = conn.execute(
                    """
                    SELECT payload
                    FROM swarm_events
                    WHERE swarm_id = ? AND event_type = 'runner_fallback'
                    """,
                    ("swarm-fallback",),
                ).fetchone()
                wave_rows = conn.execute(
                    """
                    SELECT payload
                    FROM swarm_events
                    WHERE swarm_id = ? AND event_type = 'wave_progress'
                    ORDER BY id
                    """,
                    ("swarm-fallback",),
                ).fetchall()
                swarm_row = conn.execute(
                    "SELECT topology FROM swarm_runs WHERE swarm_id = ?",
                    ("swarm-fallback",),
                ).fetchone()

            assert fallback_row is not None
            fallback_payload = json.loads(fallback_row[0])
            assert fallback_payload["declared_topology"] == "star"
            assert fallback_payload["effective_runner"] == "linear"
            assert fallback_payload["reason"]

            assert len(wave_rows) == 3
            wave_payloads = [json.loads(row[0]) for row in wave_rows]
            assert [payload["wave"] for payload in wave_payloads] == [1, 2, 3]
            assert all(payload["round"] == 0 for payload in wave_payloads)
            assert swarm_row is not None
            assert swarm_row[0] == "star"
        finally:
            db.close()
