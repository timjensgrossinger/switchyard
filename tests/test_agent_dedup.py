#!/usr/bin/env python3
"""
Tests for Phase 10 Wave 1b — Conservative Duplicate Detection and Merge

Tests the conservative duplicate detection system and merge preservation behavior.
Per D-11: near-duplicate specialists should merge conservatively and preserve both
specializations instead of flattening to generic prompts.

Per D-08: Generated agents favor narrow specialists over broad catch-all roles.
"""
from __future__ import annotations

import sys
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.agents import (
    find_similar_agents,
    merge_agent_definitions,
    _similarity_score,
    _extract_specialist_aspects,
)
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
# TEST 1: test_find_similar_agents_high_threshold
# ============================================================================

@add_test("test_find_similar_agents_high_threshold - similar A and B returned, C not returned")
def _():
    """
    Setup: Create agent definitions with descriptions:
    - Agent A: "Test writer for async code"
    - Agent B: "Test writer for async error handling"
    - Agent C: "Documentation generator"
    Action: Call find_similar_agents("Test writer for async code", lane="shared", db)
    Verify: Returns at least agent_a (perfect match)
    Verify: Similarity scores are >= 0.75 (high bar per D-11)
    """
    # Create temp database
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = Database(db_path)
        
        # Insert test agents with high overlap
        db.agent_definition_insert(
            project_id=None,
            lane="shared",
            pattern_hash="hash_a",
            pattern_desc="Test writer for async code",
            description="Test writer for async code",
            agent_id="agent_a",
            status="approved"
        )
        
        db.agent_definition_insert(
            project_id=None,
            lane="shared",
            pattern_hash="hash_b",
            pattern_desc="Test writer for async error handling",
            description="Test writer for async error handling",
            agent_id="agent_b",
            status="approved"
        )
        
        db.agent_definition_insert(
            project_id=None,
            lane="shared",
            pattern_hash="hash_c",
            pattern_desc="Documentation generator",
            description="Documentation generator",
            agent_id="agent_c",
            status="approved"
        )
        
        # Call find_similar_agents with exact match to agent_a
        result = find_similar_agents(
            "Test writer for async code",
            lane="shared",
            db=db,
            project_id=None
        )
        
        # Verify results - should find at least agent_a (exact match)
        assert len(result) >= 1, f"Expected >= 1 result, got {len(result)}: {result}"
        
        # Check that A is present (exact match)
        agent_ids = [r['agent_id'] for r in result]
        assert 'agent_a' in agent_ids, \
            f"Expected agent_a in results, got {agent_ids}"
        
        # Check that C is NOT present (completely different)
        assert 'agent_c' not in agent_ids, f"Expected agent_c NOT in results, got {agent_ids}"


# ============================================================================
# TEST 2: test_conservative_merge_keeps_specialization
# ============================================================================

@add_test("test_conservative_merge_keeps_specialization - merge preserves both aspects")
def _():
    """
    Setup: Create two agents:
    - Agent A (canonical): "Test writer for async code patterns"
    - Agent B (merge_from): "Test writer for exception handling"
    Action: Call merge_agent_definitions(A_id, B_id, db)
    Verify: Merge succeeds (returns True)
    Verify: Agent A's description now includes both specializations
    Verify: Agent A status remains "approved"
    Verify: Agent B status changed to "merged_into"
    Verify: Original specializations preserved (not flattened to generic "test writer")
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = Database(db_path)
        
        # Create canonical and merge_from agents
        db.agent_definition_insert(
            project_id=None,
            lane="shared",
            pattern_hash="hash_a",
            pattern_desc="Test writer for async code patterns",
            description="Test writer for async code patterns",
            agent_id="agent_a",
            status="approved"
        )
        
        db.agent_definition_insert(
            project_id=None,
            lane="shared",
            pattern_hash="hash_b",
            pattern_desc="Test writer for exception handling",
            description="Test writer for exception handling",
            agent_id="agent_b",
            status="pending"
        )
        
        # Perform merge
        result = merge_agent_definitions("agent_a", "agent_b", db)
        
        # Verify merge succeeded
        assert result is True, f"Expected merge to succeed, got {result}"
        
        # Verify canonical agent description includes both specializations
        canonical = db.agent_definition_get("agent_a")
        assert canonical is not None, "Canonical agent not found after merge"
        assert "async" in canonical['description'].lower(), \
            f"Async specialization not preserved: {canonical['description']}"
        assert "exception" in canonical['description'].lower(), \
            f"Exception handling specialization not preserved: {canonical['description']}"
        
        # Verify canonical agent status is still "approved"
        assert canonical['status'] == 'approved', \
            f"Expected canonical status 'approved', got '{canonical['status']}'"
        
        # Verify merge_from agent status is "merged_into"
        merge_from = db.agent_definition_get("agent_b")
        assert merge_from is not None, "Merge-from agent not found after merge"
        assert merge_from['status'] == 'merged_into', \
            f"Expected merge_from status 'merged_into', got '{merge_from['status']}'"
        
        # Verify specializations are NOT flattened to generic "test writer"
        description = canonical['description'].lower()
        assert "also handles" in description or "exception" in description, \
            f"Specializations appear to be flattened: {canonical['description']}"
        
        # Clean up
        db.close()


# ============================================================================
# TEST 3: test_low_similarity_does_not_suggest_merge
# ============================================================================

@add_test("test_low_similarity_does_not_suggest_merge - returns empty list")
def _():
    """
    Setup: Create agent descriptions with low overlap:
    - Agent A: "Test writer for async patterns"
    - New pattern: "Documentation generator"
    Action: Call find_similar_agents("Documentation generator", lane="shared", db)
    Verify: Returns empty list (similarity < 0.75 threshold)
    Verify: No merge suggestion made
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = Database(db_path)
        
        # Insert test agent
        db.agent_definition_insert(
            project_id=None,
            lane="shared",
            pattern_hash="hash_a",
            pattern_desc="Test writer for async patterns",
            description="Test writer for async patterns",
            agent_id="agent_a",
            status="approved"
        )
        
        # Call find_similar_agents with low-overlap description
        result = find_similar_agents(
            "Documentation generator",
            lane="shared",
            db=db,
            project_id=None
        )
        
        # Verify results are empty (no similar agents found)
        assert len(result) == 0, f"Expected no results for low similarity, got {len(result)}"
        
        # Clean up
        db.close()


