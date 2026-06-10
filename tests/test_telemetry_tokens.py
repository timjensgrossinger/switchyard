#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.config import TGsConfig
from shared.db import Database
from shared.planner import CLIBackend, Planner


class MockPlannerBackend(CLIBackend):
    def __init__(self, response: str, actual_tokens: int) -> None:
        self._response = response
        self.last_actual_tokens = actual_tokens

    def call(self, prompt: str, model: str | None = None, timeout: int = 120) -> str | None:
        return self._response


def test_estimated_and_actual_tokens_persist(tmp_path: Path) -> None:
    """Planner telemetry persists estimated/actual tokens and timing to an isolated DB."""
    db_path = tmp_path / "telemetry.db"
    db = Database(db_path=db_path)
    backend = MockPlannerBackend(
        "<PLAN_JSON>\n"
        + json.dumps({
            "analysis": "test",
            "subtasks": [{"id": 1, "description": "do thing", "tier": "low", "depends_on": []}],
            "strategy": "parallel",
        })
        + "\n</PLAN_JSON>",
        actual_tokens=42,
    )
    planner = Planner(TGsConfig(db_path=db_path), backend, db)

    planner.plan("plan with telemetry", skip_cache=True)

    with db.conn() as conn:
        row = conn.execute(
            "SELECT estimated_tokens, actual_tokens, timing_ms, rework_count "
            "FROM telemetry ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert row is not None
    assert row[0] and row[0] > 0
    assert row[1] == 42
    assert row[2] is not None and row[2] >= 0
    assert row[3] == 0
