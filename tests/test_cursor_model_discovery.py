#!/usr/bin/env python3
"""Tests for Cursor live model discovery and host spawn model selection."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.discovery import CLIProvider, _parse_cursor_models, _tier_cursor_models
from shared.host_spawn import build_host_spawn_waves, host_native_model_for_tier
from shared.config import TGsConfig


SAMPLE_CURSOR_MODELS_OUTPUT = """\
Available models

auto - Auto (current)
composer-2.5-fast - Composer 2.5 Fast
composer-2.5 - Composer 2.5
gpt-5.3-codex-low-fast - Codex 5.3 Low Fast
claude-4.6-sonnet-medium - Sonnet 4.6 Medium
claude-opus-4-8-thinking-high - Opus 4.8 Thinking High
"""


def test_parse_cursor_models_extracts_ids_and_tiers() -> None:
    provider = CLIProvider(name="cursor", binary="cursor-agent", display_name="Cursor", tier_models={}, cost_rank={})
    tiered = _parse_cursor_models(provider, SAMPLE_CURSOR_MODELS_OUTPUT)
    assert "composer-2.5-fast" in tiered["low"]
    assert "composer-2.5" in tiered["medium"]
    assert "claude-opus-4-8-thinking-high" in tiered["high"]


def test_tier_cursor_models_prefers_low_fast_for_low_bucket() -> None:
    tiered = _tier_cursor_models([
        "composer-2.5-fast",
        "gpt-5.3-codex-low-fast",
        "claude-opus-4-8-thinking-high",
    ])
    assert tiered["low"][0] == "composer-2.5-fast"


def test_build_host_spawn_waves_uses_host_native_model_not_subtask_registry_model() -> None:
    config = TGsConfig.defaults()
    plan = {
        "subtasks": [
            {
                "id": 1,
                "description": "Create models.py",
                "tier": "low",
                "model": "gpt-5-mini",
                "target_file": "models.py",
            }
        ],
        "waves": [[1]],
    }

    class FakeProvider:
        name = "cursor"
        tier_models = {"low": "composer-2.5-fast", "medium": "composer-2.5", "high": "claude-opus-4-8-thinking-high"}

    class FakeRegistry:
        available_providers = [FakeProvider()]

    waves = build_host_spawn_waves(
        plan,
        config=config,
        caller="cursor",
        registry=FakeRegistry(),
    )
    assert waves[0]["agents"][0]["model"] == "composer-2.5-fast"


def test_host_native_model_for_tier_prefers_live_registry() -> None:
    config = TGsConfig.defaults()

    class FakeProvider:
        name = "cursor"
        tier_models = {"low": "composer-2.5-fast"}

    class FakeRegistry:
        available_providers = [FakeProvider()]

    model = host_native_model_for_tier(config, "cursor", "low", registry=FakeRegistry())
    assert model == "composer-2.5-fast"
