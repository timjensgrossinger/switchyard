"""
End-to-end integration test: telemetry → record outcome → compute snapshot → verify

This test exercises the full observability pipeline from data recording through snapshot computation.
"""

from __future__ import annotations

import time
from pathlib import Path
import tempfile

import pytest

from shared.db import Database
from shared.outcomes import compute_learning_outcome_snapshot
from shared.memory import memory_get, memory_set, MemoryNotFoundError


def test_integration_outcome_recording_to_snapshot_computation() -> None:
    """
    Full pipeline test: insert telemetry, record outcomes, compute snapshot, query memory.
    
    Scenario: 40 tasks routed (telemetry), 35 with recorded outcomes.
    Expected coverage: 87.5% (35/40)
    Expected distribution: 30 accepted, 2 revised, 1 rejected, 2 reworked
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "integration-test.db"
        db = Database(db_path=db_path)
        
        now = time.time()
        cutoff = now - 3600  # 1 hour window
        
        # Step 1: Insert 40 telemetry records (simulating 40 routed tasks)
        with db.conn() as conn:
            for i in range(40):
                ts = cutoff + 100 + i*60  # Spread across window
                conn.execute(
                    """
                    INSERT INTO telemetry (ts, tier, model, provider_name)
                    VALUES (?, ?, ?, ?)
                    """,
                    (ts, "low", "gpt-5-mini", "test-provider"),
                )
            
            # Step 2: Record 35 outcomes (various types) for 35 of the 40 tasks
            outcomes_spec = [
                ("task-0", "accepted"),
                ("task-1", "accepted"),
                ("task-2", "accepted"),
                ("task-3", "accepted"),
                ("task-4", "accepted"),
                ("task-5", "revised"),
                ("task-6", "revised"),
                ("task-7", "rejected"),
                ("task-8", "reworked"),
                ("task-9", "reworked"),
            ]
            
            # Record first batch of outcomes (first 10)
            for task_id, outcome in outcomes_spec:
                conn.execute(
                    """
                    INSERT INTO routing_outcomes (
                        task_id, current_outcome, recorded_at, tier, model,
                        provider_name, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (task_id, outcome, cutoff + 150, "low", "gpt-5-mini", "test-provider", cutoff + 150),
                )
            
            # Record 25 more accepted outcomes (to get 30 total accepted)
            for i in range(10, 35):
                conn.execute(
                    """
                    INSERT INTO routing_outcomes (
                        task_id, current_outcome, recorded_at, tier, model,
                        provider_name, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (f"task-{i}", "accepted", cutoff + 150 + i*10, "low", "gpt-5-mini", "test-provider", cutoff + 150 + i*10),
                )
        
        # Step 3: Compute snapshot (simulates warm-path executor background task)
        compute_learning_outcome_snapshot(db)
        
        # Step 4: Query via memory
        try:
            result = memory_get("global", "learning_stats", db=db)
            snapshot = result.get("value", {})
        except MemoryNotFoundError:
            pytest.fail("Snapshot should be stored in memory after computation")
        
        # Step 5: Verify response structure and exact values
        assert snapshot, "Snapshot should not be empty"
        
        # Verify coverage percentage
        assert snapshot["coverage_percentage"] == 87.5, f"Expected 87.5% coverage (35/40), got {snapshot['coverage_percentage']}"
        
        # Verify task counts
        assert snapshot["total_tasks_in_window"] == 40, f"Expected 40 total tasks, got {snapshot['total_tasks_in_window']}"
        assert snapshot["tasks_with_feedback"] == 35, f"Expected 35 tasks with feedback, got {snapshot['tasks_with_feedback']}"
        
        # Verify outcome distribution
        dist = snapshot["outcome_distribution"]
        assert "low:gpt-5-mini" in dist, "Expected 'low:gpt-5-mini' in distribution"
        
        tier_model_dist = dist["low:gpt-5-mini"]
        assert tier_model_dist["accepted"] == 30, f"Expected 30 accepted, got {tier_model_dist['accepted']}"
        assert tier_model_dist["revised"] == 2, f"Expected 2 revised, got {tier_model_dist['revised']}"
        assert tier_model_dist["rejected"] == 1, f"Expected 1 rejected, got {tier_model_dist['rejected']}"
        assert tier_model_dist["reworked"] == 2, f"Expected 2 reworked, got {tier_model_dist['reworked']}"
        
        # Verify window timestamps are present and reasonable
        assert "window_start_time" in snapshot
        assert "window_end_time" in snapshot
        assert "computed_at" in snapshot
        # Window start should be approximately 1 hour before window end (allow 1 second drift)
        assert snapshot["window_end_time"] - snapshot["window_start_time"] >= 3599


def test_integration_snapshot_with_multiple_models() -> None:
    """
    Verify snapshot correctly aggregates across multiple tier:model combinations.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "multi-model.db"
        db = Database(db_path=db_path)
        
        now = time.time()
        cutoff = now - 3600
        
        # Insert telemetry and outcomes for multiple tier:model combinations
        models = [
            ("low", "gpt-5-mini"),
            ("medium", "claude-sonnet-4"),
            ("high", "o1-preview"),
        ]
        
        with db.conn() as conn:
            total_telemetry = 0
            for tier, model in models:
                # Insert 10 telemetry for each model
                for i in range(10):
                    ts = cutoff + 100 + (total_telemetry * 60)
                    conn.execute(
                        """
                        INSERT INTO telemetry (ts, tier, model, provider_name)
                        VALUES (?, ?, ?, ?)
                        """,
                        (ts, tier, model, "test-provider"),
                    )
                    
                    # Record outcome for each telemetry
                    conn.execute(
                        """
                        INSERT INTO routing_outcomes (
                            task_id, current_outcome, recorded_at, tier, model,
                            provider_name, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (f"task-{tier}-{i}", "accepted", ts, tier, model, "test-provider", ts),
                    )
                    
                    total_telemetry += 1
        
        # Compute snapshot
        compute_learning_outcome_snapshot(db)
        
        # Query via memory
        try:
            result = memory_get("global", "learning_stats", db=db)
            snapshot = result.get("value", {})
        except MemoryNotFoundError:
            pytest.fail("Snapshot should be stored after computation")
        
        # Verify snapshot
        assert snapshot["coverage_percentage"] == 100.0  # All tasks have outcomes
        assert snapshot["total_tasks_in_window"] == 30
        assert snapshot["tasks_with_feedback"] == 30
        
        # Verify distribution contains all models
        dist = snapshot["outcome_distribution"]
        assert "low:gpt-5-mini" in dist
        assert "medium:claude-sonnet-4" in dist
        assert "high:o1-preview" in dist
        
        # Each model should have exactly 10 accepted outcomes
        for tier, model in models:
            key = f"{tier}:{model}"
            assert dist[key]["accepted"] == 10
            assert dist[key]["revised"] == 0
            assert dist[key]["rejected"] == 0
            assert dist[key]["reworked"] == 0


def test_integration_snapshot_outside_window() -> None:
    """
    Verify that outcomes outside the 1-hour window are not included in snapshot.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "window-boundary.db"
        db = Database(db_path=db_path)
        
        now = time.time()
        cutoff = now - 3600  # 1 hour window
        
        with db.conn() as conn:
            # Insert outcomes: 20 within window, 10 outside
            
            # Within window (20 outcomes)
            for i in range(20):
                ts = cutoff + 100 + i*60  # Well within window
                conn.execute(
                    """
                    INSERT INTO routing_outcomes (
                        task_id, current_outcome, recorded_at, tier, model,
                        provider_name, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (f"task-in-{i}", "accepted", ts, "low", "gpt-5-mini", "test-provider", ts),
                )
            
            # Outside window (10 outcomes, before cutoff)
            for i in range(10):
                ts = cutoff - 100 - i*60  # Before the cutoff
                conn.execute(
                    """
                    INSERT INTO routing_outcomes (
                        task_id, current_outcome, recorded_at, tier, model,
                        provider_name, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (f"task-out-{i}", "accepted", ts, "low", "gpt-5-mini", "test-provider", ts),
                )
            
            # Also insert telemetry for the 20 within window
            for i in range(20):
                ts = cutoff + 100 + i*60
                conn.execute(
                    """
                    INSERT INTO telemetry (ts, tier, model, provider_name)
                    VALUES (?, ?, ?, ?)
                    """,
                    (ts, "low", "gpt-5-mini", "test-provider"),
                )
        
        # Compute snapshot
        compute_learning_outcome_snapshot(db)
        
        # Query via memory
        try:
            result = memory_get("global", "learning_stats", db=db)
            snapshot = result.get("value", {})
        except MemoryNotFoundError:
            pytest.fail("Snapshot should be stored after computation")
        
        # Verify only 20 outcomes (within window) are included
        assert snapshot["total_tasks_in_window"] == 20
        assert snapshot["tasks_with_feedback"] == 20
        assert snapshot["coverage_percentage"] == 100.0
        
        dist = snapshot["outcome_distribution"]
        assert dist["low:gpt-5-mini"]["accepted"] == 20
