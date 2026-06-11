#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.config import TGsConfig
from shared.orchestrator import Orchestrator
from shared.planner import CLIBackend, Planner


class DummyPlanner(Planner):
    def __init__(self) -> None:
        self._backend = RecordingBackend()


class RecordingBackend(CLIBackend):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def call(self, prompt: str, model: str | None = None, timeout: int = 120) -> str | None:
        self.calls.append(prompt)
        if "AGENT OUTPUTS (partial):" in prompt:
            if "Agent #1" in prompt:
                return "chunk-1: completed auth module"
            if "Agent #2" in prompt:
                return "chunk-2: completed billing module"
            if "Agent #3" in prompt:
                return "chunk-3: completed docs module"
            return "chunk-summary"
        if "CHUNK SUMMARIES:" in prompt:
            return (
                "- Auth module done\n"
                "- Billing module done\n"
                "- Docs module done\n"
                "- No conflicts detected"
            )
        return "single-pass summary"


class DummyProvider:
    def resolve_model(self, tier: str) -> str:
        return f"dummy-{tier}"

    def execute(self, subtask, model: str, timeout: int = 120) -> str | None:
        return None

    def available_tiers(self) -> list[str]:
        return ["low", "medium", "high"]

    def provider_info(self) -> dict:
        return {"primary": "dummy-provider"}


def _build_config(**overrides) -> TGsConfig:
    config = TGsConfig()
    config.parallelism.max_workers = 4
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def test_synthesis_auto_single_pass_for_small_outputs() -> None:
    backend = RecordingBackend()
    orchestrator = Orchestrator(_build_config(synthesis_map_reduce="auto"), DummyProvider(), DummyPlanner())
    results = {1: "small output", 2: "another small output"}

    summary = orchestrator.synthesise("integrate modules", results, backend_call=backend.call)

    assert summary == "single-pass summary"
    assert len(backend.calls) == 1
    assert "AGENT OUTPUTS:" in backend.calls[0]


def test_synthesis_off_always_single_pass() -> None:
    backend = RecordingBackend()
    orchestrator = Orchestrator(
        _build_config(synthesis_map_reduce="off", synthesis_chunk_chars=100),
        DummyProvider(),
        DummyPlanner(),
    )
    results = {index: "x" * 5000 for index in range(1, 4)}

    summary = orchestrator.synthesise("large task", results, backend_call=backend.call)

    assert summary == "single-pass summary"
    assert len(backend.calls) == 1


def test_synthesis_auto_map_reduce_for_large_outputs() -> None:
    backend = RecordingBackend()
    orchestrator = Orchestrator(
        _build_config(synthesis_map_reduce="auto", synthesis_chunk_chars=12000),
        DummyProvider(),
        DummyPlanner(),
    )
    results = {
        1: "alpha " * 3000,
        2: "beta " * 3000,
        3: "gamma " * 3000,
    }

    summary = orchestrator.synthesise("ship feature", results, backend_call=backend.call)

    assert summary is not None
    assert "Auth module done" in summary
    partial_calls = [prompt for prompt in backend.calls if "AGENT OUTPUTS (partial):" in prompt]
    reduce_calls = [prompt for prompt in backend.calls if "CHUNK SUMMARIES:" in prompt]
    assert len(partial_calls) >= 2
    assert len(reduce_calls) == 1
    assert any("Agent #1" in prompt for prompt in partial_calls)
    assert any("Agent #2" in prompt for prompt in partial_calls)
    assert any("Agent #3" in prompt for prompt in partial_calls)


def test_synthesis_always_map_reduce_even_for_small_outputs() -> None:
    backend = RecordingBackend()
    orchestrator = Orchestrator(
        _build_config(synthesis_map_reduce="always", synthesis_chunk_chars=12000),
        DummyProvider(),
        DummyPlanner(),
    )
    results = {1: "small", 2: "also small"}

    summary = orchestrator.synthesise("tiny task", results, backend_call=backend.call)

    assert summary is not None
    partial_calls = [prompt for prompt in backend.calls if "AGENT OUTPUTS (partial):" in prompt]
    reduce_calls = [prompt for prompt in backend.calls if "CHUNK SUMMARIES:" in prompt]
    assert len(partial_calls) == 1
    assert len(reduce_calls) == 1
