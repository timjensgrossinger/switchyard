"""Tests for plan 14 — quality convergence loops."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from shared.config import ConvergenceConfig, TGsConfig
from shared.orchestrator import AgentResult, Orchestrator
from shared.planner import ConvergenceTarget, Subtask

def test_convergence_target_defaults():
    ct = ConvergenceTarget()
    assert ct.min_score == 0.8
    assert ct.max_rounds == 3
    assert ct.backoff_seconds == 0.0

def test_convergence_target_custom():
    ct = ConvergenceTarget(min_score=0.5, max_rounds=5, backoff_seconds=1.0)
    assert ct.min_score == 0.5
    assert ct.max_rounds == 5
    assert ct.backoff_seconds == 1.0

def test_subtask_convergence_target_none_by_default():
    st = Subtask(id=1, description="do something", tier="low")
    assert st.convergence_target is None

def test_subtask_convergence_target_assignable():
    ct = ConvergenceTarget(min_score=0.9, max_rounds=2)
    st = Subtask(id=1, description="do something", tier="low", convergence_target=ct)
    assert st.convergence_target is ct

def test_agent_result_convergence_defaults():
    r = AgentResult(subtask_id=1, tier="low", model="haiku", output="ok", token_count=10)
    assert r.convergence_rounds_data is None
    assert r.convergence_exhausted is False

def test_convergence_config_defaults():
    cfg = ConvergenceConfig()
    assert cfg.enabled is True
    assert cfg.default_min_score == 0.0
    assert cfg.default_max_rounds == 3

def test_tgs_config_has_convergence():
    cfg = TGsConfig()
    assert hasattr(cfg, "convergence")
    assert isinstance(cfg.convergence, ConvergenceConfig)

def _make_result(**kwargs) -> AgentResult:
    defaults = dict(subtask_id=1, tier="low", model="haiku", output="ok", token_count=10)
    defaults.update(kwargs)
    return AgentResult(**defaults)

def test_gate_score_none_verdict_is_1():
    r = _make_result(gate_verdict=None)
    assert Orchestrator._gate_score_from_result(r) == 1.0

def test_gate_score_pass_verdict_is_1():
    r = _make_result(gate_verdict="pass")
    assert Orchestrator._gate_score_from_result(r) == 1.0

def test_gate_score_rejected_no_signals_is_0():
    r = _make_result(gate_verdict="rejected", gate_signals={})
    assert Orchestrator._gate_score_from_result(r) == 0.0

def test_gate_score_rejected_partial_signals():
    r = _make_result(
        gate_verdict="rejected",
        gate_signals={"lint": {"passed": True}, "types": {"passed": False}},
    )
    assert Orchestrator._gate_score_from_result(r) == 0.5

def test_gate_score_warn_no_signals_default():
    r = _make_result(gate_verdict="warn", gate_signals={})
    assert Orchestrator._gate_score_from_result(r) == 0.7

def test_gate_score_warn_all_pass():
    r = _make_result(
        gate_verdict="warn",
        gate_signals={"lint": {"passed": True}, "types": {"passed": True}},
    )
    assert Orchestrator._gate_score_from_result(r) == 1.0

def _make_orchestrator():
    orch = MagicMock(spec=Orchestrator)
    orch._gate_score_from_result = Orchestrator._gate_score_from_result
    orch._execute_subtask_with_gate = Orchestrator._execute_subtask_with_gate.__get__(orch, Orchestrator)
    return orch

def test_no_convergence_target_calls_once():
    orch = _make_orchestrator()
    result = _make_result(gate_verdict="pass")
    orch._execute_subtask_with_prefetch.return_value = result
    orch._run_verify_gate.return_value = result
    st = Subtask(id=1, description="task", tier="low")
    out = orch._execute_subtask_with_gate(
        st, 30, score=None, execution_id="e1", plan_revision=1, current_wave=0,
    )
    orch._execute_subtask_with_prefetch.assert_called_once()
    orch._run_verify_gate.assert_called_once()
    assert out.convergence_rounds_data is None

def test_convergence_accepts_on_round_3():
    orch = _make_orchestrator()
    call_count = [0]

    def fake_execute(st, timeout, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        verdicts = ["rejected", "warn", "pass"]
        signals = [
            {"lint": {"passed": False}, "types": {"passed": False}},
            {"lint": {"passed": True}, "types": {"passed": False}},
            {"lint": {"passed": True}, "types": {"passed": True}},
        ]
        return _make_result(gate_verdict=verdicts[idx], gate_signals=signals[idx])

    orch._execute_subtask_with_prefetch.side_effect = fake_execute
    orch._run_verify_gate.side_effect = lambda st, r: r

    ct = ConvergenceTarget(min_score=0.9, max_rounds=3, backoff_seconds=0.0)
    st = Subtask(id=1, description="task", tier="low", convergence_target=ct)
    out = orch._execute_subtask_with_gate(
        st, 30, score=None, execution_id="e1", plan_revision=1, current_wave=0,
    )

    assert call_count[0] == 3
    assert len(out.convergence_rounds_data) == 3
    assert out.convergence_rounds_data[2]["score"] >= 0.9
    assert out.convergence_exhausted is False

def test_convergence_exhausted_marks_rejected():
    orch = _make_orchestrator()
    orch._execute_subtask_with_prefetch.side_effect = lambda st, t, **kw: _make_result(
        gate_verdict="rejected", gate_signals={}
    )
    orch._run_verify_gate.side_effect = lambda st, r: r

    ct = ConvergenceTarget(min_score=0.9, max_rounds=3, backoff_seconds=0.0)
    st = Subtask(id=1, description="task", tier="low", convergence_target=ct)
    out = orch._execute_subtask_with_gate(
        st, 30, score=None, execution_id="e1", plan_revision=1, current_wave=0,
    )

    assert out.convergence_exhausted is True
    assert out.success is False
    assert len(out.convergence_rounds_data) == 3

def test_convergence_round_idempotency_keys_distinct():
    orch = _make_orchestrator()
    orch._execute_subtask_with_prefetch.side_effect = lambda st, t, **kw: _make_result(
        gate_verdict="rejected", gate_signals={}
    )
    orch._run_verify_gate.side_effect = lambda st, r: r

    ct = ConvergenceTarget(min_score=0.9, max_rounds=3)
    st = Subtask(id=42, description="task", tier="low", convergence_target=ct)
    out = orch._execute_subtask_with_gate(
        st, 30, score=None, execution_id="exec-1", plan_revision=1, current_wave=0,
    )

    keys = [r["idem_key"] for r in out.convergence_rounds_data]
    assert len(keys) == len(set(keys))
    assert all(":round:" in k for k in keys)

def test_routing_outcomes_has_convergence_rounds_column(tmp_path):
    from shared.db import Database
    db = Database(tmp_path / "test.db")
    with db.conn() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(routing_outcomes)").fetchall()}
    assert "convergence_rounds" in cols

def test_convergence_rounds_persisted_as_json(tmp_path):
    from shared.db import Database
    from shared.outcomes import record_outcome
    db = Database(tmp_path / "test.db")
    record_outcome(db, task_id="task-conv-1", outcome="accepted")
    rounds = [{"round": 1, "score": 0.6, "idem_key": "k1", "output": "x"}]
    with db.conn() as conn:
        conn.execute(
            "UPDATE routing_outcomes SET convergence_rounds = ? WHERE task_id = ?",
            (json.dumps(rounds), "task-conv-1"),
        )
    with db.conn() as conn:
        row = conn.execute(
            "SELECT convergence_rounds FROM routing_outcomes WHERE task_id = ?",
            ("task-conv-1",),
        ).fetchone()
    assert row is not None
    stored = json.loads(row[0])
    assert stored[0]["round"] == 1
    assert stored[0]["score"] == 0.6