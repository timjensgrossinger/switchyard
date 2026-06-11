"""Tests for plan 10 — persistent worker sessions."""
from __future__ import annotations

import time
import uuid

import pytest

from shared.db import Database
from shared.config import SessionConfig, TGsConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.db")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_worker_sessions_table_exists(db):
    with db.conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(worker_sessions)").fetchall()}
    assert {"session_id", "provider", "model", "pid", "started_at",
            "last_used_at", "status", "token_count"} <= cols


def test_worker_sessions_index_exists(db):
    with db.conn() as conn:
        indices = {
            r[1]
            for r in conn.execute(
                "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='worker_sessions'"
            ).fetchall()
        }
    assert "idx_worker_sessions_status" in indices


# ---------------------------------------------------------------------------
# create_worker_session
# ---------------------------------------------------------------------------

def test_create_worker_session_basic(db):
    sid = str(uuid.uuid4())
    db.create_worker_session(sid, "claude-code", "claude-sonnet-4-6")
    row = db.get_worker_session(sid)
    assert row is not None
    assert row["provider"] == "claude-code"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["status"] == "active"
    assert row["token_count"] == 0
    assert row["pid"] is None


def test_create_worker_session_with_pid(db):
    sid = str(uuid.uuid4())
    db.create_worker_session(sid, "gemini", "gemini-2.0-flash", pid=12345)
    row = db.get_worker_session(sid)
    assert row is not None
    assert row["pid"] == 12345


def test_get_worker_session_not_found(db):
    assert db.get_worker_session("nonexistent") is None


# ---------------------------------------------------------------------------
# update_worker_session
# ---------------------------------------------------------------------------

def test_update_status(db):
    sid = str(uuid.uuid4())
    db.create_worker_session(sid, "codex", "gpt-5-mini")
    db.update_worker_session(sid, status="idle")
    assert db.get_worker_session(sid)["status"] == "idle"


def test_update_token_count_delta(db):
    sid = str(uuid.uuid4())
    db.create_worker_session(sid, "claude-code", "claude-haiku-4-5")
    db.update_worker_session(sid, token_count_delta=100)
    db.update_worker_session(sid, token_count_delta=50)
    assert db.get_worker_session(sid)["token_count"] == 150


def test_update_pid(db):
    sid = str(uuid.uuid4())
    db.create_worker_session(sid, "claude-code", "claude-sonnet-4-6")
    db.update_worker_session(sid, pid=9999)
    assert db.get_worker_session(sid)["pid"] == 9999


def test_update_touch_updates_last_used_at(db):
    sid = str(uuid.uuid4())
    db.create_worker_session(sid, "claude-code", "claude-sonnet-4-6")
    before = db.get_worker_session(sid)["last_used_at"]
    time.sleep(0.02)
    db.update_worker_session(sid, touch=True)
    after = db.get_worker_session(sid)["last_used_at"]
    assert after >= before


# ---------------------------------------------------------------------------
# list_worker_sessions
# ---------------------------------------------------------------------------

def test_list_worker_sessions_all(db):
    for i in range(3):
        db.create_worker_session(str(uuid.uuid4()), "claude-code", f"model-{i}")
    rows = db.list_worker_sessions()
    assert len(rows) >= 3


def test_list_worker_sessions_filter_status(db):
    sid1 = str(uuid.uuid4())
    sid2 = str(uuid.uuid4())
    db.create_worker_session(sid1, "claude-code", "model-a")
    db.create_worker_session(sid2, "claude-code", "model-b")
    db.update_worker_session(sid2, status="idle")
    active_ids = [r["session_id"] for r in db.list_worker_sessions(status="active")]
    idle_ids = [r["session_id"] for r in db.list_worker_sessions(status="idle")]
    assert sid1 in active_ids
    assert sid2 in idle_ids
    assert sid2 not in active_ids


def test_list_worker_sessions_filter_provider(db):
    sid = str(uuid.uuid4())
    db.create_worker_session(sid, "gemini", "gemini-flash")
    providers = {r["provider"] for r in db.list_worker_sessions(provider="gemini")}
    assert "gemini" in providers


# ---------------------------------------------------------------------------
# reap_idle_sessions
# ---------------------------------------------------------------------------

