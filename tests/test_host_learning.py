#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import TGsConfig
from shared.db import Database
from shared.host_learning import (
    host_task_id,
    ingest_host_wave,
    inspect_host_swarm,
    plan_run_id,
    register_host_run_handoff,
)
from shared.router import TaskRouter


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "host-learning.db")
    database._init_schema(database._get_connection())
    yield database
    database.close()


def test_plan_run_id_is_stable() -> None:
    assert plan_run_id("hello") == plan_run_id("hello")
    assert plan_run_id("hello") != plan_run_id("world")


def test_register_host_run_handoff_adds_task_ids(db: Database) -> None:
    run_id = "swarm-test-1"
    waves = [
        {
            "wave": 1,
            "agents": [
                {"id": "1", "tier": "low", "model": "test-model", "prompt": "create a.py"},
                {"id": "2", "tier": "low", "model": "test-model", "prompt": "create b.py"},
            ],
        }
    ]
    register_host_run_handoff(
        db,
        run_id=run_id,
        host_spawn_waves=waves,
        planned_subtasks=2,
        workspace_root="/tmp/project",
    )
    assert waves[0]["agents"][0]["task_id"] == host_task_id(run_id, "1")
    assert waves[0]["agents"][1]["task_id"] == host_task_id(run_id, "2")

    with db.conn() as conn:
        telemetry_count = conn.execute(
            "SELECT COUNT(*) FROM telemetry WHERE session_id = ?",
            (run_id,),
        ).fetchone()[0]
        worker_count = conn.execute(
            "SELECT COUNT(*) FROM swarm_workers WHERE swarm_id = ?",
            (run_id,),
        ).fetchone()[0]
    assert telemetry_count == 2
    assert worker_count == 2


def test_ingest_host_wave_tracks_patterns_and_finalizes(db: Database) -> None:
    run_id = "swarm-test-2"
    waves = [
        {
            "wave": 1,
            "agents": [
                {"id": "1", "tier": "low", "model": "test-model", "prompt": "create greet.py"},
            ],
        }
    ]
    register_host_run_handoff(
        db,
        run_id=run_id,
        host_spawn_waves=waves,
        planned_subtasks=1,
        workspace_root="/tmp/project",
    )
    db.persist_swarm_run(
        {
            "swarm_id": run_id,
            "status": "awaiting_host_execution",
            "requested_agents": 1,
            "effective_agents": 1,
        }
    )
    router = TaskRouter(TGsConfig(), db=db)
    router.enable_learning("/tmp/project")

    result = ingest_host_wave(
        db,
        run_id=run_id,
        wave_index=1,
        agents=[
            {
                "spawn_id": "1",
                "task_id": host_task_id(run_id, "1"),
                "success": True,
                "touched_files": ["greet.py"],
                "output_excerpt": "created greet.py",
            }
        ],
        workspace_root="/tmp/project",
        terminal=True,
        outcome="accepted",
        config=TGsConfig(),
        router=router,
    )
    assert result["agents_recorded"] == 1
    assert result["finalize"]["status"] == "completed"

    with db.conn() as conn:
        pattern_count = conn.execute("SELECT COUNT(*) FROM subtask_patterns").fetchone()[0]
        outcome_count = conn.execute(
            "SELECT COUNT(*) FROM routing_outcomes WHERE task_id = ?",
            (run_id,),
        ).fetchone()[0]
    assert pattern_count >= 1
    assert outcome_count == 1

    assert db.routing_guard_has_executions(caller="mcp", cwd="/tmp/project") is True

    summary = inspect_host_swarm(db, run_id)
    assert summary is not None
    assert summary.get("status") in {"completed", "running", "awaiting_host_execution"}
