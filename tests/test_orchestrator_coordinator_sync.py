#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import TGsConfig
from shared.db import Database
from shared.orchestrator import Orchestrator, Provider
from shared.planner import ExecutionPlan, Planner, Subtask


class RecordingProvider(Provider):
    def __init__(self, outputs: dict[int, str]) -> None:
        self.outputs = outputs
        self.calls: list[int] = []
        self.descriptions: dict[int, str] = {}

    def resolve_model(self, tier: str) -> str:
        return f"dummy-{tier}"

    def execute(self, subtask: Subtask, model: str, timeout: int = 120) -> str | None:
        self.calls.append(subtask.id)
        self.descriptions[subtask.id] = subtask.description
        return self.outputs[subtask.id]

    def available_tiers(self) -> list[str]:
        return ["low", "medium", "high"]


class DummyPlanner(Planner):
    def __init__(self) -> None:
        self._backend = SimpleNamespace(call=lambda *args, **kwargs: None)

    def plan(self, *args, **kwargs):  # pragma: no cover - not exercised here
        raise NotImplementedError


def _build_plan() -> ExecutionPlan:
    subtasks = [
        Subtask(id=1, description="produce artifact", tier="low", model="low", produces=["summary"]),
        Subtask(
            id=2,
            description="coordinate next wave",
            tier="low",
            model="low",
            depends_on=[1],
            is_coordinator=True,
        ),
        Subtask(id=3, description="worker after coordinator", tier="low", model="low", depends_on=[1]),
    ]
    return ExecutionPlan(
        analysis="coordinator",
        subtasks=subtasks,
        waves=[[1], [2, 3]],
        total_agents=3,
        strategy="dag",
    )


def test_coordinator_runs_before_next_wave() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "coordinator.db")
        try:
            provider = RecordingProvider({
                1: "artifact payload",
                2: json.dumps({"verdict": "complete"}),
                3: "worker output",
            })
            config = TGsConfig()
            config.parallelism.enabled = True
            orchestrator = Orchestrator(config, provider, DummyPlanner(), db=db)

            results = orchestrator.execute_plan(
                _build_plan(),
                execution_id="exec-13",
                plan_revision=1,
            )

            assert provider.calls == [1, 2, 3]
            assert set(results) == {1, 2, 3}
        finally:
            db.close()


def test_coordinator_receives_summary_only_artifacts() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "coordinator-summary.db")
        try:
            payload = "raw-start " + ("x" * 5000) + " ENDMARK"
            provider = RecordingProvider({
                1: payload,
                2: json.dumps({"verdict": "complete"}),
                3: "worker output",
            })
            config = TGsConfig()
            orchestrator = Orchestrator(config, provider, DummyPlanner(), db=db)

            orchestrator.execute_plan(
                _build_plan(),
                execution_id="exec-13",
                plan_revision=1,
            )

            coordinator_description = provider.descriptions[2]
            assert "COORDINATOR RESPONSE CONTRACT" in coordinator_description
            assert '"verdict":"complete|another-pass|fallback"' in coordinator_description
            assert "--- ARTIFACT HANDOFF ---" in coordinator_description
            assert "Reference: artifact:" in coordinator_description
            assert "ENDMARK" not in coordinator_description
        finally:
            db.close()


def test_coordinator_another_pass_ignores_artifact_backed_amendment() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "coordinator-amendment.db")
        try:
            provider = RecordingProvider({
                1: "artifact payload",
                2: json.dumps(
                    {
                        "verdict": "another-pass",
                        "amendment": {
                            "subtask_updates": [
                                {"id": 3, "description": "worker after coordinator revised"},
                            ]
                        },
                    }
                ),
                3: "worker output",
            })
            config = TGsConfig()
            orchestrator = Orchestrator(config, provider, DummyPlanner(), db=db)

            orchestrator.execute_plan(
                _build_plan(),
                execution_id="exec-13",
                plan_revision=1,
            )

            assert provider.calls == [1, 2, 3]
            assert provider.descriptions[3] == "worker after coordinator"
        finally:
            db.close()