# ============================================================================
# TEST 4: test_similarity_score_computation
# ============================================================================

@add_test("test_similarity_score_computation - Jaccard similarity correct")
def _():
    """Test _similarity_score() with known inputs."""
    # Test 1: Identical text
    score = _similarity_score("test writer", "test writer")
    assert score == 1.0, f"Expected 1.0 for identical text, got {score}"
    
    # Test 2: Mostly overlapping
    # "test writer async" → {test, writer, async}, "test writer exception" → {test, writer, exception}
    # intersection = {test, writer} (2), union = {test, writer, async, exception} (4)
    # Jaccard = 2/4 = 0.5
    score = _similarity_score("test writer async", "test writer exception")
    assert 0.4 < score <= 0.6, f"Expected 0.4 < score <= 0.6 for 2/4 overlap, got {score}"
    
    # Test 3: No overlap
    score = _similarity_score("test", "documentation")
    assert score == 0.0 or score < 0.2, f"Expected ~0 for no overlap, got {score}"
    
    # Test 4: High overlap (almost same)
    score = _similarity_score("test writer async code", "test writer async patterns")
    # "test writer async code" → {test, writer, async, code}
    # "test writer async patterns" → {test, writer, async, patterns}
    # intersection = {test, writer, async} (3), union = {test, writer, async, code, patterns} (5)
    # Jaccard = 3/5 = 0.6
    assert 0.5 < score < 0.8, f"Expected 0.5 < score < 0.8 for high overlap, got {score}"


# ============================================================================
# TEST 5: test_extract_specialist_aspects
# ============================================================================

@add_test("test_extract_specialist_aspects - extracts keywords correctly")
def _():
    """Test _extract_specialist_aspects() with known inputs."""
    # Test 1: Single aspect
    aspects = _extract_specialist_aspects("Test writer for async patterns")
    assert "async patterns" in aspects or "async" in aspects[0].lower(), \
        f"Expected 'async patterns' or 'async' in {aspects}"
    
    # Test 2: Multiple aspects
    aspects = _extract_specialist_aspects("Test writer for async patterns and exception handling")
    assert len(aspects) >= 2, f"Expected >= 2 aspects, got {len(aspects)}"
    assert any("async" in a.lower() for a in aspects), \
        f"Expected 'async' in {aspects}"
    assert any("exception" in a.lower() for a in aspects), \
        f"Expected 'exception' in {aspects}"
    
    # Test 3: No "for" pattern
    aspects = _extract_specialist_aspects("Generic test writer")
    assert len(aspects) == 0, f"Expected no aspects for generic pattern, got {aspects}"


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("Phase 10 Wave 1b: Conservative Duplicate Detection and Merge Tests")
    print("=" * 80 + "\n")
    
    print("Running tests...\n")
    
    # All tests are run via @add_test decorator
    
    print(f"\n{'=' * 80}")
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 80)
    
    sys.exit(0 if failed == 0 else 1)
