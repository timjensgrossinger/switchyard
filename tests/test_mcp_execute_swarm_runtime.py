#!/usr/bin/env python3
"""Runtime handoff tests for execute_swarm (Plan 40.1-03).

These tests stub the Orchestrator.run path to deterministically exercise
persistence and error-handling semantics around runtime handoff and resume.
"""
from __future__ import annotations

import time
import threading
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_server
from shared.config import TGsConfig
from shared.db import Database


class _SimpleOrchestratorStub:
    def __init__(self, synth=None, delay=0):
        self.called = False
        self.called_with = None
        self.execution_ids: list[str | None] = []
        self.constraints: list[tuple[str | None, int | None]] = []
        self.synth = synth or {"summary_text": "synth-ok"}
        self.delay = delay

    def run(
        self,
        task: str,
        router=None,
        execution_id=None,
        topology=None,
        max_agents=None,
        unlimited_budget=False,
        workspace_root=None,
    ):
        # record call and optionally sleep to emulate background work
        self.called = True
        self.called_with = str(task)
        self.execution_ids.append(execution_id)
        self.constraints.append((topology, max_agents))
        if self.delay:
            time.sleep(self.delay)
        return {"task_id": "task-stub", "synthesis": self.synth}


class _FailingOrchestratorStub(_SimpleOrchestratorStub):
    def run(self, *args, **kwargs):
        raise RuntimeError("provider quota exhausted")


def _stub_init(monkeypatch, tmp_path: Path, orchestrator):
    db_path = tmp_path / "execute-swarm-runtime.db"
    cfg = TGsConfig(db_path=db_path)
    db = Database(db_path=db_path)
    db._init_schema(db._get_connection())
    # Return the orchestrator from _ensure_init when spawn helpers call it
    monkeypatch.setattr(
        mcp_server,
        "_ensure_init",
        lambda: (cfg, db, None, None, orchestrator),
    )
    return cfg, db


def test_fresh_run_streaming_capable(monkeypatch, tmp_path: Path) -> None:
    """When a progress token is provided, runtime handoff should persist final run
    and the heartbeat path should attempt to send progress notifications.
    """
    orchestrator = _SimpleOrchestratorStub()
    cfg, db = _stub_init(monkeypatch, tmp_path, orchestrator)

    notifications: list[tuple[str, dict]] = []

    # Short-circuit the heartbeat loop so it emits one notification quickly.
    def _fake_heartbeat_loop(token, stop_event, interval=15):
        try:
            mcp_server.send_notification("notifications/progress", {"progressToken": token, "progress": 1, "total": 1})
        except Exception:
            pass

    monkeypatch.setattr(mcp_server, "_heartbeat_loop", _fake_heartbeat_loop)
    monkeypatch.setattr(mcp_server, "send_notification", lambda method, payload: notifications.append((method, payload)))

    swarm_id = "swarm-stream-1"
    # Call the handoff directly (streaming-capable shape exercised via heartbeat stub)
    mcp_server._execute_swarm_runtime_handoff(db, orchestrator, swarm_id, {"task": "do-the-thing"}, progress_token="token-1")

    # Orchestrator should have been called and final swarm_run persisted
    assert orchestrator.called is True
    assert orchestrator.execution_ids == [swarm_id]

    summary = db.get_swarm_summary(swarm_id)
    assert summary is not None
    # The handoff persists a completed run record when no prior summary exists
    assert summary["status"] == "completed"

    # Heartbeat stub should have attempted to send one notification
    assert notifications and notifications[0][0] == "notifications/progress"


def test_fresh_run_background_mode_persists_summary(monkeypatch, tmp_path: Path) -> None:
    """When no progress token is provided, the spawn helper should start a
    background handoff that ultimately persists the final SwarmRun record.
    """
    orchestrator = _SimpleOrchestratorStub(delay=0.1)
    cfg, db = _stub_init(monkeypatch, tmp_path, orchestrator)

    swarm_id = "swarm-bg-1"
    # Use the spawn helper which will call _ensure_init() and start a thread
    mcp_server._spawn_execute_swarm_runtime_handoff(db, swarm_id, {"task": "background"})

    # Spawn is immediately visible even while worker execution is still running.
    initial = db.get_swarm_summary(swarm_id)
    assert initial is not None
    assert initial["status"] == "running"

    # Wait for background thread to complete and persist
    deadline = time.time() + 5.0
    while time.time() < deadline:
        summary = db.get_swarm_summary(swarm_id)
        if summary is not None and summary["status"] == "completed":
            break
        time.sleep(0.02)

    assert summary is not None
    assert summary["status"] == "completed"
    assert orchestrator.execution_ids == [swarm_id]

    with db.conn() as conn:
        events = [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM swarm_events WHERE swarm_id = ? ORDER BY id",
                (swarm_id,),
            )
        ]
    assert "runtime_handoff_started" in events


