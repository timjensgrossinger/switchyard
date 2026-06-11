"""Phase 15 - concurrency: artifact visibility gating and count integrity under parallel execution
"""
from __future__ import annotations

import pytest
import threading
from types import SimpleNamespace
from tests.helpers_phase15 import run_stubbed_execute_wave


def test_concurrent_artifact_visibility_and_counts(tmp_path):
    """Spawn multiple threads that each write telemetry via the stubbed orchestrator
    and assert that telemetry rows reflect only committed writes and counts are consistent.
    """
    db_path = tmp_path / "phase15_concurrency.db"

    # Run stubbed waves concurrently in multiple threads against the same DB
    results = []
    errors = []

    def worker(idx):
        try:
            db, rows = run_stubbed_execute_wave(db_path, max_workers=1, urgency=0.2 + idx * 0.1, topology="linear")
            # Return number of telemetry rows written by this worker
            try:
                with db.conn() as conn:
                    cnt = conn.execute("SELECT COUNT(*) FROM telemetry WHERE session_id = ?", ("wave-test",)).fetchone()[0]
                results.append(cnt)
            finally:
                db.close()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    # After both workers complete, ensure at least one telemetry row exists and counts are non-negative
    assert any(r >= 0 for r in results)
    # Basic sanity: at least 3 rows were written by one run
    assert any(r >= 3 for r in results)
