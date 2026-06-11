#!/usr/bin/env python3
"""
Wave 0 integration tests for agent pattern tracking and conservative draft gate.

Tests verify:
1. Real subtask outcomes record into subtask_patterns table
2. Occurrence counts increment predictably
3. Draft gate correctly rejects patterns with high rework or weak eval
4. Draft gate correctly enqueues drafts meeting all conditions
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure the project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.db import Database
from shared.planner import Subtask
from shared.orchestrator import Orchestrator
from shared.config import TGsConfig
from shared.agents import pattern_hash, check_draft_ready, evaluate_pattern_readiness, _detect_lane


# ============================================================================
# Test 1: Basic pattern recording
# ============================================================================


def test_draft_gate_recurrence_threshold(agent_learning_db: Database, mature_pattern_seed: dict):
    """
    Test that check_draft_ready returns True when all three gate conditions pass.
    
    Setup: Use mature_pattern_seed fixture which provides:
        - occurrence_count = 5 (meets threshold)
        - rework_detected = False (positive signal)
        - eval_quality = 0.85 (acceptable quality)
    
    Expected: Draft should be enqueued to approval_queue
    """
    project_id = mature_pattern_seed["project_id"]
    pattern_hash_val = mature_pattern_seed["pattern_hash"]
    
    # Before: approval_queue should be empty
    with agent_learning_db.conn() as conn:
        count_before = conn.execute(
            "SELECT COUNT(*) FROM approval_queue"
        ).fetchone()[0]
    
    assert count_before == 0, "approval_queue should be empty before test"
    
    # Call check_draft_ready
    result = check_draft_ready(agent_learning_db, project_id, pattern_hash_val)
    
    # Should return True (all conditions met)
    assert result is True, "check_draft_ready should return True when conditions pass"
    
    # After: approval_queue should have new entry
    with agent_learning_db.conn() as conn:
        count_after = conn.execute(
            "SELECT COUNT(*) FROM approval_queue"
        ).fetchone()[0]
    
    assert count_after > count_before, "approval_queue should have new entry after check_draft_ready"


# ============================================================================
# Test 2: Block high rework
# ============================================================================


def test_draft_gate_blocks_high_rework(agent_learning_db: Database):
    """
    Test that check_draft_ready returns False when rework_detected is True.
    
    Setup: Insert pattern with:
        - occurrence_count = 10 (above threshold)
        - rework_detected = True (negative signal — blocks drafting)
        - eval_quality = 0.85 (good quality, but rework blocks it)
    
    Expected: check_draft_ready returns False, approval_queue stays empty
    """
    from shared.agents import pattern_hash
    
    project_id = "test-project"
    description = "pattern with rework detected"
    ph = pattern_hash(description)
    
    # Insert pattern with rework_detected = True
    with agent_learning_db.conn() as conn:
        import time
        import json
        conn.execute(
            """
            INSERT OR REPLACE INTO subtask_patterns
            (pattern_hash, pattern_desc, occurrence_count, tier, last_seen, examples, rework_detected, eval_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ph,
                description,
                10,  # High recurrence
                "low",
                time.time(),
                json.dumps(["example"]),
                1,  # rework_detected = True (INTEGER 1)
                0.85,  # Good quality
            ),
        )
    
    # Before: approval_queue should be empty
    with agent_learning_db.conn() as conn:
        count_before = conn.execute(
            "SELECT COUNT(*) FROM approval_queue"
        ).fetchone()[0]
    
    # Call check_draft_ready
    result = check_draft_ready(agent_learning_db, project_id, ph)
    
    # Should return False (rework blocks drafting)
    assert result is False, "check_draft_ready should return False when rework_detected is True"
    
    # approval_queue should remain empty
    with agent_learning_db.conn() as conn:
        count_after = conn.execute(
            "SELECT COUNT(*) FROM approval_queue"
        ).fetchone()[0]
    
    assert count_after == count_before, "approval_queue should remain empty when draft is blocked"


