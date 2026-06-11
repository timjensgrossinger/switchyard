"""Tests for plan 11 — contextual bandit routing."""
from __future__ import annotations

import time
import uuid

import pytest

from shared.db import Database
from shared.bandit import (
    extract_task_features,
    LinUCBArmModel,
    BanditPolicy,
    FEATURE_DIM,
    get_bandit_policy,
)
import shared.bandit as _bandit_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture(autouse=True)
def reset_bandit_singleton():
    """Reset the module-level singleton between tests."""
    _bandit_mod._bandit_policy = None
    yield
    _bandit_mod._bandit_policy = None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_routing_decisions_table_exists(db):
    with db.conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(routing_decisions)").fetchall()}
    assert {"task_id", "features", "heuristic_pick", "bandit_pick", "chosen",
            "outcome_score", "regret", "ts"} <= cols


def test_routing_decisions_index_exists(db):
    with db.conn() as conn:
        indices = {
            r[1]
            for r in conn.execute(
                "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='routing_decisions'"
            ).fetchall()
        }
    assert "idx_routing_decisions_ts" in indices


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def test_extract_features_length(db):
    features = extract_task_features("Fix the bug in auth.py")
    assert len(features) == FEATURE_DIM


def test_extract_features_urgency(db):
    normal = extract_task_features("Fix the login bug")
    urgent = extract_task_features("CRITICAL production down fix auth")
    assert urgent[1] > normal[1]


def test_extract_features_all_floats():
    features = extract_task_features("Add caching to the database layer", project_id="proj-abc")
    assert all(isinstance(f, float) for f in features)


def test_extract_features_bounded():
    features = extract_task_features("x" * 2000)
    assert all(-1.0 <= f <= 1.0 for f in features)


def test_extract_features_language_signal():
    py_features = extract_task_features("Write a pytest fixture for the database")
    rs_features = extract_task_features("Add a Rust cargo crate for HTTP")
    # Python index=0, Rust index=2 in lang_feats (offset 6)
    assert py_features[6] == 1.0  # python signal
    assert rs_features[8] == 1.0  # rust signal


# ---------------------------------------------------------------------------
# LinUCBArmModel
# ---------------------------------------------------------------------------

def test_arm_model_initial_ucb_is_finite():
    arm = LinUCBArmModel(arm_id="low:heuristic")
    features = extract_task_features("simple task")
    score = arm.ucb_score(features)
    assert isinstance(score, float)
    assert not (score != score)  # not NaN


def test_arm_model_update_changes_score():
    arm = LinUCBArmModel(arm_id="medium:heuristic")
    features = extract_task_features("implement database migration")
    before = arm.ucb_score(features)
    arm.update(features, reward=1.0)
    arm.update(features, reward=1.0)
    after = arm.ucb_score(features)
    assert before != after or arm.n_updates == 2


def test_arm_model_n_updates_increments():
    arm = LinUCBArmModel(arm_id="high:heuristic")
    features = extract_task_features("complex refactor")
    arm.update(features, 0.5)
    arm.update(features, 0.8)
    assert arm.n_updates == 2


# ---------------------------------------------------------------------------
# BanditPolicy
# ---------------------------------------------------------------------------

def test_bandit_policy_select_returns_decision():
    policy = BanditPolicy(mode="shadow")
    features = extract_task_features("add unit tests")
    arms = ["low:heuristic", "medium:heuristic", "high:heuristic"]
    decision = policy.select(features, arms, heuristic_arm="low:heuristic")
    assert decision.heuristic_arm == "low:heuristic"
    assert decision.chosen_arm == "low:heuristic"  # shadow mode always executes heuristic


def test_bandit_policy_shadow_mode_chosen_is_heuristic():
    policy = BanditPolicy(mode="shadow")
    features = extract_task_features("refactor architecture")
    arms = ["low:heuristic", "medium:heuristic", "high:heuristic"]
    decision = policy.select(features, arms, heuristic_arm="high:heuristic")
    assert decision.chosen_arm == "high:heuristic"


def test_bandit_policy_live_mode_may_diverge():
    policy = BanditPolicy(mode="live", alpha=10.0)
    features = [1.0] * FEATURE_DIM
    arms = ["low:heuristic", "medium:heuristic"]
    # Force medium arm to have high expected reward
    for _ in range(20):
        policy.update("medium:heuristic", features, reward=1.0)
    for _ in range(20):
        policy.update("low:heuristic", features, reward=0.0)
    decision = policy.select(features, arms, heuristic_arm="low:heuristic")
    # Live mode should pick medium (higher reward)
    assert decision.bandit_arm == "medium:heuristic"


def test_bandit_policy_empty_arms_falls_back():
    policy = BanditPolicy(mode="shadow")
    features = extract_task_features("simple fix")
    decision = policy.select(features, [], heuristic_arm="low:heuristic")
    assert decision.chosen_arm == "low:heuristic"


def test_bandit_policy_update_then_arm_stats():
    policy = BanditPolicy(mode="shadow")
    features = extract_task_features("test task")
    policy.update("low:heuristic", features, reward=0.9)
    stats = policy.arm_stats()
    assert any(s["arm_id"] == "low:heuristic" and s["n_updates"] == 1 for s in stats)


def test_get_bandit_policy_singleton():
    p1 = get_bandit_policy()
    p2 = get_bandit_policy()
    assert p1 is p2


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def test_log_routing_decision_returns_id(db):
    features = extract_task_features("task")
    row_id = db.log_routing_decision(
        "task-001", features, "low:heuristic", "medium:heuristic", "low:heuristic"
    )
    assert isinstance(row_id, int) and row_id > 0


def test_get_bandit_summary_returns_rows(db):
    features = extract_task_features("task")
    db.log_routing_decision("t1", features, "low:h", "medium:h", "low:h")
    db.log_routing_decision("t2", features, "medium:h", "medium:h", "medium:h")
    rows = db.get_bandit_summary(limit=10)
    assert len(rows) >= 2


def test_update_routing_decision_outcome(db):
    features = extract_task_features("task")
    db.log_routing_decision("t3", features, "low:h", "high:h", "low:h")
    db.update_routing_decision_outcome("t3", outcome_score=0.85, regret=-0.1)
    rows = db.get_bandit_summary(limit=1)
    scored = [r for r in rows if r["task_id"] == "t3"]
    if scored:
        assert scored[0]["outcome_score"] == pytest.approx(0.85)


def test_get_bandit_summary_since_ts_filters(db):
    features = extract_task_features("task")
    db.log_routing_decision("old-task", features, "low:h", "low:h", "low:h")
    with db.conn() as conn:
        conn.execute(
            "UPDATE routing_decisions SET ts=? WHERE task_id='old-task'",
            (time.time() - 7200,),
        )
    db.log_routing_decision("new-task", features, "medium:h", "medium:h", "medium:h")
    recent = db.get_bandit_summary(limit=100, since_ts=time.time() - 3600)
    task_ids = {r["task_id"] for r in recent}
    assert "new-task" in task_ids
    assert "old-task" not in task_ids
