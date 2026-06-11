#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import TGsConfig
from shared.db import Database
from shared.host_learning import register_host_run_handoff
from shared.host_plan_expand import expand_host_plan


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "host-plan-expand.db")
    database._init_schema(database._get_connection())
    yield database
    database.close()


def test_expand_host_plan_adds_parallel_wave(db: Database) -> None:
    run_id = "swarm-expand-1"
    register_host_run_handoff(
        db,
        run_id=run_id,
        host_spawn_waves=[
            {
                "wave": 1,
                "agents": [
                    {
                        "id": "1",
                        "tier": "low",
                        "model": "test",
                        "prompt": "scaffold contract",
                        "target_files": ["openapi.yaml"],
                    },
                ],
            }
        ],
        planned_subtasks=1,
        workspace_root="/tmp/project",
        topology="dag",
        task_hint="build todo app",
    )
    db.persist_swarm_run(
        {
            "swarm_id": run_id,
            "status": "running",
            "requested_agents": 1,
            "effective_agents": 1,
            "resume_status": "running",
        }
    )
    result = expand_host_plan(
        db,
        run_id=run_id,
        discovered_files=[
            "app.py",
            "templates/index.html",
            "static/js/app.js",
        ],
        workspace_root="/tmp/project",
        config=TGsConfig(),
        caller="cursor",
    )
    assert result["expanded"] is True
    waves = result.get("host_spawn_waves")
    assert isinstance(waves, list) and len(waves) >= 1
    agent_count = sum(
        len(w.get("agents", []))
        for w in waves
        if isinstance(w, dict) and isinstance(w.get("agents"), list)
    )
    assert agent_count == 3
    snapshots = db.get_handoff_agent_snapshots(run_id)
    assert len(snapshots) == 4


def test_expand_host_plan_skips_already_assigned_files(db: Database) -> None:
    run_id = "swarm-expand-2"
    register_host_run_handoff(
        db,
        run_id=run_id,
        host_spawn_waves=[
            {
                "wave": 1,
                "agents": [
                    {
                        "id": "1",
                        "tier": "low",
                        "model": "test",
                        "prompt": "create app.py",
                        "target_files": ["app.py"],
                    },
                ],
            }
        ],
        planned_subtasks=1,
        workspace_root="/tmp/project",
    )
    db.persist_swarm_run(
        {
            "swarm_id": run_id,
            "status": "running",
            "resume_status": "running",
        }
    )
    result = expand_host_plan(
        db,
        run_id=run_id,
        discovered_files=["app.py", "style.css"],
        workspace_root="/tmp/project",
        config=TGsConfig(),
    )
    assert result["expanded"] is True
    assert result.get("new_files") == ["style.css"]
