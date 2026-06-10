#!/usr/bin/env python3
"""
Tests for conservative fan-out behavior in shared/orchestrator.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shared.orchestrator as orchestrator_module
from shared.db import Database


class FakeOrchestrator:
    def __init__(self, output: str = "success", tokens: int = 10) -> None:
        self.output = output
        self.tokens = tokens
        self.executed: list[str] = []

    def execute_subtask(self, subtask, timeout: int = 120):
        self.executed.append(subtask.description)
        return orchestrator_module.AgentResult(
            subtask_id=subtask.id,
            tier=subtask.tier,
            model="test-model",
            output=self.output,
            token_count=self.tokens,
            success=True,
        )


def test_fanout_requires_opt_in() -> None:
    with pytest.raises(orchestrator_module.FanOutNotEnabled):
        orchestrator_module.fan_out_task({"domains": []})


def test_fanout_caps_enforced_selects_only_max_routers() -> None:
    task = {
        "opt_in_fanout": True,
        "description": "split auth, db, api, and docs",
        "domains": [
            {"name": "auth", "confidence": 0.98},
            {"name": "db", "confidence": 0.95},
            {"name": "api", "confidence": 0.92},
            {"name": "docs", "confidence": 0.90},
        ],
    }
    fake = FakeOrchestrator()

    result = orchestrator_module.fan_out_task(task, max_routers=3, orchestrator=fake)

    assert len(result["per_domain"]) == 3
    assert len(fake.executed) == 3
    assert [entry["domain"] for entry in result["per_domain"]] == ["auth", "db", "api"]


def test_fanout_fallback_single_route_when_confidence_is_weak() -> None:
    task = {
        "opt_in_fanout": True,
        "description": "maybe split this",
        "domains": [
            {"name": "auth", "confidence": 0.60},
            {"name": "db", "confidence": 0.74},
        ],
    }

    result = orchestrator_module.fan_out_task(task)

    assert result["fallback"] == "single_route"
    assert result["per_domain"] == []


def test_reconcile_prefers_highest_confidence_and_tracks_conflicts() -> None:
    result = orchestrator_module.reconcile_fanout_results(
        [
            {
                "domain": "auth",
                "confidence": 0.99,
                "output": "failed answer",
                "budget_used": 3,
                "success": False,
            },
            {
                "domain": "db",
                "confidence": 0.95,
                "output": "best answer",
                "budget_used": 11,
                "success": True,
            },
            {
                "domain": "api",
                "confidence": 0.80,
                "output": "different answer",
                "budget_used": 7,
                "success": True,
            },
        ],
        overall_budget=40,
    )

    assert result["result"] == "best answer"
    assert len(result["conflicts"]) == 2
    assert result["conflicts"][0]["domain"] == "auth"
    assert result["budget_accounting"]["used"] == 21


def test_reconcile_tolerates_non_numeric_confidence_values() -> None:
    result = orchestrator_module.reconcile_fanout_results(
        [
            {
                "domain": "auth",
                "confidence": "not-a-number",
                "output": "fallback answer",
                "budget_used": 3,
                "success": True,
            },
            {
                "domain": "db",
                "confidence": 0.95,
                "output": "best answer",
                "budget_used": 11,
                "success": True,
            },
        ],
        overall_budget=40,
    )

    assert result["result"] == "best answer"
    assert result["per_domain"][0]["domain"] == "db"
    assert result["per_domain"][1]["confidence"] == 0.0


def test_fanout_persists_telemetry_row_with_temp_db() -> None:
    with TemporaryDirectory() as td:
        db = Database(Path(td) / "test.db")
        task = {
            "task_id": "fanout-123",
            "opt_in_fanout": True,
            "description": "fan out telemetry",
            "domains": [{"name": "primary", "confidence": 0.95}],
        }

        orchestrator_module.fan_out_task(task, db=db)

        with db.conn() as conn:
            row = conn.execute(
                """
                SELECT task_id, selected_routers, budget_accounting
                FROM fanout_telemetry
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

        assert row is not None
        assert row[0] == "fanout-123"
        assert "primary" in json.loads(row[1])
        assert "used" in json.loads(row[2])
        db.close()


