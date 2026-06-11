#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_server
from shared.config import TGsConfig
from shared.db import Database


def _stub_init(monkeypatch, tmp_path: Path) -> Database:
    db_path = tmp_path / "topology-explain.db"
    cfg = TGsConfig(db_path=db_path)
    db = Database(db_path=db_path)
    db._init_schema(db._get_connection())
    monkeypatch.setattr(
        mcp_server,
        "_ensure_init",
        lambda: (cfg, db, None, None, None),
    )
    return db


def test_execute_swarm_auto_topology_exposes_rationale(monkeypatch, tmp_path: Path) -> None:
    _stub_init(monkeypatch, tmp_path)
    mcp_server._execute_swarm_rate_limit.clear()
    monkeypatch.setattr(mcp_server, "_resolve_caller", lambda: "topology-explain")

    result = mcp_server.handle_execute_swarm(
        {
            "task": "Incident blocked today, parallelize immediately.",
            "max_agents": 8,
            "urgency_hint": "ASAP outage today",
        }
    )

    assert result["started"] is True
    payload = result["result"]
    assert payload["swarm_id"].startswith("swarm-")
    assert payload["selected_topology"] == "star"
    assert payload["topology_rationale"] == "urgency_high"
    assert payload["requested_vs_effective_agent_count"] == {
        "requested": 8,
        "effective": 8,
    }
    assert payload["effective_values"]["topology"] == "star"
