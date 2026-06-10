#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import TGsConfig
from shared.db import Database
from shared.planner import CLIBackend, ExecutionPlan, Planner, Subtask


class DummyBackend(CLIBackend):
    def call(
        self, prompt: str, model: str | None = None, timeout: int = 120
    ) -> str | None:
        return None


def _planner() -> Planner:
    tempdir = tempfile.TemporaryDirectory()
    db_path = Path(tempdir.name) / "planner.db"
    planner = Planner(
        TGsConfig(db_path=db_path),
        DummyBackend(),
        Database(db_path=db_path),
    )
    planner._phase32_tempdir = tempdir
    return planner


def test_stable_ids_deterministic() -> None:
    planner = _planner()
    parsed = {
        "phase_number": "32",
        "plan_number": "1",
        "analysis": "stable ids",
        "subtasks": [
            {"id": 1, "description": "define schema", "tier": "low", "model": "low"},
            {
                "id": 2,
                "description": "serialize fields",
                "tier": "medium",
                "model": "medium",
                "depends_on": [1],
            },
        ],
        "strategy": "dag",
    }

    first = planner._build_plan(parsed, "phase 32")
    second = planner._build_plan(parsed, "phase 32")

    assert [st.stable_id for st in first.subtasks] == [
        "phase32-plan01-task01",
        "phase32-plan01-task02",
    ]
    assert [st.stable_id for st in first.subtasks] == [
        st.stable_id for st in second.subtasks
    ]


def test_plan_to_dict_includes_topology_and_max_rounds() -> None:
    plan = ExecutionPlan(
        analysis="serialize",
        subtasks=[
            Subtask(
                id=1,
                stable_id="phase32-plan01-task01",
                description="define schema",
                tier="low",
                model="low",
            )
        ],
        waves=[[1]],
        total_agents=1,
        strategy="parallel",
    )

    serialized = Planner.plan_to_dict(plan)

    assert serialized["topology"] == "dag"
    assert serialized["max_rounds"] == 3
    assert serialized["subtasks"][0]["stable_id"] == "phase32-plan01-task01"
