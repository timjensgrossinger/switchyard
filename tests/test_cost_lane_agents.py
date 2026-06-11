"""Tests for cost_lane learned agent drafting."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.agents import evaluate_pattern_readiness, generate_agent_draft
from shared.db import Database


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "cost-lane.db")


def test_cost_lane_readiness_for_low_tier_pattern() -> None:
    pattern = {
        "pattern_desc": "Write tests for async helper",
        "occurrence_count": 6,
        "tier": "low",
        "rework_detected": False,
        "eval_quality": 0.82,
    }
    readiness = evaluate_pattern_readiness(pattern, "demo-project")
    assert readiness["lane"] == "cost_lane"
    assert readiness["ready"] is True


def test_generate_agent_draft_cost_lane_metadata(db) -> None:
    candidate = {
        "pattern_hash": "abc123",
        "description": "Refactor small utility with low-tier routing",
        "tier": "low",
        "occurrence_count": 7,
        "rework_detected": False,
        "eval_quality": 0.9,
    }
    draft = generate_agent_draft("demo-project", candidate, db=db)
    assert draft["lane"] == "cost_lane"
    assert draft["cost_lane"] is True
    assert draft["preferred_tier"] == "low"
    assert draft["prefer_free"] is True
    assert draft["model"] == "haiku"
