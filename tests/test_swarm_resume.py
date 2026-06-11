#!/usr/bin/env python3
"""Tests for Phase 37 strict swarm resume semantics."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_server
import shared.swarm as swarm_module
from shared.config import TGsConfig
from shared.db import Database
from shared.orchestrator import seed_resume_from_checkpoint
from shared.swarm import (
    CoordinatorRoundCheckpoint,
    SwarmRun,
    persist_coordinator_round_checkpoint,
    persist_swarm_run,
)


def _stub_init(monkeypatch, tmp_path: Path) -> tuple[TGsConfig, Database]:
    db_path = tmp_path / "swarm-resume.db"
    cfg = TGsConfig(db_path=db_path)
    db = Database(db_path=db_path)
    db._init_schema(db._get_connection())
    monkeypatch.setattr(
        mcp_server,
        "_ensure_init",
        lambda: (cfg, db, None, None, None),
    )
    return cfg, db


def _seed_failed_swarm(db: Database, swarm_id: str = "swarm-failed") -> None:
    persist_swarm_run(
        SwarmRun(
            swarm_id=swarm_id,
            task_hash="task-hash",
            status="failed",
            requested_agents=4,
            effective_agents=3,
            progress_counters={"declared_topology": "star", "runner": "star"},
            topology="star",
            round=2,
            resumable=True,
            resume_status="resumable",
        ),
        db=db,
    )
    db.log_swarm_event(
        swarm_id,
        "execute_swarm_requested",
        {"task": "resume-task"},
    )
    persist_coordinator_round_checkpoint(
        CoordinatorRoundCheckpoint(
            swarm_id=swarm_id,
            plan_revision=1,
            round_index=1,
            coordinator_subtask_id="coord-1",
            verdict="another-pass",
            synthesis_summary={"summary_text": "Need another pass for worker alpha"},
            fallback_reason="needs_retry",
            round_counters={"round": 1},
        ),
        db=db,
    )
    persist_coordinator_round_checkpoint(
        CoordinatorRoundCheckpoint(
            swarm_id=swarm_id,
            plan_revision=1,
            round_index=2,
            coordinator_subtask_id="coord-1",
            verdict="fallback",
            synthesis_summary={"summary_text": "Fallback to linear after coordinator failure"},
            fallback_reason="coordinator_failed",
            round_counters={"round": 2},
        ),
        db=db,
    )


def test_resume_tools_registered() -> None:
    tool_names = {tool["name"] for tool in mcp_server.TOOLS}
    assert "resume_swarm_inspect" in tool_names
    assert "resume_swarm_confirm" in tool_names
    assert (
        mcp_server.HANDLERS["resume_swarm_inspect"]
        is mcp_server.handle_resume_swarm_inspect
    )
    assert (
        mcp_server.HANDLERS["resume_swarm_confirm"]
        is mcp_server.handle_resume_swarm_confirm
    )


def test_inspect_returns_compact_checkpoint_list(monkeypatch, tmp_path: Path) -> None:
    _cfg, db = _stub_init(monkeypatch, tmp_path)
    _seed_failed_swarm(db)

    result = mcp_server.resume_swarm_inspect("swarm-failed")

    checkpoints = result["checkpoints"]
    assert result["checkpoint_count"] == 2
    assert checkpoints[0]["round_index"] == 2
    assert checkpoints[1]["round_index"] == 1
    assert checkpoints[0]["checkpoint_index"] == 2
    assert checkpoints[0]["plan_revision"] == 1
    assert checkpoints[0]["verdict"] == "fallback"
    assert checkpoints[0]["fallback_reason"] == "coordinator_failed"
    assert checkpoints[0]["short_summary"] == "Fallback to linear after coordinator failure"
    assert checkpoints[0]["lineage"] == {"parent_swarm_id": "swarm-failed"}


def test_resume_creates_new_swarm_and_preserves_audit(monkeypatch, tmp_path: Path) -> None:
    _cfg, db = _stub_init(monkeypatch, tmp_path)
    _seed_failed_swarm(db)

    result = mcp_server.resume_swarm_confirm(
        "swarm-failed",
        2,
    )

    assert result["started"] is True
    resumed_swarm_id = result["result"]["swarm_id"]
    assert resumed_swarm_id != "swarm-failed"
    assert result["result"]["lineage"] == {
        "parent_swarm_id": "swarm-failed",
        "chosen_checkpoint_index": 2,
        "plan_revision": 1,
    }

    failed_summary = db.get_swarm_summary("swarm-failed")
    resumed_summary = db.get_swarm_summary(resumed_swarm_id)
    assert failed_summary is not None
    assert resumed_summary is not None
    assert failed_summary["status"] == "failed"
    assert resumed_summary["parent_swarm_id"] == "swarm-failed"
    assert resumed_summary["chosen_checkpoint_index"] == 2
    assert resumed_summary["topology"] == failed_summary["topology"]
    assert resumed_summary["requested_agents"] == failed_summary["requested_agents"]
    assert resumed_summary["effective_agents"] == failed_summary["effective_agents"]
    assert resumed_summary["round"] == 2
    assert resumed_summary["progress_counters"]["restored_plan_revision"] == 1
    assert resumed_summary["progress_counters"]["resumed_from_checkpoint_index"] == 2


def test_resume_uses_ephemeral_db_fixture(monkeypatch, tmp_path: Path) -> None:
    cfg, db = _stub_init(monkeypatch, tmp_path)
    _seed_failed_swarm(db)
    checkpoint = db.get_coordinator_round_checkpoint_by_index("swarm-failed", 2)
    assert checkpoint is not None

    resumed_swarm_id = seed_resume_from_checkpoint(
        checkpoint,
        db=db,
        operator_id="operator-2",
    )

    assert cfg.db_path == tmp_path / "swarm-resume.db"
    assert db._db_path == cfg.db_path
    assert db._db_path.parent == tmp_path
    with db.conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM swarm_runs WHERE swarm_id IN (?, ?)",
            ("swarm-failed", resumed_swarm_id),
        ).fetchone()
        assert count == (2,)


def test_resume_accepts_explicit_new_swarm_id(monkeypatch, tmp_path: Path) -> None:
    _cfg, db = _stub_init(monkeypatch, tmp_path)
    _seed_failed_swarm(db)
    checkpoint = db.get_coordinator_round_checkpoint_by_index("swarm-failed", 2)
    assert checkpoint is not None

    resumed_swarm_id = seed_resume_from_checkpoint(
        checkpoint,
        db=db,
        new_swarm_id="swarm-explicit",
    )

    assert resumed_swarm_id == "swarm-explicit"
    resumed_summary = db.get_swarm_summary("swarm-explicit")
    assert resumed_summary is not None
    assert resumed_summary["parent_swarm_id"] == "swarm-failed"
    assert resumed_summary["chosen_checkpoint_index"] == 2


def test_resume_triggers_runtime_handoff(monkeypatch, tmp_path: Path) -> None:
    """Confirm that resume_swarm_confirm spawns the runtime handoff and that
    the orchestrator.run path is invoked and the resumed swarm summary preserves
    parent/checkpoint lineage.
    """
    cfg, db = _stub_init(monkeypatch, tmp_path)
    _seed_failed_swarm(db)

    called = {"flag": False, "task": None}

    class OrchStub:
        def run(self, task: str, router=None, execution_id=None):
            called["flag"] = True
            called["task"] = str(task)
            return {"task_id": "stub", "synthesis": {"summary_text": "resume-synth"}}

    monkeypatch.setattr(mcp_server, "_ensure_init", lambda: (cfg, db, None, None, OrchStub()))

    result = mcp_server.resume_swarm_confirm("swarm-failed", 2)

    assert result["started"] is True
    resumed_swarm_id = result["result"]["swarm_id"]

    # Wait for background handoff to invoke orchestrator.run and persist summary
    import time

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if called["flag"]:
            break
        time.sleep(0.02)

    assert called["flag"] is True
    assert called["task"] == "resume-task"

    resumed_summary = db.get_swarm_summary(resumed_swarm_id)
    assert resumed_summary is not None
    assert resumed_summary["parent_swarm_id"] == "swarm-failed"
    assert resumed_summary["chosen_checkpoint_index"] == 2


def test_resume_runtime_handoff_persists_missing_task_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cfg, db = _stub_init(monkeypatch, tmp_path)
    _seed_failed_swarm(db)
    checkpoint = db.get_coordinator_round_checkpoint_by_index("swarm-failed", 2)
    assert checkpoint is not None
    resumed_swarm_id = seed_resume_from_checkpoint(checkpoint, db=db)
    monkeypatch.setattr(mcp_server, "_ensure_init", lambda: (cfg, db, None, None, object()))

    result = mcp_server._resume_swarm_runtime_handoff(
        db,
        object(),
        resumed_swarm_id,
        {
            "parent_swarm_id": "swarm-failed",
            "chosen_checkpoint_index": 2,
            "plan_revision": 1,
        },
        None,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "resume source task is unavailable for failed_swarm_id"
    error_payload = db.get_latest_swarm_event_payload(resumed_swarm_id, "resume_error")
    assert error_payload == {
        "message": "resume source task is unavailable for failed_swarm_id"
    }
    resumed_summary = db.get_swarm_summary(resumed_swarm_id)
    assert resumed_summary is not None
    assert resumed_summary["status"] == "failed"
    assert resumed_summary["resume_status"] == "failed"


def test_list_resume_checkpoints_tolerates_non_numeric_revision_fields(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        swarm_module,
        "list_coordinator_round_checkpoints",
        lambda swarm_id, plan_revision=None, db=None: [  # noqa: ARG005
            {
                "plan_revision": {"bad": "value"},
                "round_index": ["oops"],
                "verdict": "fallback",
                "fallback_reason": "bad-shape",
                "synthesis_summary": {"summary_text": "still works"},
            }
        ],
    )

    checkpoints = swarm_module.list_resume_checkpoints("swarm-failed")

    assert checkpoints == [
        {
            "round_index": 0,
            "checkpoint_index": 0,
            "plan_revision": 0,
            "verdict": "fallback",
            "fallback_reason": "bad-shape",
            "short_summary": "still works",
            "lineage": {"parent_swarm_id": "swarm-failed"},
        }
    ]


def test_compact_checkpoint_summary_skips_invalid_length_chars() -> None:
    compact = swarm_module._compact_checkpoint_summary(
        {
            "artifact_type": "summary",
            "summary_text": "hello",
            "length_chars": {"bad": "value"},
            "artifact_ref": "ref-1",
        }
    )

    assert compact == {
        "artifact_type": "summary",
        "summary_text": "hello",
        "artifact_ref": "ref-1",
    }


def test_resume_confirm_rejects_spoofed_operator_id(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _cfg, db = _stub_init(monkeypatch, tmp_path)
    _seed_failed_swarm(db)

    result = mcp_server.handle_resume_swarm_confirm(
        {
            "failed_swarm_id": "swarm-failed",
            "checkpoint_index": 2,
            "operator_id": "spoofed-user",
        }
    )

    assert result == {
        "error": "invalid_request",
        "details": "operator_id cannot be asserted by this tool; omit it to resume anonymously",
    }


def test_resume_confirm_requires_plan_revision_for_ambiguous_checkpoint(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _cfg, db = _stub_init(monkeypatch, tmp_path)
    _seed_failed_swarm(db)
    persist_coordinator_round_checkpoint(
        CoordinatorRoundCheckpoint(
            swarm_id="swarm-failed",
            plan_revision=2,
            round_index=2,
            coordinator_subtask_id="coord-2",
            verdict="fallback",
            synthesis_summary={"summary_text": "Fallback in newer revision"},
            fallback_reason="newer_revision",
            round_counters={"round": 2},
        ),
        db=db,
    )

    inspect_result = mcp_server.resume_swarm_inspect("swarm-failed")

    assert inspect_result["checkpoints"][0]["plan_revision"] == 2
    assert inspect_result["checkpoints"][1]["plan_revision"] == 1
    assert mcp_server.resume_swarm_confirm("swarm-failed", 2) == {
        "error": "invalid_request",
        "details": "checkpoint_index is ambiguous across plan revisions; provide plan_revision from resume_swarm_inspect",
    }


def test_resume_confirm_allows_explicit_plan_revision(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cfg, db = _stub_init(monkeypatch, tmp_path)
    _seed_failed_swarm(db)
    persist_coordinator_round_checkpoint(
        CoordinatorRoundCheckpoint(
            swarm_id="swarm-failed",
            plan_revision=2,
            round_index=2,
            coordinator_subtask_id="coord-2",
            verdict="fallback",
            synthesis_summary={"summary_text": "Fallback in newer revision"},
            fallback_reason="newer_revision",
            round_counters={"round": 2},
        ),
        db=db,
    )

    called = {"flag": False, "task": None}

    class OrchStub:
        def run(self, task: str, router=None, execution_id=None):
            called["flag"] = True
            called["task"] = str(task)
            return {"task_id": "stub", "synthesis": {"summary_text": "resume-synth"}}

    monkeypatch.setattr(mcp_server, "_ensure_init", lambda: (cfg, db, None, None, OrchStub()))

    result = mcp_server.handle_resume_swarm_confirm(
        {
            "failed_swarm_id": "swarm-failed",
            "checkpoint_index": 2,
            "plan_revision": 2,
        }
    )

    assert result["started"] is True
    assert result["result"]["requested_values"]["plan_revision"] == 2
    assert result["result"]["lineage"]["plan_revision"] == 2
