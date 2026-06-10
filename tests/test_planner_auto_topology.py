#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import TGsConfig
from shared.planner import make_auto_topology_decision


def test_auto_select_star() -> None:
    config = TGsConfig.defaults()

    topology, rationale = make_auto_topology_decision(
        {"task_chars": 160, "subtask_count": 8},
        0.75,
        8,
        config=config,
        db=None,
    )

    assert topology == "star"
    assert rationale == "urgency_high"


def test_auto_select_hierarchical() -> None:
    config = TGsConfig.defaults()

    topology, rationale = make_auto_topology_decision(
        {
            "subtasks": [
                {"id": "architect"},
                {"id": "implementer", "parent_id": "architect"},
            ]
        },
        0.10,
        4,
        config=config,
        db=None,
    )

    assert topology == "hierarchical"
    assert rationale == "hierarchy_detected"


def test_auto_select_dag() -> None:
    config = TGsConfig.defaults()

    topology, rationale = make_auto_topology_decision(
        {"task_chars": 80, "subtask_count": 2},
        0.0,
        2,
        config=config,
        db=None,
    )

    assert topology == "dag"
    assert rationale == "balanced_default"
