#!/usr/bin/env python3
"""Helper-level tests for swarm progress payload construction."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.swarm import build_wave_progress_payload


def test_build_wave_progress_payload() -> None:
    """Wave progress helper should return the stable Phase 34 payload shape."""
    payload = build_wave_progress_payload(
        "swarm-34",
        wave=2,
        completed_subtasks=3,
        pending_subtasks=1,
        artifacts_produced=4,
        round=0,
    )

    assert payload == {
        "swarm_id": "swarm-34",
        "wave": 2,
        "completed_subtasks": 3,
        "pending_subtasks": 1,
        "artifacts_produced": 4,
        "round": 0,
    }
