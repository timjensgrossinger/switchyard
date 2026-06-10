#!/usr/bin/env python3
"""
Tests for shared/router.py — complexity classifier with intent modifier.
"""
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure shared/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shared.adaptive as adaptive_module
import shared.router as router_module
from shared.config import TGsConfig
from shared.db import Database
from shared.router import TaskRouter, RoutingDecision


def _make_router() -> TaskRouter:
    return TaskRouter(TGsConfig())


def test_base_score_low_tier() -> None:
    """Simple task with no signals should be low tier."""
    router = _make_router()
    decision = router.classify("hello world")
    assert decision.tier == "low", f"Expected low, got {decision.tier}"
    assert decision.score <= 0.55


def test_override_low() -> None:
    """Override keywords should force low tier."""
    router = _make_router()
    decision = router.classify("add a docstring to this function")
    assert decision.tier == "low"
    assert decision.override is True


def test_override_high() -> None:
    """Override keywords should force high tier."""
    router = _make_router()
    decision = router.classify("do a security review of this module")
    assert decision.tier == "high"
    assert decision.override is True


def test_routine_authentication_implementation_is_medium() -> None:
    router = _make_router()
    decision = router.classify(
        "Implement authentication middleware across auth.py service.py cli.py"
    )

    assert decision.tier == "medium"
    assert decision.override is False


def test_oauth_architecture_remains_high() -> None:
    router = _make_router()
    decision = router.classify("Architect an OAuth authentication migration")

    assert decision.tier == "high"
    assert decision.override is True


def test_intent_modifier_speed() -> None:
    """Speed signals should lower the effective score."""
    router = _make_router()
    # "implement" normally adds medium signal, but "quick" should lower it
    decision_normal = router.classify("implement message filtering logic")
    decision_quick = router.classify("quick implement message filtering logic")
    assert decision_quick.score < decision_normal.score
    assert decision_quick.intent_modifier < 0


def test_intent_modifier_quality() -> None:
    """Quality signals should raise the effective score."""
    router = _make_router()
    decision_normal = router.classify("add a helper function")
    decision_thorough = router.classify("thorough add a helper function")
    assert decision_thorough.score > decision_normal.score
    assert decision_thorough.intent_modifier > 0


def test_multi_file_bonus() -> None:
    """Multiple file references should increase score."""
    router = _make_router()
    decision = router.classify("update foo.py bar.js baz.ts to use new API")
    assert "multi_file" in decision.reason


def test_long_prompt_bonus() -> None:
    """Long prompts should get a score bump."""
    router = _make_router()
    long_task = " ".join(["word"] * 35)
    decision = router.classify(long_task)
    assert "long_prompt" in decision.reason


def test_tier_returns_labels_not_models() -> None:
    """Tier should be low/medium/high, never a model name."""
    router = _make_router()
    for task in ["simple fix", "implement auth", "architect system"]:
        decision = router.classify(task)
        assert decision.tier in ("low", "medium", "high"), (
            f"Got tier '{decision.tier}' for '{task}'"
        )


def test_hard_bounds_respected() -> None:
    """Thresholds should be within hard bounds."""
    config = TGsConfig()
    config.thresholds.low_max = 0.20  # below floor
    config.thresholds.medium_max = 0.99  # above ceiling
    config.thresholds.clamp()
    assert config.thresholds.low_max >= 0.50
    assert config.thresholds.medium_max <= 0.95


def test_project_local_optin_gate() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "router.db")
        router = TaskRouter(TGsConfig(), db=db)
        project_id = str(Path(td) / "project")

        assert router.is_learning_enabled(project_id) is False
        router.enable_learning(project_id)
        assert router.is_learning_enabled(project_id) is True
        db.close()


def test_project_learning_setting_round_trip_through_db_helper() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "router.db")
        router = TaskRouter(TGsConfig(), db=db)
        project_id = str((Path(td) / "project").resolve())

        assert router.is_learning_enabled(project_id) is False
        db.set_project_setting(project_id, "learning_enabled", True)
        assert router.is_learning_enabled(project_id) is True
        db.reset_project_setting(project_id, "learning_enabled")
        assert router.is_learning_enabled(project_id) is False
        db.close()


def test_project_sample_min_gate() -> None:
    assert hasattr(router_module, "ACTIVATION_MIN_SAMPLES")
    assert router_module.ACTIVATION_MIN_SAMPLES == 5
    assert adaptive_module.PROJECT_SAMPLE_MIN == 3


if __name__ == "__main__":
    tests = [
        test_base_score_low_tier,
        test_override_low,
        test_override_high,
        test_intent_modifier_speed,
        test_intent_modifier_quality,
        test_multi_file_bonus,
        test_long_prompt_bonus,
        test_tier_returns_labels_not_models,
        test_hard_bounds_respected,
        test_project_local_optin_gate,
        test_project_learning_setting_round_trip_through_db_helper,
        test_project_sample_min_gate,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  ✅ {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ {test.__name__}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
