#!/usr/bin/env python3
"""Scaffold tests for hierarchical artifact behavior."""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shared.config
import shared.db
import shared.orchestrator
import shared.planner


class DummyProvider(shared.orchestrator.Provider):
    def resolve_model(self, tier: str) -> str:
        return f"dummy-{tier}"

    def execute(
        self,
        subtask: shared.planner.Subtask,
        model: str,
        timeout: int = 120,
    ) -> str | None:
        return f"{model}:{subtask.id}"

    def available_tiers(self) -> list[str]:
        return ["low", "medium", "high"]


class DummyPlanner(shared.planner.Planner):
    def __init__(self) -> None:
        self._backend = SimpleNamespace(call=lambda *args, **kwargs: None)

    def plan(self, *args, **kwargs):  # pragma: no cover - not exercised here
        raise NotImplementedError


def test_skeleton() -> None:
    """The hierarchical DB seam should be importable before implementation lands."""
    assert hasattr(shared.db.Database, "get_parent_scoped_artifacts")
    assert callable(shared.db.Database.get_parent_scoped_artifacts)


def test_parent_scoped_selection() -> None:
    """Parent-scoped lookup should honor direct-parent scope, active revision, and stable ties."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = shared.db.Database(Path(tmpdir) / "hier.db")
        try:
            db.save_artifact(
                execution_id="exec-1",
                plan_revision=1,
                wave=1,
                subtask_id="parent-old",
                artifact_type="summary",
                full_payload="stale payload",
                compact_summary="stale summary",
                parent_execution_id="parent-1",
            )
            db.save_artifact(
                execution_id="exec-1",
                plan_revision=2,
                wave=1,
                subtask_id="parent-direct",
                artifact_type="summary",
                full_payload="older payload",
                compact_summary="older summary",
                parent_execution_id="parent-1",
            )
            latest_ref = db.save_artifact(
                execution_id="exec-1",
                plan_revision=2,
                wave=2,
                subtask_id="parent-direct",
                artifact_type="summary",
                full_payload="latest payload",
                compact_summary="latest summary",
                parent_execution_id="parent-1",
            )
            db.save_artifact(
                execution_id="exec-1",
                plan_revision=2,
                wave=3,
                subtask_id="parent-other",
                artifact_type="summary",
                full_payload="other parent payload",
                compact_summary="other parent summary",
                parent_execution_id="parent-2",
            )
            selected = db.get_parent_scoped_artifacts(
                "exec-1",
                2,
                "parent-1",
                ["summary"],
            )
            assert selected == [
                {
                    "artifact_type": "summary",
                    "summary_text": "latest summary",
                    "length_chars": len("latest summary"),
                    "artifact_ref": latest_ref,
                    "producer_subtask_id": "parent-direct",
                    "parent_execution_id": "parent-1",
                }
            ]

            with patch("shared.db.time.time", return_value=1_700_000_000):
                first_ref = db.save_artifact(
                    execution_id="exec-2",
                    plan_revision=2,
                    wave=2,
                    subtask_id="parent-a",
                    artifact_type="summary",
                    full_payload="tie payload a",
                    compact_summary="tie summary a",
                    parent_execution_id="parent-tie",
                )
                second_ref = db.save_artifact(
                    execution_id="exec-2",
                    plan_revision=2,
                    wave=2,
                    subtask_id="parent-b",
                    artifact_type="summary",
                    full_payload="tie payload b",
                    compact_summary="tie summary b",
                    parent_execution_id="parent-tie",
                )

            first = db.get_parent_scoped_artifacts("exec-2", 2, "parent-tie", ["summary"])
            second = db.get_parent_scoped_artifacts("exec-2", 2, "parent-tie", ["summary"])
            assert first == second
            expected_ref = min(first_ref, second_ref)
            expected_subtask = "parent-a" if expected_ref == first_ref else "parent-b"
            assert first[0]["artifact_ref"] == expected_ref
            assert first[0]["producer_subtask_id"] == expected_subtask
        finally:
            db.close()


def test_missing_parent_degrades_subtree() -> None:
    """Missing direct-parent artifacts should persist one degradation event and stay sticky."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = shared.db.Database(Path(tmpdir) / "hier.db")
        orchestrator = shared.orchestrator.Orchestrator(
            shared.config.TGsConfig(),
            DummyProvider(),
            DummyPlanner(),
            db=db,
        )
        try:
            first = orchestrator.bind_parent_artifacts(
                "exec-1",
                "child-1",
                "parent-1",
                ["summary"],
                db=db,
            )
            assert first == {"degraded": True, "artifact_refs": []}
            events = db.query_degradation_events("exec-1")
            assert events == [
                {
                    "parent_subtask_id": "parent-1",
                    "missing_artifact_type": "summary",
                    "affected_child_subtask_id": "child-1",
                    "reason": "missing_parent_artifacts",
                    "created_at": events[0]["created_at"],
                }
            ]

            second = orchestrator.bind_parent_artifacts(
                "exec-1",
                "child-1",
                "parent-1",
                ["summary"],
                db=db,
            )
            assert second == {"degraded": True, "artifact_refs": []}
            assert len(db.query_degradation_events("exec-1")) == 1
        finally:
            db.close()
