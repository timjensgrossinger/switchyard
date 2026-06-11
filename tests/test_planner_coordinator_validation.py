#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import TGsConfig
from shared.db import Database
from shared.planner import (
    CLIBackend,
    Planner,
    PlannerParseError,
)


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
    planner._phase13_tempdir = tempdir
    return planner


def test_single_coordinator_validation() -> None:
    planner = _planner()

    plan = planner._build_plan(
        {
            "analysis": "coordinator validation",
            "subtasks": [
                {
                    "id": 1,
                    "description": "inspect prior artifacts",
                    "tier": "low",
                    "depends_on": [],
                    "is_coordinator": True,
                },
                {
                    "id": 2,
                    "description": "run worker task",
                    "tier": "low",
                    "depends_on": [1],
                },
            ],
            "strategy": "dag",
        },
        "fallback task",
    )

    assert plan.subtasks[0].is_coordinator is True


def test_duplicate_coordinators_in_wave_rejected() -> None:
    planner = _planner()

    with pytest.raises(PlannerParseError, match="D-01/D-02"):
        planner._build_plan(
            {
                "analysis": "coordinator validation",
                "subtasks": [
                    {
                        "id": 1,
                        "description": "first coordinator",
                        "tier": "low",
                        "depends_on": [],
                        "is_coordinator": True,
                    },
                    {
                        "id": 2,
                        "description": "second coordinator",
                        "tier": "low",
                        "depends_on": [],
                        "is_coordinator": True,
                    },
                ],
                "strategy": "parallel",
            },
            "fallback task",
        )
