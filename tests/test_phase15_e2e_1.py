"""Phase 15 - representative multi-wave end-to-end: artifact publish/consume and urgency/fanout visibility
"""
from __future__ import annotations

import pytest
from tests.helpers_phase15 import run_stubbed_execute_wave


def test_multiwave_artifact_and_urgency_path(tmp_path):
    """Representative multi-wave scenario asserting telemetry explainability fields
    and artifact publish counts are written per-agent.
    """
    db_path = tmp_path / "phase15_e2e_1.db"
    # Run a stubbed execute wave that writes Phase 15 telemetry fields
    db, _rows = run_stubbed_execute_wave(db_path, urgency=0.7, topology="star")
    try:
        with db.conn() as conn:
            rows = conn.execute(
                "SELECT urgency_score, selected_topology, artifact_publish_count FROM telemetry WHERE session_id = ?",
                ("wave-test",),
            ).fetchall()
        # Expect one telemetry row per subtask (3)
        assert len(rows) == 3
        # Check the values written for each row
        for urgency_score, selected_topology, publish_count in rows:
            assert abs(urgency_score - 0.7) < 1e-6
            assert selected_topology == "star"
            assert publish_count == 1
    finally:
        db.close()
