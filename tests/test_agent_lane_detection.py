#!/usr/bin/env python3
"""
Tests for Phase 10 Wave 1a — Lane Detection and Lane-Specific Evidence Bars

Tests the two-lane agent classification system (project vs shared) and
verifies that shared lane requires stricter evidence thresholds than project lane.

Per D-05, D-06, D-07:
- Project-specific patterns stay in the project lane
- Generic patterns default to the shared lane
- Shared lane requires higher evidence bars (recurrence >= 10, eval >= 0.85)
- Project lane requires lower evidence bars (recurrence >= 5, eval >= 0.70)
"""
from __future__ import annotations

import sys
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.agents import _detect_lane, check_draft_ready, evaluate_pattern_readiness
from shared.db import Database

passed = 0
failed = 0


def add_test(name: str):
    """Decorator for script-style tests."""
    global passed, failed
    def decorator(fn):
        global passed, failed
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ {name}: {type(e).__name__}: {e}")
            failed += 1
    return decorator


# ============================================================================
# TEST 1: test_detect_lane_project_specific
# ============================================================================

@add_test("test_detect_lane_project_specific - 'our' marker")
def _():
    result = _detect_lane("Write tests for our asyncio-based worker pool")
    assert result == "project", f"Expected 'project', got '{result}'"


@add_test("test_detect_lane_project_specific - 'our code' marker")
def _():
    result = _detect_lane("Debug our error handler")
    assert result == "project", f"Expected 'project', got '{result}'"


@add_test("test_detect_lane_project_specific - 'this project' marker")
def _():
    result = _detect_lane("Fix bug in this project's config loader")
    assert result == "project", f"Expected 'project', got '{result}'"


@add_test("test_detect_lane_project_specific - 'our codebase' marker")
def _():
    result = _detect_lane("Optimize performance in our codebase")
    assert result == "project", f"Expected 'project', got '{result}'"


# ============================================================================
# TEST 2: test_detect_lane_default_shared
# ============================================================================

@add_test("test_detect_lane_default_shared - generic 'test' pattern")
def _():
    result = _detect_lane("Test writer for async patterns")
    assert result == "shared", f"Expected 'shared', got '{result}'"


@add_test("test_detect_lane_default_shared - generic 'refactor' pattern")
def _():
    result = _detect_lane("Refactor API error handler")
    assert result == "shared", f"Expected 'shared', got '{result}'"


@add_test("test_detect_lane_default_shared - generic 'write' pattern")
def _():
    result = _detect_lane("Write documentation for the schema")
    assert result == "shared", f"Expected 'shared', got '{result}'"


@add_test("test_detect_lane_default_shared - generic 'fix' pattern")
def _():
    result = _detect_lane("Fix bug in config loader")
    assert result == "shared", f"Expected 'shared', got '{result}'"


@add_test("test_detect_lane_default_shared - generic 'optimize' pattern")
def _():
    result = _detect_lane("Optimize endpoint performance")
    assert result == "shared", f"Expected 'shared', got '{result}'"


# ============================================================================
# TEST 3: test_draft_gate_project_lane_lower_bar
# ============================================================================

@add_test("test_draft_gate_project_lane_lower_bar - meets lower bar (5 recurrence, 0.70 eval)")
def _():
    # Create a temporary database for this test
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    
    try:
        db = Database(db_path=db_path)
        
        # Insert a pattern with project-specific description
        # Meeting the project lane bar: recurrence=5, eval=0.70, rework=False
        pattern_hash = "test_proj_pattern_001"
        with db.conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO subtask_patterns
                (pattern_hash, pattern_desc, occurrence_count, rework_detected, eval_quality, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (pattern_hash, "Write tests for our asyncio worker pool", 5, 0, 0.70, time.time())
            )
            conn.commit()
        
        # Call check_draft_ready
        project_id = "test-project"
        result = check_draft_ready(db, project_id, pattern_hash)
        
        # Should return True (meets project lane bar)
        assert result is True, f"Expected True (meets project lane bar), got {result}"
        
    finally:
        try:
            db.close()
        except Exception:
            pass
        try:
            db_path.unlink(missing_ok=True)
            (db_path.parent / f"{db_path.name}-wal").unlink(missing_ok=True)
            (db_path.parent / f"{db_path.name}-shm").unlink(missing_ok=True)
        except Exception:
            pass


# ============================================================================
# TEST 4: test_draft_gate_shared_lane_higher_bar
# ============================================================================

