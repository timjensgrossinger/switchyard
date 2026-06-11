"""Tests for plan 02 replay-safe op classification."""
from __future__ import annotations

import pytest

from shared.orchestrator import OpClass, infer_op_class
from shared.planner import Subtask


# ---------------------------------------------------------------------------
# OpClass enum
# ---------------------------------------------------------------------------

def test_opclass_values():
    assert OpClass.REPLAYABLE.value == "replayable"
    assert OpClass.SIDE_EFFECTING.value == "side_effecting"
    assert OpClass.APPROVAL_REQUIRED.value == "approval_required"


def test_opclass_from_value():
    assert OpClass("replayable") is OpClass.REPLAYABLE
    assert OpClass("side_effecting") is OpClass.SIDE_EFFECTING
    assert OpClass("approval_required") is OpClass.APPROVAL_REQUIRED


# ---------------------------------------------------------------------------
# infer_op_class
# ---------------------------------------------------------------------------

def _subtask(description: str, target_file: str | None = None) -> Subtask:
    return Subtask(id=1, description=description, tier="low", target_file=target_file)


def test_infer_target_file_is_side_effecting():
    st = _subtask("do something", target_file="/tmp/output.py")
    assert infer_op_class(st) is OpClass.SIDE_EFFECTING


def test_infer_read_description_is_replayable():
    st = _subtask("read the config and summarize it")
    assert infer_op_class(st) is OpClass.REPLAYABLE


def test_infer_grep_description_is_replayable():
    st = _subtask("grep the codebase for TODO markers")
    assert infer_op_class(st) is OpClass.REPLAYABLE


def test_infer_search_description_is_replayable():
    st = _subtask("search for all Python files")
    assert infer_op_class(st) is OpClass.REPLAYABLE


def test_infer_list_description_is_replayable():
    st = _subtask("list all subtasks in the plan")
    assert infer_op_class(st) is OpClass.REPLAYABLE


def test_infer_inspect_description_is_replayable():
    st = _subtask("inspect the database schema")
    assert infer_op_class(st) is OpClass.REPLAYABLE


def test_infer_apply_description_is_approval_required():
    st = _subtask("apply the migration to production")
    assert infer_op_class(st) is OpClass.APPROVAL_REQUIRED


def test_infer_deploy_description_is_approval_required():
    st = _subtask("deploy the service to staging")
    assert infer_op_class(st) is OpClass.APPROVAL_REQUIRED


def test_infer_merge_description_is_approval_required():
    st = _subtask("merge the branch into main")
    assert infer_op_class(st) is OpClass.APPROVAL_REQUIRED


def test_infer_default_no_target_no_keywords_is_side_effecting():
    st = _subtask("implement the authentication module")
    assert infer_op_class(st) is OpClass.SIDE_EFFECTING


def test_infer_target_file_overrides_read_description():
    """target_file always → SIDE_EFFECTING regardless of read keywords."""
    st = _subtask("read and rewrite the config", target_file="/tmp/config.yaml")
    assert infer_op_class(st) is OpClass.SIDE_EFFECTING


# ---------------------------------------------------------------------------
# Subtask.op_class field
# ---------------------------------------------------------------------------

def test_subtask_default_op_class():
    st = Subtask(id=1, description="do something", tier="low")
    assert st.op_class == "side_effecting"


def test_subtask_explicit_op_class():
    st = Subtask(id=2, description="read files", tier="low", op_class="replayable")
    assert st.op_class == "replayable"


def test_subtask_approval_required_op_class():
    st = Subtask(id=3, description="deploy", tier="high", op_class="approval_required")
    assert st.op_class == "approval_required"


# ---------------------------------------------------------------------------
# op_class column on swarm_events
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    from shared.db import Database
    return Database(tmp_path / "test.db")


def test_swarm_events_has_op_class_column(db):
    with db.conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(swarm_events)").fetchall()}
    assert "op_class" in cols


def test_swarm_events_op_class_default(db):
    with db.conn() as conn:
        conn.execute(
            "INSERT INTO swarm_events (swarm_id, event_type, payload, ts)"
            " VALUES ('sw-001', 'test', '{}', 0.0)"
        )
    with db.conn() as conn:
        row = conn.execute(
            "SELECT op_class FROM swarm_events WHERE swarm_id='sw-001'"
        ).fetchone()
    assert row is not None
    assert row[0] == "side_effecting"


def test_swarm_events_op_class_explicit(db):
    with db.conn() as conn:
        conn.execute(
            "INSERT INTO swarm_events (swarm_id, event_type, payload, ts, op_class)"
            " VALUES ('sw-002', 'test', '{}', 0.0, 'replayable')"
        )
    with db.conn() as conn:
        row = conn.execute(
            "SELECT op_class FROM swarm_events WHERE swarm_id='sw-002'"
        ).fetchone()
    assert row is not None
    assert row[0] == "replayable"


# ---------------------------------------------------------------------------
# Replay semantics contract (unit)
# ---------------------------------------------------------------------------

def test_replayable_does_not_consume_idempotency_key(db):
    """REPLAYABLE ops re-run each time — claim_attempt still returns not-completed."""
    _, done = db.claim_attempt("replay_test", "read-op-key")
    assert done is False
    _, done2 = db.claim_attempt("replay_test", "read-op-key")
    assert done2 is False  # no record_file_write called → still not completed


def test_side_effecting_marks_complete_after_file_write(db):
    """SIDE_EFFECTING ops: after record_file_write, claim_attempt returns already_completed."""
    db.record_file_write("side_eff_test", "write-op-key", "/tmp/out.py", 5)
    _, done = db.claim_attempt("side_eff_test", "write-op-key")
    assert done is True