def test_fanout_clamps_budget_used_when_per_router_budget_is_set() -> None:
    long_output = "0123456789" * 20
    fake = FakeOrchestrator(output=long_output, tokens=200)
    task = {
        "opt_in_fanout": True,
        "description": "budget clamp",
        "domains": [{"name": "primary", "confidence": 0.95}],
    }

    result = orchestrator_module.fan_out_task(
        task,
        per_router_budget=20,
        orchestrator=fake,
    )

    assert result["per_domain"][0]["budget_used"] == 20
    assert len(result["per_domain"][0]["output"]) == 80
    assert len(result["per_domain"][0]["output"]) < len(long_output)


def test_fanout_uses_persisted_project_caps_when_available() -> None:
    with TemporaryDirectory() as td:
        db = Database(Path(td) / "fanout.db")
        project_id = str((Path(td) / "project").resolve())
        db.set_project_setting(project_id, "fanout_cap", 2)
        db.set_project_setting(project_id, "budget_hard_cap_tokens", 15)

        fake = FakeOrchestrator(output="x" * 200, tokens=50)
        task = {
            "project_id": project_id,
            "opt_in_fanout": True,
            "description": "project caps",
            "domains": [
                {"name": "auth", "confidence": 0.98},
                {"name": "db", "confidence": 0.95},
                {"name": "api", "confidence": 0.92},
            ],
        }

        result = orchestrator_module.fan_out_task(task, orchestrator=fake, db=db)

        assert len(result["per_domain"]) == 2
        assert len(fake.executed) == 2
        assert all(entry["budget_used"] == 15 for entry in result["per_domain"])
        db.close()


def test_fanout_prompt_limit_is_respected() -> None:
    fake = FakeOrchestrator()
    task = {
        "opt_in_fanout": True,
        "description": "split this up but use max 2 agents",
        "domains": [
            {"name": "auth", "confidence": 0.98},
            {"name": "db", "confidence": 0.95},
            {"name": "api", "confidence": 0.92},
        ],
    }

    result = orchestrator_module.fan_out_task(task, orchestrator=fake)

    assert [entry["domain"] for entry in result["per_domain"]] == ["auth", "db"]
    assert set(fake.executed) == {
        "[fan-out:auth] split this up but use max 2 agents",
        "[fan-out:db] split this up but use max 2 agents",
    }


def test_fanout_unlimited_sentinel_does_not_reject_routes() -> None:
    fake = FakeOrchestrator()
    fake._config = SimpleNamespace(
        parallelism=SimpleNamespace(max_workers=orchestrator_module.UNLIMITED_PARALLELISM)
    )
    task = {
        "opt_in_fanout": True,
        "description": "split auth db api",
        "domains": [
            {"name": "auth", "confidence": 0.98},
            {"name": "db", "confidence": 0.95},
            {"name": "api", "confidence": 0.92},
        ],
    }

    result = orchestrator_module.fan_out_task(task, orchestrator=fake)

    assert [entry["domain"] for entry in result["per_domain"]] == ["auth", "db", "api"]
    assert len(fake.executed) == 3


def test_fanout_ignores_non_numeric_domain_confidence() -> None:
    fake = FakeOrchestrator()
    task = {
        "opt_in_fanout": True,
        "description": "ignore bad confidence",
        "domains": [
            {"name": "auth", "confidence": "oops"},
            {"name": "db", "confidence": 0.95},
        ],
    }

    result = orchestrator_module.fan_out_task(task, orchestrator=fake)

    assert [entry["domain"] for entry in result["per_domain"]] == ["db"]
    assert fake.executed == ["[fan-out:db] ignore bad confidence"]
