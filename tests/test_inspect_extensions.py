import json
from pathlib import Path

import pytest

from shared import db as db_mod
import mcp_server


def _make_test_db(path: Path):
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass
    return db_mod.Database(db_path=path)


def test_inspect_task_explainability_fields(tmp_path):
    task_id = "test-task-123"
    db_path = tmp_path / "telemetry.db"
    db = _make_test_db(db_path)

    # Insert a telemetry row with parse_diagnostics JSON and explainability columns
    extras = {"note": "important event", "details": {"x": 1}}
    db.write_telemetry_row(
        session_id="test",
        task_hash=task_id,
        agent_id=1,
        tier="low",
        model="test-model",
        urgency_score=0.95,
        selected_topology="linear",
        fanout_final_action="none",
        artifact_publish_count=2,
        artifact_consume_count=3,
        coordinator_amendment_count=1,
        parse_diagnostics=json.dumps(extras),
        reason="unit-test",
        version="test",
    )

    # Ensure mcp_server uses our test DB instance
    class _DummyCfg:
        class parallelism:
            max_workers = 4
            enabled = False
        class budgets:
            default_hard_cap_tokens = 10000
            default_soft_warning_pct = 0.25
        planner_model = "test"
        planner_timeout = 120
    mcp_server._ensure_init = lambda: (_DummyCfg(), db, None, None, None)

    resp = mcp_server.inspect_task(task_id)

    assert resp.get("task_id") == task_id
    assert isinstance(resp.get("events"), list)
    assert len(resp["events"]) >= 1
    ev = resp["events"][0]
    assert "explainability" in ev
    explain = ev["explainability"]
    assert explain.get("urgency_score") == 0.95
    assert explain.get("selected_topology") == "linear"
    assert explain.get("artifact_publish_count") == 2
    assert explain.get("coordinator_amendment_count") == 1
    assert "extras" in explain and explain["extras"].get("note") == "important event"


def test_inspect_status_recent_summary(tmp_path):
    # Use same DB file to ensure telemetry rows are visible to inspect_status
    db_path = tmp_path / "telemetry.db"
    db = _make_test_db(db_path)

    # Seed a telemetry row so aggregates are non-zero
    db.write_telemetry_row(
        session_id="test",
        task_hash="x",
        agent_id=0,
        tier="system",
        model="",
        artifact_publish_count=5,
        artifact_consume_count=7,
        coordinator_amendment_count=2,
        urgency_score=0.42,
        parse_diagnostics=json.dumps({"note": "ok"}),
    )

    # Ensure mcp_server uses our test DB instance
    class _DummyCfg:
        class parallelism:
            max_workers = 4
            enabled = False
        class budgets:
            default_hard_cap_tokens = 10000
            default_soft_warning_pct = 0.25
        planner_model = "test"
        planner_timeout = 120
    mcp_server._ensure_init = lambda: (_DummyCfg(), db, None, None, None)

    resp = mcp_server.inspect_status("")
    assert "recent_summary" in resp
    recent = resp["recent_summary"]
    assert isinstance(recent.get("artifact_publish_count"), int)
    assert isinstance(recent.get("artifact_consume_count"), int)
    assert isinstance(recent.get("coordinator_amendment_count"), int)
    assert "latest_notable_event" in recent
