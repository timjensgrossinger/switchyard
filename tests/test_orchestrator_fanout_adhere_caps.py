#!/usr/bin/env python3
"""
Tests for orchestrator fan-out cap enforcement and urgency explainability telemetry.
"""
import sys
import tempfile
import time
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from shared.config import TGsConfig
from shared.db import Database
from shared.orchestrator import fan_out_task, Orchestrator, Provider, AgentResult
from shared.planner import Subtask


class DummyProvider(Provider):
    def resolve_model(self, tier: str) -> str:
        return f"dummy-{tier}"

    def execute(self, subtask: Subtask, model: str, timeout: int = 120) -> str | None:
        # simple echo output
        return f"{model}:output-for-{subtask.id}"

    def available_tiers(self) -> list[str]:
        return ["low", "medium", "high"]


class DummyPlanner(SimpleNamespace):
    pass


class StubOrchestrator(Orchestrator):
    def __init__(self, config: TGsConfig, db: Database):
        super().__init__(config, DummyProvider(), DummyPlanner(), db=db)

    def execute_subtask(
        self,
        subtask: Subtask,
        timeout: int = 120,
        score: float | None = None,
        *,
        execution_id: str | None = None,
        plan_revision: int = 1,
        current_wave: int | None = None,
    ) -> AgentResult:
        assert self._db is not None
        # record a lightweight agent result for telemetry
        self._db.log_agent_result(
            session_id="fanout-test",
            task_hash=f"task-{subtask.id}",
            agent_id=subtask.id,
            tier=subtask.tier,
            model="dummy-low",
        )
        # small token usage
        return AgentResult(
            subtask_id=subtask.id,
            tier=subtask.tier,
            model="dummy-low",
            output=f"completed {subtask.id}",
            token_count=2,
        )


def _build_config(max_workers: int) -> TGsConfig:
    cfg = TGsConfig()
    cfg.parallelism.enabled = True
    cfg.parallelism.max_workers = max_workers
    return cfg


def _task_id_for_description(desc: str) -> str:
    return hashlib.sha256(desc.encode("utf-8", errors="replace")).hexdigest()[:16]


def test_orchestrator_rejects_fanout_above_budget() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "reject.db")
        try:
            config = _build_config(max_workers=10)
            orchestrator = StubOrchestrator(config, db)

            # 3 high-confidence domains, but total configured budget is too small
            domains = [
                {"name": "A", "confidence": 0.95, "tier": "low"},
                {"name": "B", "confidence": 0.9, "tier": "low"},
                {"name": "C", "confidence": 0.9, "tier": "low"},
            ]
            task_desc = "urgent: please run cross-domain analysis asap"
            task = {
                "opt_in_fanout": True,
                "domains": domains,
                "description": task_desc,
                "budget_limit": 50,  # total allowed budget too small for 3 * per_router_budget
                "urgency_score": 0.9,
                "matched_urgency_signals": ["asap"],
            }

            # Request per-router budget that would make total budget 300
            result = fan_out_task(task, max_routers=3, per_router_budget=100, orchestrator=orchestrator, db=db)

            assert result.get("fallback") == "single_route"
            assert result.get("reason") == "caps_exceeded"

            # Verify telemetry row recorded with urgency metadata
            task_id = _task_id_for_description(task_desc)
            with db.conn() as conn:
                row = conn.execute("SELECT budget_accounting FROM fanout_telemetry WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
                assert row is not None, "Expected a fanout_telemetry row"
                payload = json.loads(row[0])
                assert "urgency" in payload, payload
                urgency = payload["urgency"]
                assert urgency.get("final_action") in ("fallback_to_linear", "rejected")
                assert urgency.get("requested_router_count") == 3
                assert urgency.get("urgency_score") == 0.9
        finally:
            db.close()


def test_orchestrator_allows_safe_urgency_fanout() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "allow.db")
        try:
            config = _build_config(max_workers=4)
            orchestrator = StubOrchestrator(config, db)

            domains = [
                {"name": "A", "confidence": 0.95, "tier": "low"},
                {"name": "B", "confidence": 0.9, "tier": "low"},
            ]
            task_desc = "please prioritize this soon"
            task = {
                "opt_in_fanout": True,
                "domains": domains,
                "description": task_desc,
                "budget_limit": 100,  # plenty for 2 routers
                "urgency_score": 0.7,
                "matched_urgency_signals": ["soon"],
            }

            result = fan_out_task(task, max_routers=3, per_router_budget=10, orchestrator=orchestrator, db=db)

            assert result.get("result") is not None or result.get("per_domain")
            assert len(result.get("per_domain", [])) == 2

            task_id = _task_id_for_description(task_desc)
            with db.conn() as conn:
                row = conn.execute("SELECT budget_accounting FROM fanout_telemetry WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
                assert row is not None, "Expected a fanout_telemetry row"
                payload = json.loads(row[0])
                # In allowed case we embed urgency under the 'urgency' key by helper
                assert "urgency" in payload
                urgency = payload["urgency"]
                assert urgency.get("final_action") == "allowed"
                assert urgency.get("urgency_score") == 0.7
        finally:
            db.close()
