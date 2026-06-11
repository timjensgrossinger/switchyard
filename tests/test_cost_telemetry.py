"""Tests for plan 07 cost dashboard / savings telemetry."""
from __future__ import annotations

import time
import pytest

from shared.db import Database


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.db")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_cost_telemetry_table_exists(db):
    with db.conn() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "cost_telemetry" in tables


def test_cost_telemetry_columns(db):
    with db.conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(cost_telemetry)").fetchall()}
    for col in ("task_id", "tier", "provider_id", "model", "input_tokens",
                "output_tokens", "est_cost_usd", "counterfactual_tier",
                "counterfactual_cost_usd", "ts"):
        assert col in cols, f"missing column: {col}"


# ---------------------------------------------------------------------------
# record_cost_telemetry
# ---------------------------------------------------------------------------

def test_record_cost_telemetry_basic(db):
    db.record_cost_telemetry(
        task_id="task-001", tier="low", provider_id="claude-code",
        model="haiku", input_tokens=1000, output_tokens=250,
        est_cost_usd=0.00025, counterfactual_cost_usd=0.015,
    )
    with db.conn() as conn:
        row = conn.execute(
            "SELECT tier, provider_id, model, input_tokens, est_cost_usd,"
            " counterfactual_cost_usd FROM cost_telemetry WHERE task_id='task-001'"
        ).fetchone()
    assert row is not None
    assert row[0] == "low"
    assert row[1] == "claude-code"
    assert row[2] == "haiku"
    assert row[3] == 1000
    assert abs(row[4] - 0.00025) < 1e-9
    assert abs(row[5] - 0.015) < 1e-9


def test_record_multiple_subtasks(db):
    for i in range(5):
        db.record_cost_telemetry(
            task_id=f"task-{i}", tier="low", provider_id="gemini",
            model="flash", input_tokens=500, output_tokens=100,
            est_cost_usd=0.0001, counterfactual_cost_usd=0.005,
        )
    with db.conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM cost_telemetry").fetchone()[0]
    assert count == 5


# ---------------------------------------------------------------------------
# get_cost_summary
# ---------------------------------------------------------------------------

def _record_mix(db):
    db.record_cost_telemetry("t1", "low", "gemini", "flash", 1000, 200, 0.0002, counterfactual_cost_usd=0.01)
    db.record_cost_telemetry("t2", "low", "claude-code", "haiku", 2000, 400, 0.0005, counterfactual_cost_usd=0.02)
    db.record_cost_telemetry("t3", "medium", "claude-code", "sonnet", 5000, 1000, 0.002, counterfactual_cost_usd=0.025)
    db.record_cost_telemetry("t4", "high", "claude-code", "opus", 8000, 2000, 0.015, counterfactual_cost_usd=0.015)


def test_get_cost_summary_by_tier(db):
    _record_mix(db)
    rows = db.get_cost_summary(group_by="tier")
    assert len(rows) > 0
    tiers = {r["tier"] for r in rows}
    assert "low" in tiers


def test_get_cost_summary_savings_positive_for_low(db):
    db.record_cost_telemetry("t1", "low", "gemini", "flash", 1000, 200, 0.0002, counterfactual_cost_usd=0.01)
    rows = db.get_cost_summary(group_by="tier")
    low = next(r for r in rows if r["tier"] == "low")
    assert low["savings_usd"] > 0


def test_get_cost_summary_by_provider(db):
    _record_mix(db)
    rows = db.get_cost_summary(group_by="provider_id")
    providers = {r["provider_id"] for r in rows}
    assert "gemini" in providers
    assert "claude-code" in providers


def test_get_cost_summary_since_ts_filters(db):
    old_ts = time.time() - 7200
    # Insert old record manually
    with db.conn() as conn:
        conn.execute(
            "INSERT INTO cost_telemetry (task_id, tier, provider_id, model, input_tokens,"
            " output_tokens, est_cost_usd, counterfactual_cost_usd, ts)"
            " VALUES ('old-task', 'low', 'p', 'm', 100, 20, 0.0001, 0.001, ?)",
            (old_ts,),
        )
    db.record_cost_telemetry("new-task", "low", "p", "m", 200, 40, 0.0002, counterfactual_cost_usd=0.002)
    # Filter to last hour
    rows = db.get_cost_summary(since_ts=time.time() - 3600, group_by="tier")
    total_tasks = sum(r["subtask_count"] for r in rows)
    assert total_tasks == 1  # only new-task


def test_get_cost_summary_subtask_count(db):
    _record_mix(db)
    rows = db.get_cost_summary(group_by="tier")
    total = sum(r["subtask_count"] for r in rows)
    assert total == 4


def test_get_cost_summary_invalid_group_by_defaults_to_tier(db):
    _record_mix(db)
    rows = db.get_cost_summary(group_by="invalid_column")
    assert all("tier" in r for r in rows)
