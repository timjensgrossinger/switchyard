#!/usr/bin/env python3
"""
Concurrency tests for shared/db.py.
"""
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shared.db


def _exercise_concurrent_writes() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = shared.db.Database(Path(tmpdir) / "concurrency.db")
        failures: list[Exception] = []
        failure_lock = threading.Lock()
        worker_count = 8
        iterations = 25

        def worker(worker_id: int) -> None:
            for index in range(iterations):
                try:
                    db.cache_put(
                        f"task-{worker_id}-{index}",
                        "result",
                        "gpt-5-mini",
                    )
                    db.log_agent_result(
                        session_id=f"session-{worker_id}",
                        task_hash=f"hash-{worker_id}-{index}",
                        agent_id=index,
                        tier="low",
                        model="gpt-5-mini",
                        tokens_used=index,
                    )
                except Exception as exc:  # pragma: no cover - assertion below reports failures
                    with failure_lock:
                        failures.append(exc)
                    return

        threads = [
            threading.Thread(target=worker, args=(worker_id,))
            for worker_id in range(worker_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        if failures:
            first = failures[0]
            raise AssertionError(
                f"concurrent writes raised {type(first).__name__}: {first}"
            )

        with db.conn() as conn:
            cache_count = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            telemetry_count = conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]

        assert cache_count == worker_count * iterations
        assert telemetry_count == worker_count * iterations
        db.close()


# Wave-0 validation guard: each worker must get its own DB connection under load.
def test_connection_per_thread() -> None:
    """Concurrent cache and telemetry writes should not share one SQLite connection."""
    _exercise_concurrent_writes()


def test_artifact_visibility_scoping() -> None:
    """Artifacts should be visible across DB instances and scoped by revision and wave."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "artifacts.db"
        first = shared.db.Database(db_path)
        second = shared.db.Database(db_path)
        try:
            first_ref = first.save_artifact(
                execution_id="exec-1",
                plan_revision=1,
                wave=1,
                subtask_id="12-01",
                artifact_type="summary",
                full_payload="payload-one",
                compact_summary="summary-one",
            )
            second_ref = first.save_artifact(
                execution_id="exec-1",
                plan_revision=2,
                wave=2,
                subtask_id="12-01",
                artifact_type="summary",
                full_payload="payload-two",
                compact_summary="summary-two",
            )

            visible = second.query_artifacts("exec-1", 1, wave=1, artifact_types=["summary"])
            assert len(visible) == 1
            assert visible[0]["stable_ref"] == first_ref
            assert visible[0]["compact_summary"]["artifact_ref"] == first_ref

            hidden = second.query_artifacts("exec-1", 1, wave=2, artifact_types=["summary"])
            assert hidden == []

            other_revision = second.query_artifacts("exec-1", 2, wave=2, artifact_types=["summary"])
            assert len(other_revision) == 1
            assert other_revision[0]["stable_ref"] == second_ref
            assert first._get_full_payload(second_ref) == "payload-two"
        finally:
            first.close()
            second.close()
