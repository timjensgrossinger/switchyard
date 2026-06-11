#!/usr/bin/env python3
from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

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


class BrokenProvider(DummyProvider):
    def resolve_model(self, tier: str) -> str:
        raise RuntimeError("provider unavailable")


class DummyPlanner(Planner):
    def __init__(self) -> None:
        self._backend = DummyBackend()

    def plan(self, *args, **kwargs):  # pragma: no cover - not exercised here
        raise NotImplementedError


class StarLoopOrchestrator(Orchestrator):
    def __init__(
        self,
        db: Database,
        coordinator_outputs: list[str],
        *,
        fail_linear: bool = False,
    ) -> None:
        config = TGsConfig()
        config.parallelism.enabled = False
        super().__init__(config, DummyProvider(), DummyPlanner(), db=db)
        self._coordinator_outputs = list(coordinator_outputs)
        self._fail_linear = fail_linear
        self.worker_runs: dict[int, int] = {}
        self.coordinator_runs = 0
        self.linear_fallback_calls = 0

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
        if subtask.is_coordinator:
            self.coordinator_runs += 1
            if not self._coordinator_outputs:
                raise AssertionError("test coordinator outputs exhausted")
            output = self._coordinator_outputs.pop(0)
        else:
            run_count = self.worker_runs.get(subtask.id, 0) + 1
            self.worker_runs[subtask.id] = run_count
            output = f"worker-{subtask.id}-run-{run_count}"
            self._persist_subtask_artifacts(
                subtask,
                output,
                execution_id=execution_id,
                plan_revision=plan_revision,
                current_wave=current_wave,
            )
        return AgentResult(
            subtask_id=subtask.id,
            tier=subtask.tier,
            model=subtask.model or subtask.tier,
            output=output,
            token_count=1,
        )

    def _execute_runtime_plan(
        self,
        runtime_plan: ExecutionPlan,
        *,
        declared_plan: ExecutionPlan | None = None,
        timeout: int = 120,
        router=None,
        task_id: str = "",
        budget_state=None,
        execution_id: str | None = None,
        plan_revision: int = 1,
        return_runtime_plan: bool = False,
    ):
        if runtime_plan.topology == "linear":
            self.linear_fallback_calls += 1
            if self._fail_linear:
                raise RuntimeError("linear fallback failed")
        return super()._execute_runtime_plan(
            runtime_plan,
            declared_plan=declared_plan,
            timeout=timeout,
            router=router,
            task_id=task_id,
            budget_state=budget_state,
            execution_id=execution_id,
            plan_revision=plan_revision,
            return_runtime_plan=return_runtime_plan,
        )


def _build_star_plan(*, max_rounds: int = 3) -> ExecutionPlan:
    return ExecutionPlan(
        analysis="star-loop",
        subtasks=[
            Subtask(
                id=1,
                stable_id="phase35-plan02-task01",
                description="coordinator",
                tier="low",
                model="low",
                depends_on=[],
                is_coordinator=True,
            ),
            Subtask(
                id=2,
                stable_id="phase35-plan02-task02",
                description="worker alpha",
                tier="low",
                model="low",
                depends_on=[1],
                produces=["summary"],
            ),
            Subtask(
                id=3,
                stable_id="phase35-plan02-task03",
                description="worker beta",
                tier="low",
                model="low",
                depends_on=[1],
                produces=["summary"],
            ),
            Subtask(
                id=4,
                stable_id="phase35-plan02-task04",
                description="worker gamma",
                tier="low",
                model="low",
                depends_on=[1],
                produces=["summary"],
            ),
        ],
        waves=[[1], [2, 3, 4]],
        total_agents=4,
        strategy="dag",
        topology="star",
        max_rounds=max_rounds,
        _topology_explicit=True,
    )


