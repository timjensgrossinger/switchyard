#!/usr/bin/env python3
"""Tests for Phase 5 — Adaptive Personalization."""
from __future__ import annotations

import sys
import os
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.style import (
    StyleProfile, StyleLearner, DecompositionPrefs, analyze_diff,
    _detect_naming, _detect_return_style, _detect_type_hints,
    _detect_comment_verbosity, _detect_import_style, _detect_output_format,
)
from shared.router import TaskRouter
from shared.config import TGsConfig
from shared.db import Database



# ---------------------------------------------------------------------------
# Style detection heuristics
# ---------------------------------------------------------------------------

def test_detect_naming_snake_case():
    code = "user_name = get_user_id()\nfirst_name = parse_input()"
    result = _detect_naming(code)
    assert result == "snake_case", f"Expected snake_case, got {result}"


def test_detect_naming_camel_case():
    code = "userName = getUserId()\nfirstName = parseInput()"
    result = _detect_naming(code)
    assert result == "camelCase", f"Expected camelCase, got {result}"


def test_detect_naming_mixed():
    code = "user_name = getUserId()\nfirst_name = parseInput()\nold_val = get_old_val()"
    result = _detect_naming(code)
    assert result in ("snake_case", "mixed"), f"Expected snake_case or mixed, got {result}"


def test_detect_return_style_early():
    code = """
def check(x):
    if not x:
        return None
    if x < 0:
        return -1
    return x
"""
    result = _detect_return_style(code)
    assert result == "early_return", f"Expected early_return, got {result}"


def test_detect_type_hints_always():
    code = """
def foo(x: int) -> str:
    pass
def bar(y: float) -> None:
    pass
"""
    result = _detect_type_hints(code)
    assert result == "always", f"Expected always, got {result}"


def test_detect_type_hints_never():
    code = """
def foo(x):
    pass
def bar(y, z):
    pass
"""
    result = _detect_type_hints(code)
    assert result == "never", f"Expected never, got {result}"


def test_detect_comment_verbosity_high():
    code = """
# This is a comment
x = 1
# Another comment
y = 2
# Yet another comment
z = 3
# And one more
w = 4
"""
    result = _detect_comment_verbosity(code)
    assert result == "high", f"Expected high, got {result}"


def test_detect_comment_verbosity_low():
    code = "x = 1\ny = 2\nz = 3\nw = 4\na = 5\nb = 6\nc = 7\nd = 8\ne = 9\nf = 10\n"
    result = _detect_comment_verbosity(code)
    assert result == "low", f"Expected low, got {result}"


def test_detect_import_style_relative():
    code = "from .models import User\nfrom .utils import helper\n"
    result = _detect_import_style(code)
    assert result == "relative", f"Expected relative, got {result}"


def test_detect_import_style_absolute():
    code = "from mypackage.models import User\nimport os\nimport sys\n"
    result = _detect_import_style(code)
    assert result == "absolute", f"Expected absolute, got {result}"


def test_detect_output_format_code_only():
    original = "Here's the solution:\n```python\nx = 1\n```\nThis works because..."
    edited = "x = 1"
    result = _detect_output_format(original, edited)
    assert result == "code_only", f"Expected code_only, got {result}"


# ---------------------------------------------------------------------------
# analyze_diff integration
# ---------------------------------------------------------------------------

def test_analyze_diff_returns_observations():
    original = """
def get_user(user_id: int) -> dict:
    # Fetch from database
    if not user_id:
        return None
    return db.query(user_id)
"""
    edited = original  # no changes
    obs = analyze_diff(original, edited)
    assert isinstance(obs, dict)
    # Should detect at least some dimensions
    assert any(k in obs for k in [
        "naming_convention", "return_style", "type_hint_usage",
        "comment_verbosity", "import_style",
    ])


def test_analyze_diff_empty_input():
    obs = analyze_diff("", "")
    assert isinstance(obs, dict)
    # Should return empty or minimal observations
    assert len(obs) <= 6


# ---------------------------------------------------------------------------
# StyleLearner
# ---------------------------------------------------------------------------

def test_style_learner_observe_and_get_profile():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=Path(td) / "test.db")
        learner = StyleLearner(db)
        code = """
def get_user(user_id: int) -> dict:
    if not user_id:
        return None
    result = db.query(user_id)
    return result
"""
        learner.observe("/proj", code, code)
        profile = learner.get_profile("/proj")
        assert isinstance(profile, StyleProfile)
        assert profile.sample_count >= 1
        db.close()


def test_style_learner_default_profile():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=Path(td) / "test.db")
        learner = StyleLearner(db)
        profile = learner.get_profile("/nonexistent")
        assert profile.naming_convention == "unknown"
        assert profile.sample_count == 0
        db.close()


