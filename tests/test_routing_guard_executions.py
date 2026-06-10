"""Tests for Fix 2: routing_guard_executions table and guard integration."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.db import Database


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        yield Database(Path(d) / "test.db")


def test_record_and_has_executions(db):
    db.routing_guard_record_execution(caller="claude", cwd="/proj", task_id="t1")
    assert db.routing_guard_has_executions(caller="claude", cwd="/proj") is True


def test_has_executions_false_when_none(db):
    assert db.routing_guard_has_executions(caller="claude", cwd="/proj") is False


def test_has_executions_caller_isolated(db):
    db.routing_guard_record_execution(caller="claude", cwd="/proj", task_id="t1")
    assert db.routing_guard_has_executions(caller="other", cwd="/proj") is False


def test_has_executions_cwd_isolated(db):
    db.routing_guard_record_execution(caller="claude", cwd="/proj-a", task_id="t1")
    assert db.routing_guard_has_executions(caller="claude", cwd="/proj-b") is False


def test_record_with_file_written(db):
    db.routing_guard_record_execution(
        caller="mcp", cwd="/proj", task_id="t2", file_written="shared/db.py"
    )
    assert db.routing_guard_has_executions(caller="mcp", cwd="/proj") is True


def test_caller_normalized_case(db):
    db.routing_guard_record_execution(caller="Claude", cwd="/proj", task_id="t1")
    assert db.routing_guard_has_executions(caller="claude", cwd="/proj") is True


def test_multiple_executions(db):
    for i in range(5):
        db.routing_guard_record_execution(caller="claude", cwd="/proj", task_id=f"t{i}")
    assert db.routing_guard_has_executions(caller="claude", cwd="/proj") is True