def test_draft_gate_treats_missing_rework_as_not_detected(agent_learning_db: Database):
    project_id = "test-project"
    description = "pattern missing rework metadata"
    ph = pattern_hash(description)

    with agent_learning_db.conn() as conn:
        import time
        import json
        conn.execute(
            """
            INSERT OR REPLACE INTO subtask_patterns
            (pattern_hash, pattern_desc, occurrence_count, tier, last_seen, examples, eval_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ph,
                description,
                10,
                "low",
                time.time(),
                json.dumps(["example"]),
                0.85,
            ),
        )

    result = check_draft_ready(agent_learning_db, project_id, ph)

    assert result is True, "missing rework_detected should not block draft readiness"


def test_draft_gate_treats_none_rework_as_not_detected(agent_learning_db: Database):
    project_id = "test-project"
    description = "pattern with null rework metadata"
    ph = pattern_hash(description)

    with agent_learning_db.conn() as conn:
        import time
        import json
        conn.execute(
            """
            INSERT OR REPLACE INTO subtask_patterns
            (pattern_hash, pattern_desc, occurrence_count, tier, last_seen, examples, rework_detected, eval_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ph,
                description,
                10,
                "low",
                time.time(),
                json.dumps(["example"]),
                None,
                0.85,
            ),
        )

    result = check_draft_ready(agent_learning_db, project_id, ph)

    assert result is True, "rework_detected=None should not block draft readiness"


def test_draft_gate_coerces_numeric_pattern_fields(agent_learning_db: Database):
    project_id = "test-project"
    description = "pattern with stringified numeric metadata"
    ph = pattern_hash(description)

    with agent_learning_db.conn() as conn:
        import time
        import json
        conn.execute(
            """
            INSERT OR REPLACE INTO subtask_patterns
            (pattern_hash, pattern_desc, occurrence_count, tier, last_seen, examples, rework_detected, eval_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ph,
                description,
                "10",
                "low",
                time.time(),
                json.dumps(["example"]),
                0,
                "0.85",
            ),
        )

    result = check_draft_ready(agent_learning_db, project_id, ph)

    assert result is True, "stringified numeric fields should not break draft readiness"


def test_draft_gate_treats_string_zero_rework_as_not_detected(agent_learning_db: Database):
    project_id = "test-project"
    description = "pattern with string zero rework metadata"
    ph = pattern_hash(description)

    with agent_learning_db.conn() as conn:
        import time
        import json
        conn.execute(
            """
            INSERT OR REPLACE INTO subtask_patterns
            (pattern_hash, pattern_desc, occurrence_count, tier, last_seen, examples, rework_detected, eval_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ph,
                description,
                10,
                "low",
                time.time(),
                json.dumps(["example"]),
                "0",
                0.85,
            ),
        )

    result = check_draft_ready(agent_learning_db, project_id, ph)

    assert result is True, "rework_detected='0' should not block draft readiness"


