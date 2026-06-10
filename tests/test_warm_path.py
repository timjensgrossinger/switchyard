#!/usr/bin/env python3
"""
Tests for warm-path scheduling from synchronous flows.
"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.eval import BackgroundEvaluator, WaveFileTracker


def test_spawn_warm_path_from_sync() -> None:
    """BackgroundEvaluator should schedule work via ThreadPoolExecutor warm path."""
    event = threading.Event()
    evaluator = BackgroundEvaluator()

    def fake_run_warm_path_sync(
        tracker: WaveFileTracker,
        rework_events: list[dict],
        model: str = "gpt-5-mini",
    ) -> list[object]:
        event.set()
        return []

    evaluator._run_warm_path_sync = fake_run_warm_path_sync  # type: ignore[method-assign]
    future = evaluator.spawn_warm_path(
        WaveFileTracker(),
        [{"file_path": "foo.py", "wave_n": 0, "wave_n1": 1}],
    )

    assert future is not None
    assert event.wait(timeout=5)
    if hasattr(future, "result"):
        future.result(timeout=5)
