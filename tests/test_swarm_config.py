#!/usr/bin/env python3
"""Tests for Phase 31 swarm config and cap enforcement scaffolding."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server import prepare_swarm_execution_request
from shared.config import TGsConfig
from shared.db import Database


def test_max_agents_default_and_clamp() -> None:
    """Over-cap swarm requests should clamp to the hard cap and persist telemetry."""
    with tempfile.NamedTemporaryFile(suffix=".db") as handle:
        db = Database(Path(handle.name))
        config = TGsConfig.defaults()

        prepared = prepare_swarm_execution_request(
            {"max_agents": 20},
            config=config,
            db=db,
            swarm_id="swarm-cap-test",
        )

        assert config.swarm_max_agents == 12
        assert prepared["requested_agents"] == 20
        assert prepared["effective_agents"] == 12
        assert prepared["clamped"] is True
        assert prepared["requested_vs_effective_agent_count"] == {
            "requested": 20,
            "effective": 12,
        }

        with db.conn() as conn:
            run_row = conn.execute(
                """
                SELECT requested_agents, effective_agents
                FROM swarm_runs
                WHERE swarm_id = ?
                """,
                ("swarm-cap-test",),
            ).fetchone()
            event_row = conn.execute(
                """
                SELECT payload
                FROM swarm_events
                WHERE swarm_id = ? AND event_type = ?
                """,
                ("swarm-cap-test", "cap_event"),
            ).fetchone()

        assert run_row == (20, 12)
        assert event_row is not None
        payload = json.loads(event_row[0])
        assert payload["requested"] == 20
        assert payload["effective"] == 12
        db.close()


def test_invalid_swarm_max_agents_config_falls_back_to_default() -> None:
    """Malformed swarm.max_agents config should fall back to the default hard cap."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.yaml"
        config_path.write_text(
            "parallelism:\n"
            "  enabled: true\n"
            "  max_workers: 9\n"
            "swarm:\n"
            "  max_agents: nope\n",
            encoding="utf-8",
        )
        config = TGsConfig.from_yaml(config_path)
        assert config.swarm_max_agents == 12
