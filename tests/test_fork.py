"""Tests for plan 13 — trace fork + lineage."""
from __future__ import annotations

import json
import time
import uuid

import pytest

from shared.db import Database
from shared.replay import ReplayEngine


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture()
def engine(db):
    return ReplayEngine(db)


def _make_swarm(db, swarm_id: str | None = None, parent: str | None = None) -> str:
    sid = swarm_id or str(uuid.uuid4())
    with db.conn() as conn:
        conn.execute(
            "INSERT INTO swarm_runs"
            " (swarm_id, task_hash, created_ts, status,"
            "  requested_agents, effective_agents,"
            "  progress_counters, topology, round, resumable, resume_status,"
            "  parent_swarm_id)"
            " VALUES (?, ?, ?, 'completed', 2, 2, '{}', 'linear', 2, 0,"
            "         'not_resumable', ?)",
            (sid, "hash-xyz", time.time(), parent),
        )
    return sid


def _add_checkpoint(db, swarm_id, round_index, verdict="continue", next_work=None):
    with db.conn() as conn:
        cursor = conn.execute(
            "INSERT INTO coordinator_round_checkpoints"
            " (swarm_id, plan_revision, round_index, coordinator_subtask_id,"
            "  verdict, next_work_json, created_ts)"
            " VALUES (?, 1, ?, ?, ?, ?, ?)",
            (swarm_id, round_index, f"coord-{round_index}",
             verdict, json.dumps(next_work or []), time.time()),
        )
        return cursor.lastrowid


# ---------------------------------------------------------------------------
# Fork lineage
# ---------------------------------------------------------------------------

def test_fork_sets_parent_swarm_id(db, engine):
    parent_id = _make_swarm(db)
    _add_checkpoint(db, parent_id, 0)
    result = engine.fork(parent_id, dry_run=False)
    fork_id = result["fork_run_id"]
    fork_info = engine.show_run(fork_id)
    assert fork_info["parent_swarm_id"] == parent_id


def test_fork_parent_remains_intact(db, engine):
    parent_id = _make_swarm(db)
    _add_checkpoint(db, parent_id, 0)
    _add_checkpoint(db, parent_id, 1, verdict="complete")
    engine.fork(parent_id, dry_run=False)
    parent_info = engine.show_run(parent_id)
    assert parent_info["status"] == "completed"
    assert len(parent_info["checkpoints"]) == 2


def test_fork_returns_unique_run_id_each_time(db, engine):
    parent_id = _make_swarm(db)
    _add_checkpoint(db, parent_id, 0)
    r1 = engine.fork(parent_id, dry_run=False)
    r2 = engine.fork(parent_id, dry_run=False)
    assert r1["fork_run_id"] != r2["fork_run_id"]


def test_fork_inherits_from_checkpoint(db, engine):
    parent_id = _make_swarm(db)
    cp0 = _add_checkpoint(db, parent_id, 0, verdict="continue")
    _add_checkpoint(db, parent_id, 1, verdict="complete")
    result = engine.fork(parent_id, from_checkpoint_id=cp0, dry_run=True)
    assert result["plan"]["from_checkpoint_id"] == cp0
    assert result["plan"]["from_round_index"] == 0


def test_fork_plan_shows_is_fork(db, engine):
    parent_id = _make_swarm(db)
    _add_checkpoint(db, parent_id, 0)
    result = engine.fork(parent_id, dry_run=True)
    assert result["plan"]["is_fork"] is True


def test_fork_non_existent_run_returns_empty_plan(engine):
    result = engine.fork("nonexistent-run", dry_run=True)
    assert result["status"] == "dry_run"
    assert result["plan"]["subtasks_to_replay"] == 0


# ---------------------------------------------------------------------------
# Multi-generation lineage
# ---------------------------------------------------------------------------

def test_multi_gen_fork_chain(db, engine):
    grandparent_id = _make_swarm(db)
    _add_checkpoint(db, grandparent_id, 0)
    parent_result = engine.fork(grandparent_id, dry_run=False)
    parent_id = parent_result["fork_run_id"]
    _add_checkpoint(db, parent_id, 0)
    child_result = engine.fork(parent_id, dry_run=False)
    child_id = child_result["fork_run_id"]

    child_info = engine.show_run(child_id)
    parent_info = engine.show_run(parent_id)
    assert child_info["parent_swarm_id"] == parent_id
    assert parent_info["parent_swarm_id"] == grandparent_id