def test_runtime_handoff_propagates_topology_and_agent_constraints(
    monkeypatch, tmp_path: Path
) -> None:
    orchestrator = _SimpleOrchestratorStub()
    _, db = _stub_init(monkeypatch, tmp_path, orchestrator)

    mcp_server._execute_swarm_runtime_handoff(
        db,
        orchestrator,
        "swarm-constraints-1",
        {"task_text": "build it", "topology": "star", "max_agents": 4},
    )

    assert orchestrator.constraints == [("star", 4)]


def test_runtime_failure_preserves_swarm_contract(monkeypatch, tmp_path: Path) -> None:
    orchestrator = _FailingOrchestratorStub()
    _, db = _stub_init(monkeypatch, tmp_path, orchestrator)
    swarm_id = "swarm-failed-contract"
    db.persist_swarm_run({
        "swarm_id": swarm_id,
        "status": "running",
        "requested_agents": 4,
        "effective_agents": 4,
        "topology": "star",
    })

    mcp_server._execute_swarm_runtime_handoff(
        db,
        orchestrator,
        swarm_id,
        {"task_text": "build it", "topology": "star", "max_agents": 4},
    )

    summary = db.get_swarm_summary(swarm_id)
    assert summary is not None
    assert summary["status"] == "failed"
    assert summary["requested_agents"] == 4
    assert summary["effective_agents"] == 4
    assert summary["topology"] == "star"


def test_simulated_channel_drop_does_not_stop_persistence(monkeypatch, tmp_path: Path) -> None:
    """If notifications fail mid-run (broken pipe), the handoff should still
    persist a final summary and make the run inspectable.
    """
    orchestrator = _SimpleOrchestratorStub(delay=0.05)
    cfg, db = _stub_init(monkeypatch, tmp_path, orchestrator)

    # Simulate send_notification raising to emulate a channel drop
    def _broken_send(method, payload):
        raise BrokenPipeError("broken pipe")

    monkeypatch.setattr(mcp_server, "send_notification", _broken_send)

    swarm_id = "swarm-drop-1"
    # Call the handoff directly with a progress token so heartbeat tries to send
    mcp_server._execute_swarm_runtime_handoff(db, orchestrator, swarm_id, {"task": "resilient"}, progress_token="token-drop")

    # Despite notification failures, final SwarmRun must be persisted
    summary = db.get_swarm_summary(swarm_id)
    assert summary is not None
    assert summary["status"] == "completed"
    assert orchestrator.execution_ids == [swarm_id]


def test_materialize_swarm_outputs_writes_file(tmp_path: Path) -> None:
    """_materialize_swarm_outputs writes a target_file and persists a worker snapshot."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from shared.planner import Subtask, ExecutionPlan
    from shared.swarm import WorkerSnapshot
    from dataclasses import dataclass

    db_path = tmp_path / "mat.db"
    db = Database(db_path=db_path)
    db._init_schema(db._get_connection())

    # Build a mock plan with one subtask that has a target_file
    subtask = Subtask(id=1, description="calc", tier="low", model="gpt-5-mini",
                      target_file="calc.py")
    plan = ExecutionPlan(
        analysis="test",
        subtasks=[subtask],
        waves=[[1]],
        total_agents=1,
        strategy="sequential",
        topology="linear",
    )

    @dataclass
    class _FakeResult:
        subtask_id: int
        output: str
        tier: str = "low"
        model: str = "gpt-5-mini"

    result = {
        "plan": plan,
        "results": {1: _FakeResult(subtask_id=1, output="```python\ndef add(a, b):\n    return a + b\n```")},
    }

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    mcp_server._materialize_swarm_outputs(
        db=db,
        swarm_id="swarm-mat-1",
        result=result,
        workspace_root=str(project_dir),
    )

    # File should be written to project_dir/calc.py
    calc = project_dir / "calc.py"
    assert calc.exists(), "target_file was not written to workspace"
    assert "def add" in calc.read_text()

    # Worker snapshot should be persisted
    with db.conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM swarm_workers WHERE swarm_id = 'swarm-mat-1'"
        ).fetchone()[0]
    assert count == 1, "expected one worker snapshot"


def test_workspace_root_stored_in_event_and_resolved_by_spawn(monkeypatch, tmp_path: Path) -> None:
    """workspace_root stored in execute_swarm_requested event is resolved by spawn helper."""
    orchestrator = _SimpleOrchestratorStub()
    cfg, db = _stub_init(monkeypatch, tmp_path, orchestrator)

    captured_workspace: list[str | None] = []

    def _fake_handoff(db_, orch, swarm_id, ctx, started=None, progress_token=None, workspace_root=None):
        captured_workspace.append(workspace_root)

    monkeypatch.setattr(mcp_server, "_execute_swarm_runtime_handoff", _fake_handoff)

    swarm_id = "swarm-ws-1"
    # Store workspace_root in the event payload (simulating handle_execute_swarm)
    mcp_server._log_swarm_event_safe(
        db, swarm_id, "execute_swarm_requested",
        {"task_text": "build a cli", "workspace_root": str(tmp_path / "myproject")},
    )

    mcp_server._spawn_execute_swarm_runtime_handoff(db, swarm_id, "build a cli")

    import time; time.sleep(0.2)  # let background thread run
    assert captured_workspace == [str(tmp_path / "myproject")]
