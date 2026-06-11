#!/usr/bin/env python3
"""
Tests for warm-path scheduling from synchronous flows.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import ParallelismConfig, TGsConfig
from shared.eval import BackgroundEvaluator, EvalResult, WaveFileTracker


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


def test_warm_path_parallel_eval_faster_than_serial() -> None:
    """Four prompts with warm_path_workers=4 should run concurrently."""
    config = TGsConfig.defaults()
    config.parallelism = ParallelismConfig(warm_path_workers=4)
    evaluator = BackgroundEvaluator(config=config)

    tracker = WaveFileTracker()
    files = ["a.py", "b.py", "c.py", "d.py"]
    for fp in files:
        tracker.snapshots_before[fp] = "v1\n"
        tracker.snapshots_after[fp] = "v2\n"

    rework_events = [
        {"file_path": fp, "wave_n": 0, "wave_n1": 1}
        for fp in files
    ]

    def slow_eval_one(prompt_data, model: str) -> EvalResult:
        time.sleep(0.05)
        return EvalResult(
            file_path=prompt_data.file_path,
            score=0.8,
            reason="ok",
            model=model,
        )

    evaluator._eval_one = slow_eval_one  # type: ignore[method-assign]

    start = time.monotonic()
    results = evaluator._run_warm_path_sync(tracker, rework_events)
    elapsed = time.monotonic() - start

    assert len(results) == 4
    # Serial would take ~0.2s; four workers should finish near one sleep.
    assert elapsed < 0.15