def test_draft_gate_uses_description_fallback(agent_learning_db: Database):
    project_id = "test-project"
    ph = pattern_hash("legacy description only")

    with agent_learning_db.conn() as conn:
        import time
        import json
        conn.execute(
            """
            INSERT OR REPLACE INTO subtask_patterns
            (pattern_hash, pattern_desc, occurrence_count, tier, last_seen, examples, eval_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ph,
                "",
                10,
                "low",
                time.time(),
                json.dumps(["example"]),
                0.85,
            ),
        )

    original_get_pattern = agent_learning_db.get_pattern

    def legacy_get_pattern(pattern_hash_value: str):
        record = original_get_pattern(pattern_hash_value)
        assert record is not None
        record["description"] = "Fix api handler output"
        return record

    agent_learning_db.get_pattern = legacy_get_pattern  # type: ignore[method-assign]
    try:
        result = check_draft_ready(agent_learning_db, project_id, ph)
    finally:
        agent_learning_db.get_pattern = original_get_pattern  # type: ignore[method-assign]

    assert result is True, "legacy description fallback should preserve draft readiness"


def test_evaluate_pattern_readiness_accepts_decimal_string_occurrence_count():
    readiness = evaluate_pattern_readiness({
        "pattern_desc": "Fix api handler output",
        "occurrence_count": "12.0",
        "eval_quality": 0.90,
        "rework_detected": False,
    })

    assert readiness["recurrence_count"] == 12
    assert readiness["ready"] is True


def test_evaluate_pattern_readiness_prefers_explicit_lane():
    readiness = evaluate_pattern_readiness({
        "pattern_desc": "Fix api handler output",
        "lane": "project",
        "occurrence_count": 5,
        "eval_quality": 0.70,
        "rework_detected": False,
    })

    assert readiness["lane"] == "project"
    assert readiness["ready"] is True


def test_evaluate_pattern_readiness_handles_special_float_occurrence_counts():
    readiness = evaluate_pattern_readiness({
        "pattern_desc": "Fix api handler output",
        "occurrence_count": float("inf"),
        "eval_quality": 0.90,
        "rework_detected": False,
    })

    assert readiness["recurrence_count"] == 0
    assert readiness["ready"] is False


def test_evaluate_pattern_readiness_handles_special_string_occurrence_counts():
    readiness = evaluate_pattern_readiness({
        "pattern_desc": "Fix api handler output",
        "occurrence_count": "inf",
        "eval_quality": 0.90,
        "rework_detected": False,
    })

    assert readiness["recurrence_count"] == 0
    assert readiness["ready"] is False


def test_evaluate_pattern_readiness_ignores_whitespace_only_pattern_desc():
    readiness = evaluate_pattern_readiness({
        "pattern_desc": "   ",
        "description": "Write tests for our asyncio worker",
        "occurrence_count": 5,
        "eval_quality": 0.70,
        "rework_detected": False,
    })

    assert readiness["lane"] == "project"
    assert readiness["ready"] is True


def test_evaluate_pattern_readiness_normalizes_explicit_lane():
    readiness = evaluate_pattern_readiness({
        "pattern_desc": "Fix api handler output",
        "lane": " Project ",
        "occurrence_count": 5,
        "eval_quality": 0.70,
        "rework_detected": False,
    })

    assert readiness["lane"] == "project"
    assert readiness["ready"] is True


def test_evaluate_pattern_readiness_treats_unknown_rework_string_as_false():
    readiness = evaluate_pattern_readiness({
        "pattern_desc": "Fix api handler output",
        "occurrence_count": 10,
        "eval_quality": 0.90,
        "rework_detected": "unexpected-token",
    })

    assert readiness["rework_detected"] is False
    assert readiness["ready"] is True


def test_evaluate_pattern_readiness_rejects_nonfinite_eval_quality():
    readiness = evaluate_pattern_readiness({
        "pattern_desc": "Fix api handler output",
        "occurrence_count": 10,
        "eval_quality": "inf",
        "rework_detected": False,
    })

    assert readiness["eval_quality"] == 0.0
    assert readiness["ready"] is False


# ============================================================================
# Test 3: Block weak eval
# ============================================================================


def test_draft_gate_blocks_weak_eval(agent_learning_db: Database):
    """
    Test that check_draft_ready returns False when eval_quality is too low.
    
    Setup: Insert pattern with:
        - occurrence_count = 10 (above threshold)
        - rework_detected = False (positive signal)
        - eval_quality = 0.60 (below 0.70 threshold — blocks drafting)
    
    Expected: check_draft_ready returns False, approval_queue stays empty
    """
    from shared.agents import pattern_hash
    
    project_id = "test-project"
    description = "pattern with weak eval"
    ph = pattern_hash(description)
    
    # Insert pattern with eval_quality < 0.70
    with agent_learning_db.conn() as conn:
        import time
        import json
        conn.execute(
            """
            INSERT OR REPLACE INTO subtask_patterns
            (pattern_hash, pattern_desc, occurrence_count, tier, last_seen, examples, rework_detected, eval_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ph,
                description,
                10,  # High recurrence
                "low",
                time.time(),
                json.dumps(["example"]),
                0,  # rework_detected = False (no rework)
                0.60,  # Below 0.70 threshold
            ),
        )
    
    # Before: approval_queue should be empty
    with agent_learning_db.conn() as conn:
        count_before = conn.execute(
            "SELECT COUNT(*) FROM approval_queue"
        ).fetchone()[0]
    
    # Call check_draft_ready
    result = check_draft_ready(agent_learning_db, project_id, ph)
    
    # Should return False (eval_quality too low)
    assert result is False, "check_draft_ready should return False when eval_quality < 0.70"
    
    # approval_queue should remain empty
    with agent_learning_db.conn() as conn:
        count_after = conn.execute(
            "SELECT COUNT(*) FROM approval_queue"
        ).fetchone()[0]
    
    assert count_after == count_before, "approval_queue should remain empty when draft is blocked"


# ============================================================================
# Test 4: Block insufficient recurrence
# ============================================================================


