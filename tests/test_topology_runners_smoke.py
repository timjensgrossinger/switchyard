#!/usr/bin/env python3
"""Smoke tests for explicit topology runner entry points."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.orchestrator import Orchestrator


def test_runner_methods_exist() -> None:
    """Phase 34 should expose named runner methods on Orchestrator."""
    assert hasattr(Orchestrator, "_execute_dag_runner")
    assert hasattr(Orchestrator, "_execute_hierarchical_runner")
    assert hasattr(Orchestrator, "_execute_star_runner")