def _event_payloads(db: Database, swarm_id: str, event_type: str) -> list[dict[str, object]]:
    with db.conn() as conn:
        rows = conn.execute(
            """
            SELECT payload
            FROM swarm_events
            WHERE swarm_id = ? AND event_type = ?
            ORDER BY id
            """,
            (swarm_id, event_type),
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def _swarm_outcome_snapshot(
    db: Database,
    swarm_id: str,
) -> tuple[tuple[object, ...] | None, tuple[object, ...] | None, int]:
    with db.conn() as conn:
        outcome_row = conn.execute(
            """
            SELECT current_outcome, previous_outcome
            FROM routing_outcomes
            WHERE task_id = ?
            """,
            (swarm_id,),
        ).fetchone()
        telemetry_row = conn.execute(
            """
            SELECT selected_topology, coordinator_round_count,
                   artifact_consume_count, coordinator_amendment_count
            FROM telemetry
            WHERE task_hash = ? AND provider_name = 'swarm'
            ORDER BY ts DESC, id DESC
            LIMIT 1
            """,
            (swarm_id,),
        ).fetchone()
        telemetry_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM telemetry
            WHERE task_hash = ? AND provider_name = 'swarm'
            """,
            (swarm_id,),
        ).fetchone()[0]
    return outcome_row, telemetry_row, telemetry_count


def test_star_run_finishes_only_on_explicit_complete() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "star-complete.db")
        try:
            orchestrator = StarLoopOrchestrator(
                db,
                [json.dumps({"verdict": "complete", "synthesis": {"summary_text": "done"}})],
            )

            results = orchestrator.execute_plan(
                _build_star_plan(),
                execution_id="star-complete",
            )

            assert {result.subtask_id for result in results.values()} == {1, 2, 3, 4}
            assert orchestrator.linear_fallback_calls == 0
            assert orchestrator.worker_runs == {2: 1, 3: 1, 4: 1}
            checkpoints = db.list_coordinator_round_checkpoints("star-complete")
            assert len(checkpoints) == 1
            assert checkpoints[0]["verdict"] == "complete"
        finally:
            db.close()


def test_another_pass_reruns_only_changed_tasks_in_star_topology() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "star-another-pass.db")
        try:
            orchestrator = StarLoopOrchestrator(
                db,
                [
                    json.dumps(
                        {
                            "verdict": "another-pass",
                            "next_work": {
                                "rerun_subtasks": [2],
                            },
                            "synthesis": {"summary_text": "retry worker alpha"},
                        }
                    ),
                    json.dumps({"verdict": "complete", "synthesis": {"summary_text": "done"}}),
                ],
            )

            results = orchestrator.execute_plan(
                _build_star_plan(),
                execution_id="star-another-pass",
            )

            assert orchestrator.worker_runs[2] == 2
            assert orchestrator.worker_runs[3] == 1
            assert orchestrator.worker_runs[4] == 1
            assert results[3].output == "worker-3-run-1"
            checkpoints = db.list_coordinator_round_checkpoints("star-another-pass")
            assert len(checkpoints) == 2
            assert checkpoints[0]["verdict"] == "another-pass"
            assert checkpoints[1]["verdict"] == "complete"
        finally:
            db.close()


def test_another_pass_requires_explicit_guidance() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "star-guidance.db")
        try:
            orchestrator = StarLoopOrchestrator(
                db,
                [json.dumps({"verdict": "another-pass", "synthesis": {"summary_text": "retry"}})],
            )

            results = orchestrator.execute_plan(
                _build_star_plan(),
                execution_id="star-guidance",
            )

            assert {result.subtask_id for result in results.values()} == {1, 2, 3, 4}
            assert orchestrator.linear_fallback_calls == 1
            fallback_events = _event_payloads(db, "star-guidance", "star_linear_fallback")
            assert len(fallback_events) == 1
            assert "explicit amendment or next_work guidance" in str(fallback_events[0]["reason"])
        finally:
            db.close()


def test_malformed_payload_triggers_immediate_linear_fallback() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "star-malformed.db")
        try:
            orchestrator = StarLoopOrchestrator(db, ["not-json"])

            orchestrator.execute_plan(
                _build_star_plan(),
                execution_id="star-malformed",
            )

            assert orchestrator.linear_fallback_calls == 1
            fallback_events = _event_payloads(db, "star-malformed", "star_linear_fallback")
            assert len(fallback_events) == 1
            assert "malformed coordinator payload" in str(fallback_events[0]["reason"])
        finally:
            db.close()


def test_missing_verdict_triggers_immediate_linear_fallback() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "star-missing-verdict.db")
        try:
            orchestrator = StarLoopOrchestrator(
                db,
                [json.dumps({"synthesis": {"summary_text": "retry"}})],
            )

            orchestrator.execute_plan(
                _build_star_plan(),
                execution_id="star-missing-verdict",
            )

            assert orchestrator.linear_fallback_calls == 1
            fallback_events = _event_payloads(
                db,
                "star-missing-verdict",
                "star_linear_fallback",
            )
            assert len(fallback_events) == 1
            assert "invalid coordinator verdict: missing" in str(
                fallback_events[0]["reason"]
            )
        finally:
            db.close()


def test_max_rounds_exhaustion_falls_back_once() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "star-max-rounds.db")
        try:
            orchestrator = StarLoopOrchestrator(
                db,
                [
                    json.dumps(
                        {
                            "verdict": "another-pass",
                            "next_work": {
                                "rerun_subtasks": [2],
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "verdict": "another-pass",
                            "next_work": {
                                "rerun_subtasks": [2],
                            },
                        }
                    ),
                ],
                fail_linear=False,
            )

            orchestrator.execute_plan(
                _build_star_plan(max_rounds=2),
                execution_id="star-max-rounds",
            )

            assert orchestrator.coordinator_runs == 2
            assert orchestrator.linear_fallback_calls == 1
            fallback_events = _event_payloads(db, "star-max-rounds", "star_linear_fallback")
            assert len(fallback_events) == 1
            assert "max_rounds exhausted" in str(fallback_events[0]["reason"])
        finally:
            db.close()


def test_star_plan_mutation_request_triggers_linear_fallback() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "star-plan-mutation.db")
        try:
            orchestrator = StarLoopOrchestrator(
                db,
                [
                    json.dumps(
                        {
                            "verdict": "another-pass",
                            "amendment": {
                                "subtask_updates": [
                                    {"id": 2, "description": "worker alpha retry"},
                                ]
                            },
                        }
                    )
                ],
            )

            results = orchestrator.execute_plan(
                _build_star_plan(),
                execution_id="star-plan-mutation",
            )

            assert {result.subtask_id for result in results.values()} == {1, 2, 3, 4}
            assert orchestrator.linear_fallback_calls == 1
            fallback_events = _event_payloads(db, "star-plan-mutation", "star_linear_fallback")
            assert len(fallback_events) == 1
            assert "must use next_work only" in str(fallback_events[0]["reason"])
        finally:
            db.close()


def test_summary_context_coerces_non_numeric_length_chars() -> None:
    summary_context = Orchestrator._summary_context_from_artifacts(
        [
            {
                "artifact_type": "summary",
                "producer_subtask_id": "2",
                "stable_ref": "artifact-2",
                "compact_summary": {
                    "summary_text": "abc",
                    "length_chars": "not-a-number",
                },
            }
        ],
        current_round=2,
    )

    assert '"length_chars":3' in summary_context
    assert '"untrusted_summary_text":"abc"' in summary_context


def test_summary_context_includes_successful_worker_results() -> None:
    summary_context = Orchestrator._summary_context_from_results(
        {
            2: AgentResult(
                subtask_id=2,
                tier="low",
                model="gpt-5.5",
                output="def slugify(text): return text",
                provider_name="CodexProvider",
                token_count=8,
            ),
            3: AgentResult(
                subtask_id=3,
                tier="low",
                model="gpt-5.5",
                output="(no output)",
                provider_name="CodexProvider",
                token_count=2,
                success=False,
            ),
        },
        current_round=1,
    )

    assert "UNTRUSTED_WORKER_RESULTS_JSON" in summary_context
    assert '"subtask_id":2' in summary_context
    assert "def slugify" in summary_context
    assert '"subtask_id":3' not in summary_context


def test_star_linear_fallback_skips_empty_worker_plan() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "star-no-workers.db")
        try:
            orchestrator = StarLoopOrchestrator(db, ["not-json"])
            plan = ExecutionPlan(
                analysis="star-no-workers",
                subtasks=[
                    Subtask(
                        id=1,
                        stable_id="phase35-plan02-task01",
                        description="coordinator",
                        tier="low",
                        model="low",
                        depends_on=[],
                        is_coordinator=True,
                    )
                ],
                waves=[[1]],
                total_agents=1,
                strategy="dag",
                topology="star",
                max_rounds=1,
                _topology_explicit=True,
            )

            results = orchestrator.execute_plan(
                plan,
                execution_id="star-no-workers",
            )

            assert {result.subtask_id for result in results.values()} == {1}
            assert orchestrator.linear_fallback_calls == 0
            fallback_events = _event_payloads(db, "star-no-workers", "star_linear_fallback")
            assert len(fallback_events) == 1
            assert fallback_events[0]["skipped"] is True
            assert fallback_events[0]["worker_count"] == 0
        finally:
            db.close()


def test_validate_routed_subtask_wraps_provider_resolution_errors() -> None:
    orchestrator = Orchestrator(TGsConfig(), BrokenProvider(), DummyPlanner())

    with pytest.raises(ValueError, match="Failed to resolve provider model"):
        orchestrator._validate_routed_subtask(
            Subtask(
                id=99,
                stable_id="phase35-plan02-task99",
                description="broken provider lookup",
                tier="low",
                model="low",
            )
        )


def test_latest_artifacts_for_execution_falls_back_without_window_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "star-window-fallback.db")
        try:
            orchestrator = Orchestrator(TGsConfig(), DummyProvider(), DummyPlanner(), db=db)
            subtask = Subtask(
                id=2,
                stable_id="phase35-plan02-task02",
                description="worker alpha",
                tier="low",
                model="low",
                produces=["summary"],
            )
            orchestrator._persist_subtask_artifacts(
                subtask,
                "worker-output",
                execution_id="star-window-fallback",
                plan_revision=1,
                current_wave=1,
            )

            original_conn = db.conn

            @contextmanager
            def fallback_conn():
                with original_conn() as conn:
                    class ConnectionWrapper:
                        def __init__(self, inner) -> None:
                            self._inner = inner

                        def execute(self, query, params=()):
                            if "ROW_NUMBER() OVER" in query:
                                raise sqlite3.OperationalError("window functions disabled")
                            return self._inner.execute(query, params)

                        def __getattr__(self, name: str):
                            return getattr(self._inner, name)

                    yield ConnectionWrapper(conn)

            monkeypatch.setattr(db, "conn", fallback_conn)

            artifacts = orchestrator._latest_artifacts_for_execution(
                "star-window-fallback",
                1,
            )

            assert len(artifacts) == 1
            assert artifacts[0]["artifact_type"] == "summary"
            assert artifacts[0]["producer_subtask_id"] == "2"
        finally:
            db.close()


def test_fallback_failure_marks_run_failed() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "star-fallback-failed.db")
        try:
            orchestrator = StarLoopOrchestrator(
                db,
                ["not-json"],
                fail_linear=True,
            )

            with pytest.raises(RuntimeError, match="linear fallback failed"):
                orchestrator.execute_plan(
                    _build_star_plan(),
                    execution_id="star-fallback-failed",
                )

            fallback_failures = _event_payloads(
                db,
                "star-fallback-failed",
                "star_linear_fallback_failed",
            )
            assert len(fallback_failures) == 1
            assert fallback_failures[0]["error"] == "linear fallback failed"
        finally:
            db.close()


def test_complete_run_records_swarm_outcome_signal() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "star-complete-outcome.db")
        try:
            orchestrator = StarLoopOrchestrator(
                db,
                [json.dumps({"verdict": "complete", "synthesis": {"summary_text": "done"}})],
            )

            orchestrator.execute_plan(
                _build_star_plan(),
                execution_id="star-complete-outcome",
            )

            outcome_row, telemetry_row, telemetry_count = _swarm_outcome_snapshot(
                db,
                "star-complete-outcome",
            )
            assert outcome_row == ("accepted", None)
            assert telemetry_row == ("star", 1, 3, 0)
            assert telemetry_count == 1
        finally:
            db.close()


def test_successful_fallback_records_revised_swarm_outcome() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "star-fallback-outcome.db")
        try:
            orchestrator = StarLoopOrchestrator(
                db,
                [json.dumps({"verdict": "fallback", "fallback_reason": "need linear takeover"})],
            )

            orchestrator.execute_plan(
                _build_star_plan(),
                execution_id="star-fallback-outcome",
            )

            outcome_row, telemetry_row, telemetry_count = _swarm_outcome_snapshot(
                db,
                "star-fallback-outcome",
            )
            assert outcome_row == ("revised", None)
            assert telemetry_row == ("star", 1, 3, 0)
            assert telemetry_count == 1
        finally:
            db.close()


def test_failed_fallback_records_rejected_swarm_outcome() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "star-fallback-rejected.db")
        try:
            orchestrator = StarLoopOrchestrator(
                db,
                [json.dumps({"verdict": "fallback", "fallback_reason": "need linear takeover"})],
                fail_linear=True,
            )

            with pytest.raises(RuntimeError, match="linear fallback failed"):
                orchestrator.execute_plan(
                    _build_star_plan(),
                    execution_id="star-fallback-rejected",
                )

            outcome_row, telemetry_row, telemetry_count = _swarm_outcome_snapshot(
                db,
                "star-fallback-rejected",
            )
            assert outcome_row == ("rejected", None)
            assert telemetry_row == ("star", 1, 3, 0)
            assert telemetry_count == 1
        finally:
            db.close()
