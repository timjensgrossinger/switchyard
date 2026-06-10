from pathlib import Path
import time
import json
import tempfile

from shared.db import Database
from shared.planner import ExecutionPlan, Subtask, FanOutConfig, evaluate_fanout, build_waves


def test_fanout_columns_written():
    # Use a real temporary file-backed SQLite DB so multiple connections share schema
    with tempfile.NamedTemporaryFile(suffix=".db") as tf:
        db = Database(db_path=Path(tf.name))

        # Build a simple parallel plan with two subtasks
        subtasks = [
            Subtask(id=1, description="one", tier="low"),
            Subtask(id=2, description="two", tier="low"),
        ]
        waves = build_waves(subtasks)
        plan = ExecutionPlan(
            analysis="test",
            subtasks=subtasks,
            waves=waves,
            total_agents=2,
            strategy="parallel",
            estimated_agent_tokens=100,
        )

        config = FanOutConfig(opt_in_fanout=True, max_routers=2, budget_limit=1000)

        # Run evaluation which should write telemetry with explainability fields
        decision = evaluate_fanout(plan, config=config, db=db, urgency_score=0.7)
        assert decision.enabled

        # Verify telemetry schema contains the new columns
        with db.conn() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(telemetry)").fetchall()}
            assert "urgency_score" in cols
            assert "selected_topology" in cols
            assert "fanout_final_action" in cols

            row = conn.execute("SELECT urgency_score, selected_topology, fanout_final_action FROM telemetry ORDER BY id DESC LIMIT 1").fetchone()
            assert row is not None
            urgency_score_val, selected_topology, fanout_final_action = row
            assert urgency_score_val is not None
            # With urgency_score 0.7 and a parallel plan, planner prefers 'star'
            assert selected_topology == 'star' or selected_topology is None
            assert fanout_final_action is not None


def test_telemetry_backward_compatibility():
    with tempfile.NamedTemporaryFile(suffix=".db") as tf:
        db = Database(db_path=Path(tf.name))
        with db.conn() as conn:
            # Insert an older-style minimal telemetry row (simulate older writer)
            conn.execute(
                "INSERT INTO telemetry (session_id, task_hash, agent_id, tier, model, ts) VALUES (?, ?, ?, ?, ?, ?)",
                ("legacy", "oldhash", 1, "low", "legacy-model", time.time()),
            )

            # Reading rows should not crash and newer columns should be readable (NULL)
            row = conn.execute("SELECT session_id, urgency_score FROM telemetry WHERE session_id = ?", ("legacy",)).fetchone()
            assert row is not None
            assert row[0] == "legacy"
            # urgency_score should be NULL for legacy row but selecting it must succeed
            assert row[1] is None