def test_draft_gate_blocks_low_recurrence(agent_learning_db: Database):
    """
    Test that check_draft_ready returns False when recurrence is below threshold.
    
    Setup: Insert pattern with:
        - occurrence_count = 3 (below 5 threshold)
        - rework_detected = False (positive signal)
        - eval_quality = 0.85 (good quality)
    
    Expected: check_draft_ready returns False, approval_queue stays empty
    """
    from shared.agents import pattern_hash
    
    project_id = "test-project"
    description = "low recurrence pattern"
    ph = pattern_hash(description)
    
    # Insert pattern with occurrence_count < 5
    with agent_learning_db.conn() as conn:
        import time
        import json
        conn.execute(
            """
            INSERT OR REPLACE INTO subtask_patterns
            (pattern_hash, pattern_desc, occurrence_count, tier, last_seen, examples, rework_detected, eval_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ph,
                description,
                3,  # Below 5 threshold
                "low",
                time.time(),
                json.dumps(["example"]),
                0,  # No rework
                0.85,  # Good quality
            ),
        )
    
    # Before: approval_queue should be empty
    with agent_learning_db.conn() as conn:
        count_before = conn.execute(
            "SELECT COUNT(*) FROM approval_queue"
        ).fetchone()[0]
    
    # Call check_draft_ready
    result = check_draft_ready(agent_learning_db, project_id, ph)
    
    # Should return False (recurrence below threshold)
    assert result is False, "check_draft_ready should return False when recurrence < 5"
    
    # approval_queue should remain empty
    with agent_learning_db.conn() as conn:
        count_after = conn.execute(
            "SELECT COUNT(*) FROM approval_queue"
        ).fetchone()[0]
    
    assert count_after == count_before, "approval_queue should remain empty when draft is blocked"


# ============================================================================
# Test 5: Lane detection
# ============================================================================


def test_detect_lane_project_specific():
    """Test that project-specific keywords map to 'project' lane."""
    patterns = [
        "our codebase has a bug",
        "this project's module needs refactoring",
        "the repository-specific handler",
    ]
    
    for pattern in patterns:
        lane = _detect_lane(pattern)
        assert lane == "project", f"Pattern '{pattern}' should map to 'project' lane"


def test_detect_lane_default_shared():
    """Test that generic patterns map to 'shared' lane."""
    patterns = [
        "write unit tests",
        "fix function implementation",
        "add error handling",
        "optimize performance",
    ]
    
    for pattern in patterns:
        lane = _detect_lane(pattern)
        assert lane == "shared", f"Pattern '{pattern}' should map to 'shared' lane"


# ============================================================================
# Test 6: Pattern increment on repeated work
# ============================================================================


def test_pattern_occurrence_increment(agent_learning_db: Database):
    """
    Test that occurrence count increments predictably on repeated patterns.
    
    Setup: Record same pattern 3 times
    Expected: occurrence_count increases from 1 → 2 → 3
    """
    from shared.agents import pattern_hash
    
    description = "repeated pattern"
    ph = pattern_hash(description)
    
    # Record same pattern 3 times
    for i in range(3):
        count = agent_learning_db.track_pattern(
            pattern_hash=ph,
            pattern_desc=description,
            tier="low",
            example=f"example_{i}",
        )
        assert count == i + 1, f"Occurrence count should be {i + 1}, got {count}"
    
    # Verify final count
    pattern = agent_learning_db.get_pattern(ph)
    assert pattern is not None, "Pattern should exist in database"
    assert pattern["occurrence_count"] == 3, "Final occurrence_count should be 3"


def test_db_pattern_readers_treat_string_zero_rework_as_false(agent_learning_db: Database):
    description = "db boolean coercion pattern"
    ph = pattern_hash(description)

    for i in range(5):
        agent_learning_db.track_pattern(
            pattern_hash=ph,
            pattern_desc=description,
            tier="low",
            example=f"example_{i}",
        )

    with agent_learning_db.conn() as conn:
        conn.execute(
            "UPDATE subtask_patterns SET rework_detected = ? WHERE pattern_hash = ?",
            ("0", ph),
        )

    pattern = agent_learning_db.get_pattern(ph)
    mature = agent_learning_db.get_mature_patterns(min_occurrences=5)
    mature_pattern = next(item for item in mature if item["pattern_hash"] == ph)

    assert pattern is not None
    assert pattern["rework_detected"] is False
    assert mature_pattern["rework_detected"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
