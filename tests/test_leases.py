"""Tests for plan 09 worker leases + dead letter queue."""
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

def test_worker_leases_table_exists(db):
    with db.conn() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "worker_leases" in tables


def test_dead_letters_table_exists(db):
    with db.conn() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "dead_letters" in tables


def test_worker_leases_columns(db):
    with db.conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(worker_leases)").fetchall()}
    for col in ("task_id", "worker_id", "acquired_at", "expires_at", "last_heartbeat", "attempt", "status"):
        assert col in cols


# ---------------------------------------------------------------------------
# acquire_lease
# ---------------------------------------------------------------------------

def test_acquire_lease_success(db):
    ok = db.acquire_lease("task-001", "worker-1", ttl_seconds=60)
    assert ok is True


def test_acquire_lease_idempotent_same_worker(db):
    db.acquire_lease("task-002", "worker-1", ttl_seconds=60)
    ok = db.acquire_lease("task-002", "worker-1", ttl_seconds=60)
    assert ok is True  # same worker re-acquires


def test_acquire_lease_blocked_by_other_worker(db):
    db.acquire_lease("task-003", "worker-1", ttl_seconds=60)
    ok = db.acquire_lease("task-003", "worker-2", ttl_seconds=60)
    assert ok is False


def test_acquire_lease_reclaims_expired(db):
    db.acquire_lease("task-004", "worker-1", ttl_seconds=0.001)
    time.sleep(0.01)
    ok = db.acquire_lease("task-004", "worker-2", ttl_seconds=60)
    assert ok is True


# ---------------------------------------------------------------------------
# heartbeat
# ---------------------------------------------------------------------------

def test_heartbeat_returns_true_for_active_lease(db):
    db.acquire_lease("task-005", "worker-1", ttl_seconds=60)
    ok = db.heartbeat("task-005", "worker-1")
    assert ok is True


def test_heartbeat_returns_false_for_unknown(db):
    ok = db.heartbeat("task-none", "worker-x")
    assert ok is False


def test_heartbeat_returns_false_for_expired(db):
    db.acquire_lease("task-006", "worker-1", ttl_seconds=0.001)
    time.sleep(0.01)
    ok = db.heartbeat("task-006", "worker-1")
    assert ok is False


# ---------------------------------------------------------------------------
# expire_stale_leases
# ---------------------------------------------------------------------------

def test_expire_stale_leases_returns_expired(db):
    db.acquire_lease("task-007", "worker-1", ttl_seconds=0.001)
    db.acquire_lease("task-008", "worker-1", ttl_seconds=60)
    time.sleep(0.01)
    expired = db.expire_stale_leases()
    assert "task-007" in expired
    assert "task-008" not in expired


def test_expire_stale_leases_empty_when_none_expired(db):
    db.acquire_lease("task-009", "worker-1", ttl_seconds=60)
    expired = db.expire_stale_leases()
    assert expired == []


# ---------------------------------------------------------------------------
# release_lease
# ---------------------------------------------------------------------------

def test_release_lease_marks_released(db):
    db.acquire_lease("task-010", "worker-1", ttl_seconds=60)
    db.release_lease("task-010", "worker-1")
    with db.conn() as conn:
        row = conn.execute(
            "SELECT status FROM worker_leases WHERE task_id='task-010'"
        ).fetchone()
    assert row is not None and row[0] == "released"


# ---------------------------------------------------------------------------
# dead_letter
# ---------------------------------------------------------------------------

def test_dead_letter_creates_entry(db):
    db.dead_letter("task-011", "subprocess crashed")
    entries = db.get_dead_letters()
    assert any(e["task_id"] == "task-011" for e in entries)


def test_dead_letter_increments_on_repeat(db):
    db.dead_letter("task-012", "error A")
    db.dead_letter("task-012", "error B")
    entries = db.get_dead_letters()
    e = next(x for x in entries if x["task_id"] == "task-012")
    assert e["attempt_count"] == 2
    assert e["last_error"] == "error B"


def test_get_dead_letters_empty(db):
    assert db.get_dead_letters() == []


def test_replay_dead_letter_removes_entry(db):
    db.dead_letter("task-013", "some error")
    ok = db.replay_dead_letter("task-013")
    assert ok is True
    entries = db.get_dead_letters()
    assert not any(e["task_id"] == "task-013" for e in entries)


def test_replay_dead_letter_unknown_returns_false(db):
    ok = db.replay_dead_letter("nonexistent-task")
    assert ok is False
