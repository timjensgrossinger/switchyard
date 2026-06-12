from __future__ import annotations

"""Tests for multi-queen consensus — run_coordinator_consensus and _consensus_active."""

import sys
import tempfile
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.config import TGsConfig
from shared.db import Database
from shared.orchestrator import AgentResult, Orchestrator, Provider
from shared.planner import ExecutionPlan, Planner, Subtask


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class DummyProvider(Provider):
    def resolve_model(self, tier: str) -> str:
        return f"dummy-{tier}"

    def execute(self, subtask: Subtask, model: str, timeout: int = 120) -> str | None:
        return '{"verdict": "complete"}'

    def available_tiers(self) -> list[str]:
        return ["low", "medium", "high"]


class DummyPlanner(Planner):
    def __init__(self) -> None:
        self._backend = SimpleNamespace(call=lambda *args, **kwargs: None)

    def plan(self, *args, **kwargs):
        raise NotImplementedError


def _coordinator_subtask(subtask_id: int = 1) -> Subtask:
    return Subtask(
        id=subtask_id,
        stable_id=f"phase-test-coord-{subtask_id:02d}",
        description="coordinate work",
        tier="medium",
        model="medium",
        depends_on=[],
        is_coordinator=True,
    )


def _cfg(*, enabled: bool = True, queens: int = 2, max_rounds: int = 0) -> TGsConfig:
    cfg = TGsConfig()
    cfg.consensus_enabled = enabled
    cfg.consensus_queens = queens
    cfg.consensus_queen_tier = "low"
    cfg.consensus_judge_tier = "low"
    cfg.consensus_max_rounds = max_rounds
    return cfg


def _make_orchestrator(cfg: TGsConfig, db: Database) -> Orchestrator:
    return Orchestrator(cfg, DummyProvider(), DummyPlanner(), db=db)


# ---------------------------------------------------------------------------
# _consensus_active
# ---------------------------------------------------------------------------

def test_consensus_inactive_when_disabled(temp_db_fixture: Database) -> None:
    orch = _make_orchestrator(_cfg(enabled=False), temp_db_fixture)
    assert orch._consensus_active(1) is False
    assert orch._consensus_active(None) is False


def test_consensus_active_all_rounds_when_max_zero(temp_db_fixture: Database) -> None:
    orch = _make_orchestrator(_cfg(enabled=True, max_rounds=0), temp_db_fixture)
    assert orch._consensus_active(1) is True
    assert orch._consensus_active(99) is True
    assert orch._consensus_active(None) is True


def test_consensus_active_limited_rounds(temp_db_fixture: Database) -> None:
    orch = _make_orchestrator(_cfg(enabled=True, max_rounds=2), temp_db_fixture)
    assert orch._consensus_active(1) is True
    assert orch._consensus_active(2) is True
    assert orch._consensus_active(3) is False


# ---------------------------------------------------------------------------
# run_coordinator_consensus — agreement path (no judge needed)
# ---------------------------------------------------------------------------

def test_consensus_agreement_skips_judge(temp_db_fixture: Database) -> None:
    """When all valid queens agree, no judge subtask is spawned."""
    cfg = _cfg(enabled=True, queens=2)
    orch = _make_orchestrator(cfg, temp_db_fixture)

    decision = {
        "verdict": "complete",
        "result": None,
        "amendment": None,
        "next_work": {},
        "synthesis": {},
        "fallback_reason": None,
    }
    call_count = {"n": 0}

    def fake_sync(*args, **kwargs):
        call_count["n"] += 1
        return dict(decision)

    with unittest.mock.patch.object(orch, "run_coordinator_sync", side_effect=fake_sync):
        with unittest.mock.patch.object(orch, "execute_subtask") as mock_exec:
            result = orch.run_coordinator_consensus(
                _coordinator_subtask(),
                "summary",
                current_round=1,
            )

    assert result["verdict"] == "complete"
    assert result["consensus"]["agreement"] is True
    assert result["consensus"]["judge_used"] is False
    mock_exec.assert_not_called()  # judge was not needed


# ---------------------------------------------------------------------------
# run_coordinator_consensus — disagreement → judge selects
# ---------------------------------------------------------------------------

def test_consensus_disagreement_judge_selects(temp_db_fixture: Database) -> None:
    """When queens disagree, judge is called and its selection is honoured."""
    cfg = _cfg(enabled=True, queens=2)
    orch = _make_orchestrator(cfg, temp_db_fixture)

    proposals = [
        {"verdict": "complete", "result": None, "amendment": None,
         "next_work": {"focus": "A"}, "synthesis": {}, "fallback_reason": None},
        {"verdict": "complete", "result": None, "amendment": None,
         "next_work": {"focus": "B"}, "synthesis": {}, "fallback_reason": None},
    ]
    call_iter = iter(proposals)

    def fake_sync(*args, **kwargs):
        return dict(next(call_iter))

    judge_output = AgentResult(
        subtask_id=10000,
        output='{"selected": 1, "reason": "better"}',
        provider_name="dummy",
        model="dummy-low",
        tier="low",
        token_count=10,
    )

    with unittest.mock.patch.object(orch, "run_coordinator_sync", side_effect=fake_sync):
        with unittest.mock.patch.object(orch, "execute_subtask", return_value=judge_output):
            result = orch.run_coordinator_consensus(
                _coordinator_subtask(),
                "summary",
                current_round=1,
            )

    assert result["verdict"] == "complete"
    assert result["next_work"] == {"focus": "B"}
    assert result["consensus"]["judge_used"] is True
    assert result["consensus"]["selected"] == 1


