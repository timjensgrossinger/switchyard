#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.config import TGsConfig
from shared.db import Database
from shared.plan_cache import (
    PLAN_CACHE_HIT,
    PLAN_CACHE_MISS,
    build_plan_cache_summary,
)
from shared.planner import CLIBackend, Planner
from shared.status import build_status_snapshot


class MockPlannerBackend(CLIBackend):
    def __init__(self, response: str | None = None) -> None:
        self._response = response
        self.prompts: list[str] = []

    def call(self, prompt: str, model: str | None = None, timeout: int = 120) -> str | None:
        self.prompts.append(prompt)
        return self._response


def _sample_plan_json() -> str:
    return (
        "<PLAN_JSON>\n"
        + json.dumps(
            {
                "analysis": "test",
                "subtasks": [
                    {"id": 1, "description": "do thing", "tier": "low", "depends_on": []},
                ],
                "strategy": "parallel",
            }
        )
        + "\n</PLAN_JSON>"
    )


def test_plan_cache_hit_logs_planner_telemetry(tmp_path: Path) -> None:
    db_path = tmp_path / "plan-cache-hit.db"
    db = Database(db_path=db_path)
    cached_plan = {
        "analysis": "cached",
        "subtasks": [{"id": 1, "description": "cached work", "tier": "low", "depends_on": []}],
        "strategy": "parallel",
        "token_estimate": {"planner_total": 900},
    }
    db.plan_put("cached decomposition task", cached_plan, "sonnet")
    backend = MockPlannerBackend("should-not-run")
    planner = Planner(TGsConfig(db_path=db_path), backend, db)

    plan = planner.plan("cached decomposition task")

    assert plan.cache_hit is True
    assert backend.prompts == []

    with db.conn() as conn:
        row = conn.execute(
            """
            SELECT reason, estimated_tokens
            FROM telemetry
            WHERE version = 'planner'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row is not None
    assert row[0] == PLAN_CACHE_HIT
    assert row[1] == 900


def test_plan_cache_miss_logs_before_planner_call(tmp_path: Path) -> None:
    db_path = tmp_path / "plan-cache-miss.db"
    db = Database(db_path=db_path)
    backend = MockPlannerBackend(_sample_plan_json())
    planner = Planner(TGsConfig(db_path=db_path), backend, db)

    planner.plan("fresh decomposition task", skip_cache=False)

    with db.conn() as conn:
        rows = conn.execute(
            """
            SELECT reason
            FROM telemetry
            WHERE version = 'planner'
            ORDER BY id ASC
            """
        ).fetchall()

    reasons = [row[0] for row in rows]
    assert PLAN_CACHE_MISS in reasons
    assert "planner_plan" in reasons


def test_status_snapshot_includes_plan_cache_summary(tmp_path: Path) -> None:
    db_path = tmp_path / "plan-cache-status.db"
    db = Database(db_path=db_path)
    cfg = TGsConfig(db_path=db_path)
    db.log_agent_result(
        session_id="planner",
        task_hash="task-hash",
        agent_id=0,
        tier="medium",
        model="planner-model",
        success=True,
        estimated_tokens=640,
        actual_tokens=0,
        timing_ms=0,
        reason=PLAN_CACHE_HIT,
        version="planner",
    )

    snapshot = build_status_snapshot(cfg, db, str(tmp_path))

    summary = snapshot["plan_cache_summary"]
    assert summary["hits"] == 1
    assert summary["estimated_planner_tokens_saved"] == 640
    assert summary["entries"] == 0
    assert summary["hit_rate_pct"] == 100.0


def test_build_plan_cache_summary_aggregates_counters(tmp_path: Path) -> None:
    db_path = tmp_path / "plan-cache-summary.db"
    db = Database(db_path=db_path)
    db.log_agent_result(
        session_id="planner",
        task_hash="hit",
        agent_id=0,
        tier="medium",
        model="planner-model",
        success=True,
        estimated_tokens=100,
        actual_tokens=0,
        timing_ms=0,
        reason=PLAN_CACHE_HIT,
        version="planner",
    )
    db.log_agent_result(
        session_id="planner",
        task_hash="miss",
        agent_id=0,
        tier="medium",
        model="planner-model",
        success=True,
        estimated_tokens=0,
        actual_tokens=0,
        timing_ms=0,
        reason=PLAN_CACHE_MISS,
        version="planner",
    )

    summary = build_plan_cache_summary(db)

    assert summary["hits"] == 1
    assert summary["misses"] == 1
    assert summary["estimated_planner_tokens_saved"] == 100
    assert summary["hit_rate_pct"] == 50.0