@add_test("test_draft_gate_shared_lane_higher_bar - does not meet higher bar (8 recurrence)")
def _():
    # Create a temporary database for this test
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    
    try:
        db = Database(db_path=db_path)
        
        # Insert a pattern with shared-specific description
        # NOT meeting the shared lane bar: recurrence=8 (need 10), eval=0.80 (need 0.85)
        pattern_hash = "test_shared_pattern_001"
        with db.conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO subtask_patterns
                (pattern_hash, pattern_desc, occurrence_count, rework_detected, eval_quality, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (pattern_hash, "Test writer for async patterns", 8, 0, 0.80, time.time())
            )
            conn.commit()
        
        # Call check_draft_ready
        project_id = "test-project"
        result = check_draft_ready(db, project_id, pattern_hash)
        
        # Should return False (does not meet shared lane bar)
        assert result is False, f"Expected False (does not meet shared lane bar), got {result}"
        
    finally:
        try:
            db.close()
        except Exception:
            pass
        try:
            db_path.unlink(missing_ok=True)
            (db_path.parent / f"{db_path.name}-wal").unlink(missing_ok=True)
            (db_path.parent / f"{db_path.name}-shm").unlink(missing_ok=True)
        except Exception:
            pass


@add_test("test_draft_gate_shared_lane_higher_bar - meets higher bar (10 recurrence, 0.85 eval)")
def _():
    # Create a temporary database for this test
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    
    try:
        db = Database(db_path=db_path)
        
        # Insert a pattern with shared-specific description
        # Meeting the shared lane bar: recurrence=10, eval=0.85, rework=False
        pattern_hash = "test_shared_pattern_002"
        with db.conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO subtask_patterns
                (pattern_hash, pattern_desc, occurrence_count, rework_detected, eval_quality, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (pattern_hash, "Test writer for async patterns", 10, 0, 0.85, time.time())
            )
            conn.commit()
        
        # Call check_draft_ready
        project_id = "test-project"
        result = check_draft_ready(db, project_id, pattern_hash)
        
        # Should return True (meets shared lane bar)
        assert result is True, f"Expected True (meets shared lane bar), got {result}"
        
    finally:
        try:
            db.close()
        except Exception:
            pass
        try:
            db_path.unlink(missing_ok=True)
            (db_path.parent / f"{db_path.name}-wal").unlink(missing_ok=True)
            (db_path.parent / f"{db_path.name}-shm").unlink(missing_ok=True)
        except Exception:
            pass


# ============================================================================
# TEST 5: evaluate_pattern_readiness compatibility
# ============================================================================

@add_test("test_evaluate_pattern_readiness - project lane returns ready metadata")
def _():
    state = evaluate_pattern_readiness(
        {
            "pattern_hash": "proj-ready-001",
            "pattern_desc": "Write tests for our asyncio worker pool",
            "occurrence_count": 5,
            "rework_detected": False,
            "eval_quality": 0.70,
        },
        "test-project",
    )
    assert state["ready"] is True, f"Expected ready=True, got {state}"
    assert state["lane"] == "project", f"Expected project lane, got {state['lane']}"
    assert state["recurrence_threshold"] == 5, f"Expected project recurrence threshold, got {state['recurrence_threshold']}"


@add_test("test_evaluate_pattern_readiness - shared lane reports blocker")
def _():
    state = evaluate_pattern_readiness(
        {
            "pattern_hash": "shared-blocked-001",
            "pattern_desc": "Write tests for async patterns",
            "occurrence_count": 8,
            "rework_detected": False,
            "eval_quality": 0.80,
        },
        "test-project",
    )
    assert state["ready"] is False, f"Expected ready=False, got {state}"
    assert state["lane"] == "shared", f"Expected shared lane, got {state['lane']}"
    assert state["reason"] == "recurrence_below_threshold", f"Unexpected blocker: {state['reason']}"


@add_test("test_mcp_server_import_smoke")
def _():
    import mcp_server

    assert callable(mcp_server.handle_learning_pattern_health), "mcp_server should import successfully"


# ============================================================================
# Edge cases
# ============================================================================

@add_test("test_detect_lane_edge_case_empty_string")
def _():
    result = _detect_lane("")
    assert result == "shared", f"Empty string should default to shared, got '{result}'"


@add_test("test_detect_lane_edge_case_whitespace_only")
def _():
    result = _detect_lane("   ")
    assert result == "shared", f"Whitespace-only should default to shared, got '{result}'"


@add_test("test_detect_lane_case_insensitive_our")
def _():
    result = _detect_lane("Our module needs refactoring")
    assert result == "project", f"'Our' (capitalized) should trigger project lane, got '{result}'"


@add_test("test_detect_lane_edge_case_none_input")
def _():
    result = _detect_lane(None)
    assert result == "shared", f"None input should safely default to shared, got '{result}'"


@add_test("test_detect_lane_edge_case_non_string_input")
def _():
    result = _detect_lane(123)  # type: ignore
    assert result == "shared", f"Non-string input should safely default to shared, got '{result}'"


# ============================================================================
# Summary
# ============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("Phase 10 Wave 1a: Lane Detection Tests")
    print("=" * 60)
    print()
    
    # Need to manually run the test functions since we use a decorator approach
    # that doesn't work well with the script-style testing structure
    #
    # All test functions are named test_* and decorated with @add_test()
    # The decorator execution happens at import time, so we just need
    # to print the summary
    
    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    sys.exit(0 if failed == 0 else 1)