# ---------------------------------------------------------------------------
# run_coordinator_consensus — all queens fail → single coordinator degrade
# ---------------------------------------------------------------------------

def test_consensus_all_queens_fail_degrades(temp_db_fixture: Database) -> None:
    """When no valid proposals exist, falls back to single run_coordinator_sync."""
    cfg = _cfg(enabled=True, queens=2)
    orch = _make_orchestrator(cfg, temp_db_fixture)

    fallback_decision = {
        "verdict": "complete",
        "result": None,
        "amendment": None,
        "next_work": {},
        "synthesis": {},
        "fallback_reason": None,
    }
    queen_decision = {
        "verdict": "fallback",
        "result": None,
        "amendment": None,
        "next_work": {},
        "synthesis": {},
        "fallback_reason": "coordinator execution error",
    }

    calls = {"queen": 0, "fallback": 0}

    def fake_sync(subtask, *args, **kwargs):
        if subtask.id == _coordinator_subtask().id:
            calls["fallback"] += 1
            return dict(fallback_decision)
        calls["queen"] += 1
        return dict(queen_decision)

    with unittest.mock.patch.object(orch, "run_coordinator_sync", side_effect=fake_sync):
        result = orch.run_coordinator_consensus(
            _coordinator_subtask(),
            "summary",
            current_round=1,
        )

    assert result["verdict"] == "complete"
    assert result["consensus"]["degraded"] is True
    assert result["consensus"]["valid"] == 0
    assert calls["fallback"] == 1


# ---------------------------------------------------------------------------
# run_coordinator_consensus — garbage judge output → deterministic fallback
# ---------------------------------------------------------------------------

def test_consensus_garbage_judge_deterministic_fallback(temp_db_fixture: Database) -> None:
    """Garbage judge output falls back to first complete proposal."""
    cfg = _cfg(enabled=True, queens=2)
    orch = _make_orchestrator(cfg, temp_db_fixture)

    proposals = [
        {"verdict": "complete", "result": None, "amendment": None,
         "next_work": {"x": 1}, "synthesis": {}, "fallback_reason": None},
        {"verdict": "another-pass", "result": None,
         "amendment": {"add": []}, "next_work": {"x": 2}, "synthesis": {}, "fallback_reason": None},
    ]
    call_iter = iter(proposals)

    def fake_sync(*args, **kwargs):
        return dict(next(call_iter))

    bad_judge = AgentResult(
        subtask_id=10000,
        output="not json at all",
        provider_name="dummy",
        model="dummy-low",
        tier="low",
        token_count=5,
    )

    with unittest.mock.patch.object(orch, "run_coordinator_sync", side_effect=fake_sync):
        with unittest.mock.patch.object(orch, "execute_subtask", return_value=bad_judge):
            result = orch.run_coordinator_consensus(
                _coordinator_subtask(),
                "summary",
                current_round=1,
            )

    assert result["verdict"] == "complete"
    assert result["next_work"] == {"x": 1}
    assert result["consensus"]["judge_used"] is False


# ---------------------------------------------------------------------------
# run_coordinator_consensus — single valid queen (no judge)
# ---------------------------------------------------------------------------

def test_consensus_single_valid_queen_no_judge(temp_db_fixture: Database) -> None:
    """One valid + one fallback queen → use the valid one directly."""
    cfg = _cfg(enabled=True, queens=2)
    orch = _make_orchestrator(cfg, temp_db_fixture)

    proposals = [
        {"verdict": "complete", "result": None, "amendment": None,
         "next_work": {}, "synthesis": {}, "fallback_reason": None},
        {"verdict": "fallback", "result": None, "amendment": None,
         "next_work": {}, "synthesis": {}, "fallback_reason": "error"},
    ]
    call_iter = iter(proposals)

    def fake_sync(*args, **kwargs):
        return dict(next(call_iter))

    with unittest.mock.patch.object(orch, "run_coordinator_sync", side_effect=fake_sync):
        with unittest.mock.patch.object(orch, "execute_subtask") as mock_exec:
            result = orch.run_coordinator_consensus(
                _coordinator_subtask(),
                "summary",
                current_round=1,
            )

    assert result["verdict"] == "complete"
    assert result["consensus"]["judge_used"] is False
    mock_exec.assert_not_called()


# ---------------------------------------------------------------------------
# Consensus disabled → existing star tests unaffected
# ---------------------------------------------------------------------------

def test_consensus_disabled_call_site_uses_sync(temp_db_fixture: Database) -> None:
    """When disabled, _consensus_active is False so the call site uses run_coordinator_sync."""
    cfg = _cfg(enabled=False)
    orch = _make_orchestrator(cfg, temp_db_fixture)
    assert orch._consensus_active(1) is False
    assert orch._consensus_active(None) is False


# ---------------------------------------------------------------------------
# Swarm events logged
# ---------------------------------------------------------------------------

def test_consensus_logs_swarm_event(temp_db_fixture: Database) -> None:
    cfg = _cfg(enabled=True, queens=2)
    orch = _make_orchestrator(cfg, temp_db_fixture)

    decision = {
        "verdict": "complete", "result": None,
        "amendment": None, "next_work": {}, "synthesis": {}, "fallback_reason": None,
    }

    with unittest.mock.patch.object(orch, "run_coordinator_sync", return_value=dict(decision)):
        orch.run_coordinator_consensus(
            _coordinator_subtask(), "ctx",
            execution_id="exec-123",
            current_round=1,
        )

    with temp_db_fixture.conn() as conn:
        events = conn.execute(
            "SELECT event_type FROM swarm_events WHERE swarm_id = ?", ("exec-123",)
        ).fetchall()
    event_types = {row[0] for row in events}
    assert "consensus_vote" in event_types
