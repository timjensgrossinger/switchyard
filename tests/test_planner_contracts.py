#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import TGsConfig
from shared.db import Database
from shared.planner import CLIBackend, Planner, PlannerParseError


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
    planner._phase11_tempdir = tempdir
    return planner


def test_topology_default_dag() -> None:
    planner = _planner()

    plan = planner._build_plan(
        {
            "analysis": "contracts",
            "subtasks": [
                {
                    "id": 1,
                    "description": "define metadata",
                    "tier": "low",
                    "model": "low",
                    "depends_on": [],
                }
            ],
            "strategy": "parallel",
        },
        "fallback task",
    )

    assert plan.topology == "dag"
    assert plan.max_rounds == 3
    assert plan.subtasks[0].stable_id == "phase00-plan01-task01"
    serialized = planner.plan_to_dict(plan)
    assert serialized["topology"] == "dag"
    assert serialized["max_rounds"] == 3
    assert serialized["subtasks"][0]["stable_id"] == "phase00-plan01-task01"


def test_roundtrip_serialization() -> None:
    planner = _planner()
    parsed = {
        "analysis": "contracts",
        "topology": "star",
        "subtasks": [
            {
                "id": 1,
                "description": "produce artifact metadata",
                "tier": "low",
                "model": "claude-haiku-4.5",
                "provider": "Claude Code",
                "provider_id": "claude-code",
                "depends_on": [],
                "consumes": "plan-outline",
                "produces": ["typed-artifact"],
                "is_coordinator": "true",
            }
        ],
        "strategy": "parallel",
    }

    plan = planner._build_plan(parsed, "fallback task")
    assert plan.topology == "star"
    assert plan.subtasks[0].consumes == ["plan-outline"]
    assert plan.subtasks[0].produces == ["typed-artifact"]
    assert plan.subtasks[0].is_coordinator is True
    assert plan.subtasks[0].model == "claude-haiku-4.5"
    assert plan.subtasks[0].provider == "Claude Code"
    assert plan.subtasks[0].provider_id == "claude-code"

    serialized = planner.plan_to_dict(plan)
    assert serialized["topology"] == "star"
    assert serialized["max_rounds"] == 3
    assert serialized["subtasks"][0]["stable_id"] == "phase00-plan01-task01"
    assert serialized["subtasks"][0]["model"] == "claude-haiku-4.5"
    assert serialized["subtasks"][0]["provider"] == "Claude Code"
    assert serialized["subtasks"][0]["provider_id"] == "claude-code"
    assert serialized["subtasks"][0]["consumes"] == ["plan-outline"]
    assert serialized["subtasks"][0]["produces"] == ["typed-artifact"]
    assert serialized["subtasks"][0]["is_coordinator"] is True


def test_local_contradiction_parse_error() -> None:
    planner = _planner()

    with pytest.raises(PlannerParseError, match="TOPO-11-001"):
        planner._build_plan(
            {
                "subtasks": [
                    {
                        "id": 1,
                        "description": "conflicting metadata",
                        "tier": "low",
                        "model": "low",
                        "depends_on": [],
                        "consumes": ["artifact-a"],
                        "produces": ["artifact-a"],
                    }
                ],
                "strategy": "parallel",
            },
            "fallback task",
        )