def test_reap_idle_sessions_removes_stale(db):
    sid = str(uuid.uuid4())
    db.create_worker_session(sid, "claude-code", "model-x")
    db.update_worker_session(sid, status="idle")
    with db.conn() as conn:
        conn.execute(
            "UPDATE worker_sessions SET last_used_at=? WHERE session_id=?",
            (time.time() - 400, sid),
        )
    reaped = db.reap_idle_sessions(300.0)
    assert sid in reaped
    assert db.get_worker_session(sid)["status"] == "reaped"


def test_reap_idle_sessions_leaves_active(db):
    sid = str(uuid.uuid4())
    db.create_worker_session(sid, "claude-code", "model-y")
    with db.conn() as conn:
        conn.execute(
            "UPDATE worker_sessions SET last_used_at=? WHERE session_id=?",
            (time.time() - 9999, sid),
        )
    reaped = db.reap_idle_sessions(300.0)
    assert sid not in reaped


def test_reap_idle_sessions_empty_when_no_stale(db):
    sid = str(uuid.uuid4())
    db.create_worker_session(sid, "claude-code", "model-z")
    db.update_worker_session(sid, status="idle")
    reaped = db.reap_idle_sessions(300.0)
    assert sid not in reaped


# ---------------------------------------------------------------------------
# SessionConfig
# ---------------------------------------------------------------------------

def test_session_config_defaults():
    cfg = SessionConfig()
    assert cfg.enabled is True
    assert cfg.idle_ttl == 300.0
    assert cfg.max_per_provider == 8


def test_tgsconfig_has_session_field():
    cfg = TGsConfig()
    assert hasattr(cfg, "session")
    assert isinstance(cfg.session, SessionConfig)


# ---------------------------------------------------------------------------
# SessionManager (in-process, no subprocess)
# ---------------------------------------------------------------------------

def test_session_manager_start_returns_id(db):
    from shared.orchestrator import SessionManager
    mgr = SessionManager(db=db)
    sid = mgr.start("claude-code", "claude-sonnet-4-6")
    assert isinstance(sid, str) and len(sid) > 0


def test_session_manager_get_returns_session(db):
    from shared.orchestrator import SessionManager, WorkerSession
    mgr = SessionManager(db=db)
    sid = mgr.start("claude-code", "claude-sonnet-4-6")
    session = mgr.get(sid)
    assert isinstance(session, WorkerSession)


def test_session_manager_close_removes_session(db):
    from shared.orchestrator import SessionManager
    mgr = SessionManager(db=db)
    sid = mgr.start("claude-code", "claude-sonnet-4-6")
    mgr.close(sid)
    assert mgr.get(sid) is None


def test_session_manager_close_updates_db(db):
    from shared.orchestrator import SessionManager
    mgr = SessionManager(db=db)
    sid = mgr.start("claude-code", "claude-sonnet-4-6")
    mgr.close(sid)
    row = db.get_worker_session(sid)
    assert row is not None
    assert row["status"] == "closed"


def test_session_manager_start_records_in_db(db):
    from shared.orchestrator import SessionManager
    mgr = SessionManager(db=db)
    sid = mgr.start("gemini", "gemini-flash")
    row = db.get_worker_session(sid)
    assert row is not None
    assert row["provider"] == "gemini"
    assert row["status"] == "active"


def test_session_manager_reap_idle(db):
    from shared.orchestrator import SessionManager
    mgr = SessionManager(db=db)
    sid = mgr.start("claude-code", "claude-sonnet-4-6")
    db.update_worker_session(sid, status="idle")
    with db.conn() as conn:
        conn.execute(
            "UPDATE worker_sessions SET last_used_at=? WHERE session_id=?",
            (time.time() - 400, sid),
        )
    reaped = mgr.reap_idle(300.0)
    assert sid in reaped


def test_worker_session_is_alive_without_proc(db):
    from shared.orchestrator import WorkerSession
    session = WorkerSession("s-1", "claude-code", "model", proc=None, db=db)
    assert session.is_alive is False


def test_worker_session_close_idempotent(db):
    from shared.orchestrator import WorkerSession
    session = WorkerSession("s-2", "claude-code", "model", proc=None, db=db)
    session.close()
    session.close()


def test_worker_session_send_raises_when_closed(db):
    from shared.orchestrator import WorkerSession
    session = WorkerSession("s-3", "claude-code", "model", proc=None, db=db)
    session.close()
    with pytest.raises(RuntimeError, match="closed"):
        session.send("hello")