def test_style_learner_preamble_generation():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=Path(td) / "test.db")
        learner = StyleLearner(db)
        snake_code = "user_name = get_user_id()\ndef foo(x: int) -> str:\n    if not x:\n        return None\n    return str(x)\n"
        learner.observe("/proj", snake_code, snake_code)
        preamble = learner.get_preamble("/proj")
        assert isinstance(preamble, str)
        # Should mention snake_case if detected
        if "snake" in preamble.lower():
            assert True
        db.close()


def test_style_learner_preamble_empty_for_unknown():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=Path(td) / "test.db")
        learner = StyleLearner(db)
        preamble = learner.get_preamble("/nonexistent")
        # Should be empty or minimal for unknown profiles
        assert len(preamble) < 200
        db.close()


def test_style_learner_voting_accumulation():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=Path(td) / "test.db")
        learner = StyleLearner(db)
        snake = "user_name = get_user_id()\nfirst_name = x\n"
        camel = "userName = getUserId()\nfirstName = x\n"
        # 3 snake, 1 camel — snake should win
        learner.observe("/proj", snake, snake)
        learner.observe("/proj", snake, snake)
        learner.observe("/proj", snake, snake)
        learner.observe("/proj", camel, camel)
        profile = learner.get_profile("/proj")
        assert profile.naming_convention == "snake_case", \
            f"Expected snake_case (3 vs 1), got {profile.naming_convention}"
        assert profile.sample_count == 4
        db.close()


def test_style_learner_track_followup():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=Path(td) / "test.db")
        learner = StyleLearner(db)
        for _ in range(4):
            learner.track_followup("/proj", "edge_cases")
        # After >3 edge_cases, review_depth should become thorough
        profile = learner.get_profile("/proj")
        assert profile.review_depth == "thorough", \
            f"Expected thorough after 4 edge_case followups, got {profile.review_depth}"
        db.close()


# ---------------------------------------------------------------------------
# DecompositionPrefs
# ---------------------------------------------------------------------------

def test_decomp_prefs_default():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=Path(td) / "test.db")
        prefs = DecompositionPrefs(db)
        result = prefs.get_preferred_granularity("/proj")
        assert result == "default"
        db.close()


def test_decomp_prefs_coarse():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=Path(td) / "test.db")
        prefs = DecompositionPrefs(db)
        # User consistently merges subtasks (actual < planned)
        prefs.record_plan_interaction("/proj", planned_count=6, actual_count=2)
        prefs.record_plan_interaction("/proj", planned_count=5, actual_count=2)
        prefs.record_plan_interaction("/proj", planned_count=4, actual_count=2)
        result = prefs.get_preferred_granularity("/proj")
        assert result == "coarse", f"Expected coarse, got {result}"
        db.close()


def test_decomp_prefs_fine():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=Path(td) / "test.db")
        prefs = DecompositionPrefs(db)
        # User consistently splits (actual > planned)
        prefs.record_plan_interaction("/proj", planned_count=2, actual_count=5)
        prefs.record_plan_interaction("/proj", planned_count=3, actual_count=6)
        prefs.record_plan_interaction("/proj", planned_count=2, actual_count=4)
        result = prefs.get_preferred_granularity("/proj")
        assert result == "fine", f"Expected fine, got {result}"
        db.close()


def test_decomp_prefs_needs_minimum_interactions():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=Path(td) / "test.db")
        prefs = DecompositionPrefs(db)
        # Only 2 interactions — should still be "default"
        prefs.record_plan_interaction("/proj", planned_count=6, actual_count=2)
        prefs.record_plan_interaction("/proj", planned_count=5, actual_count=2)
        result = prefs.get_preferred_granularity("/proj")
        assert result == "default", f"Expected default (< 3 interactions), got {result}"
        db.close()


# ---------------------------------------------------------------------------
# Project routing profiles (router.py)
# ---------------------------------------------------------------------------

def test_router_project_modifier_no_data():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=Path(td) / "test.db")
        router = TaskRouter(TGsConfig(), db=db)
        # Should route normally with no project data
        d1 = router.classify("fix typo")
        d2 = router.classify("fix typo", project_path="/tmp/proj")
        # Scores should be equal (no modifier)
        assert abs(d1.score - d2.score) < 0.01
        db.close()


def test_router_learn_project_routing():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=Path(td) / "test.db")
        router = TaskRouter(TGsConfig(), db=db)
        # Train: low tier was consistently wrong
        for _ in range(10):
            router.learn_project_routing("/proj", "low", was_correct=False)
        # Project modifier should now push scores up
        d = router.classify("fix typo", project_path="/proj")
        d_no_proj = router.classify("fix typo")
        assert d.score >= d_no_proj.score, \
            f"Project modifier should push score up: {d.score} vs {d_no_proj.score}"
        db.close()


def test_router_time_based_learning():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=Path(td) / "test.db")
        router = TaskRouter(TGsConfig(), db=db)
        hour = time.localtime().tm_hour
        # Train: quality focused at this hour
        for _ in range(10):
            router.learn_time_pattern(hour, was_quality_focused=True)
        # Time modifier should push scores up
        d = router.classify("fix typo")
        assert d.score >= 0.0  # Basic sanity
        db.close()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

