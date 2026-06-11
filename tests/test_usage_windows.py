from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import io
import tempfile
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from shared.config import TGsConfig, UsageWindowEntry, ProviderUsageWindowConfig
from shared.discovery import ProviderUsageChecker, ProviderRegistry
from shared.quota import ProviderQuotaResult, ProviderQuotaSnapshot, ProviderQuotaService

@dataclass
class DummyProvider:
    name: str
    cost_rank: dict[str, Any]

def make_usage_config_entry(hours: float, budget_tokens: int | None, threshold: float, action: str):
    return UsageWindowEntry(hours=hours, budget_tokens=budget_tokens, threshold=threshold, action=action)

def make_provider_config(windows: list[UsageWindowEntry]) -> ProviderUsageWindowConfig:
    return ProviderUsageWindowConfig(windows=windows)

def make_tgsconfig(provider_windows: dict[str, ProviderUsageWindowConfig]) -> TGsConfig:
    cfg = TGsConfig()
    # assign directly to avoid depending on parsing logic; tests only require the structure
    cfg.provider_usage_windows = provider_windows
    return cfg

def provider_names(lst):
    return [p.name for p in lst]

def copy_candidates(cands):
    return [DummyProvider(name=c.name, cost_rank=dict(c.cost_rank)) for c in cands]

def assert_same_candidates(original, returned):
    assert provider_names(original) == provider_names(returned)
    for o, r in zip(original, returned):
        assert o.cost_rank == r.cost_rank

def test_config_parse_usage_windows():
    yaml_text = """
providers:
  usage_windows:
    test-provider:
      - hours: 24
        budget_tokens: 500000
        threshold: 0.8
        action: cost_rank_boost
"""
    stream = io.StringIO(yaml_text)
    data = yaml.safe_load(stream)
    # navigate to the first window entry
    entry_dict = data["providers"]["usage_windows"]["test-provider"][0]
    entry = UsageWindowEntry(**entry_dict)
    assert isinstance(entry, UsageWindowEntry)
    assert entry.hours == 24
    assert entry.budget_tokens == 500000
    assert pytest.approx(entry.threshold, rel=1e-9) == 0.8
    assert entry.action == "cost_rank_boost"

def test_telemetry_fallback_ratio():
    db = MagicMock()
    # simulate 400k tokens used
    db.get_provider_token_usage.return_value = 400_000
    checker = ProviderUsageChecker()
    ratio = checker.query_usage_ratio("test-provider", window_hours=24, budget_tokens=500_000, db=db)
    assert pytest.approx(ratio, rel=1e-9) == 0.8

def test_ttl_cache_reuse():
    db = MagicMock()
    db.get_provider_token_usage.return_value = 400_000
    checker = ProviderUsageChecker()
    # first call populates cache
    r1 = checker.query_usage_ratio("cached-provider", window_hours=1, budget_tokens=500_000, db=db)
    r2 = checker.query_usage_ratio("cached-provider", window_hours=1, budget_tokens=500_000, db=db)
    assert pytest.approx(r1, rel=1e-9) == pytest.approx(r2, rel=1e-9)
    # underlying DB should only be queried once if TTL caching works
    assert db.get_provider_token_usage.call_count == 1


def test_provider_reported_quota_precedes_telemetry_budget():
    import time
    observed = time.time()

    class Adapter:
        def collect(self, *, now: float | None = None):
            return ProviderQuotaResult(
                provider="codex",
                status="supported",
                source="fixture",
                observed_timestamp=observed,
                snapshots=(
                    ProviderQuotaSnapshot(
                        provider="codex",
                        window_name="five-hour",
                        window_duration_seconds=5 * 3600,
                        used=92,
                        remaining=8,
                        limit=100,
                        unit="percent",
                        reset_timestamp=2000.0,
                        observed_timestamp=observed,
                        source="fixture",
                    ),
                ),
            )

    db = MagicMock()
    db.get_provider_token_usage.return_value = 1
    service = ProviderQuotaService(None, adapters={"codex": Adapter()})  # type: ignore[arg-type]
    checker = ProviderUsageChecker(service)

    decision = checker.query_window_decision(
        "codex", 5, 1_000_000, 0.9, "hard_exclude", db
    )

    assert decision["source"] == "provider_reported"
    assert decision["ratio"] == pytest.approx(0.92)
    assert decision["triggered"] is True
    db.get_provider_token_usage.assert_not_called()


def test_unsupported_quota_falls_back_to_manual_budget():
    service = ProviderQuotaService(None, adapters={})
    checker = ProviderUsageChecker(service)
    db = MagicMock()
    db.get_provider_token_usage.return_value = 800

    decision = checker.query_window_decision(
        "claude-code", 5, 1000, 0.9, "prefer_alternatives", db
    )

    assert decision["source"] == "telemetry_budget"
    assert decision["ratio"] == pytest.approx(0.8)
    assert decision["fallback_reason"] == "unsupported"


def test_provider_quota_does_not_substitute_wrong_window_duration():
    import time

    class Adapter:
        def collect(self, *, now: float | None = None):
            observed = time.time()
            return ProviderQuotaResult(
                provider="codex",
                status="supported",
                source="fixture",
                observed_timestamp=observed,
                snapshots=(
                    ProviderQuotaSnapshot(
                        provider="codex",
                        window_name="weekly",
                        window_duration_seconds=168 * 3600,
                        used=95,
                        remaining=5,
                        limit=100,
                        unit="percent",
                        reset_timestamp=None,
                        observed_timestamp=observed,
                        source="fixture",
                    ),
                ),
            )

    db = MagicMock()
    db.get_provider_token_usage.return_value = 100
    checker = ProviderUsageChecker(
        ProviderQuotaService(None, adapters={"codex": Adapter()})  # type: ignore[arg-type]
    )

    decision = checker.query_window_decision(
        "codex", 5, 1000, 0.9, "hard_exclude", db
    )

    assert decision["source"] == "telemetry_budget"
    assert decision["ratio"] == pytest.approx(0.1)
    assert decision["fallback_reason"] == "no_duration_matched_configured_usage_window"

def test_below_threshold_no_change():
    # prepare config where threshold is 0.9 but usage is 0.5
    entry = UsageWindowEntry(hours=24, budget_tokens=1000, threshold=0.9, action="cost_rank_boost")
    provider_cfg = ProviderUsageWindowConfig(windows=[entry])
    cfg = make_tgsconfig({"trigger-provider": provider_cfg})

    db = MagicMock()
    db.get_provider_token_usage.return_value = 500  # 0.5 * budget

    registry = ProviderRegistry()
    # single candidate that matches provider name
    original = [DummyProvider(name="trigger-provider", cost_rank={"test-tier": 10})]
    orig_copy = copy_candidates(original)

    returned, changed = registry._apply_usage_window_overrides(original, tier="test-tier", config=cfg, db=db)
    # candidates should be unchanged and no change flagged
    assert not changed
    assert provider_names(returned) == provider_names(orig_copy)
    # cost ranks unchanged
    assert returned[0].cost_rank["test-tier"] == 10

def test_threshold_cost_rank_boost():
    # action: cost_rank_boost should move provider to end and set cost_rank for tier to 9999
    budget = 1000
    entry = UsageWindowEntry(hours=24, budget_tokens=budget, threshold=0.9, action="cost_rank_boost")
    provider_cfg = ProviderUsageWindowConfig(windows=[entry])
    cfg = make_tgsconfig({"boost-provider": provider_cfg})

    db = MagicMock()
    db.get_provider_token_usage.return_value = int(budget * 0.95)  # 95% usage -> above threshold

    registry = ProviderRegistry()
    # create two providers where the first is the one to be boosted
    p1 = DummyProvider(name="boost-provider", cost_rank={"low": 1})
    p2 = DummyProvider(name="other-provider", cost_rank={"low": 2})
    original = [p1, p2]
    orig_names = provider_names(original)
    returned, changed = registry._apply_usage_window_overrides(original, tier="low", config=cfg, db=db)

    assert changed is True
    # the boosted provider should be at the end
    assert provider_names(returned)[-1] == "boost-provider"
    # cost_rank for the boosted provider in returned copy should be 9999
    boosted = next(p for p in returned if p.name == "boost-provider")
    assert boosted.cost_rank["low"] == 9999
    # original should remain unchanged
    assert original[0].cost_rank["low"] == 1
    assert provider_names(original) == orig_names


def test_usage_window_overrides_accept_dict_backed_registry_config():
    budget = 1000
    cfg = {
        "provider_usage_windows": {
            "boost-provider": {
                "windows": [
                    {
                        "hours": 24,
                        "budget_tokens": budget,
                        "threshold": 0.9,
                        "action": "cost_rank_boost",
                    }
                ]
            }
        }
    }

    db = MagicMock()
    db.get_provider_token_usage.return_value = int(budget * 0.95)

    registry = ProviderRegistry()
    p1 = DummyProvider(name="boost-provider", cost_rank={"low": 1})
    p2 = DummyProvider(name="other-provider", cost_rank={"low": 2})

    returned, changed = registry._apply_usage_window_overrides([p1, p2], tier="low", config=cfg, db=db)

    assert changed is True
    assert provider_names(returned)[-1] == "boost-provider"
    assert next(p for p in returned if p.name == "boost-provider").cost_rank["low"] == 9999


def test_usage_window_overrides_normalize_dict_keys():
    budget = 1000
    cfg = {
        "provider_usage_windows": {
            "Boost_Provider": {
                "windows": [
                    {
                        "hours": 24,
                        "budget_tokens": budget,
                        "threshold": 0.9,
                        "action": "prefer_alternatives",
                    }
                ]
            }
        }
    }

    db = MagicMock()
    db.get_provider_token_usage.return_value = int(budget * 0.95)

    registry = ProviderRegistry()
    p1 = DummyProvider(name="boost-provider", cost_rank={"low": 1})
    p2 = DummyProvider(name="other-provider", cost_rank={"low": 2})

    returned, changed = registry._apply_usage_window_overrides([p1, p2], tier="low", config=cfg, db=db)

    assert changed is True
    assert provider_names(returned) == ["other-provider", "boost-provider"]


def test_threshold_hard_exclude():
    # action: hard_exclude should remove provider from returned list
    budget = 2000
    entry = UsageWindowEntry(hours=1, budget_tokens=budget, threshold=0.9, action="hard_exclude")
    provider_cfg = ProviderUsageWindowConfig(windows=[entry])
    cfg = make_tgsconfig({"exclude-provider": provider_cfg})

    db = MagicMock()
    db.get_provider_token_usage.return_value = int(budget * 0.95)  # 95% usage -> above threshold

    registry = ProviderRegistry()
    p1 = DummyProvider(name="exclude-provider", cost_rank={"t": 5})
    p2 = DummyProvider(name="keep-provider", cost_rank={"t": 3})
    original = [p1, p2]

    returned, changed = registry._apply_usage_window_overrides(original, tier="t", config=cfg, db=db)
    assert changed is True
    # excluded provider should not be present
    assert "exclude-provider" not in provider_names(returned)
    # other provider should remain
    assert "keep-provider" in provider_names(returned)
    # original untouched
    assert "exclude-provider" in provider_names(original)

def test_prefer_alternatives_moves_to_end():
    # action: prefer_alternatives should move the triggered provider to the end
    budget = 10000
    entry = UsageWindowEntry(hours=2, budget_tokens=budget, threshold=0.9, action="prefer_alternatives")
    provider_cfg = ProviderUsageWindowConfig(windows=[entry])
    cfg = make_tgsconfig({"pref-provider": provider_cfg})

    db = MagicMock()
    db.get_provider_token_usage.side_effect = lambda provider_name, since_ts: (int(budget * 0.95) if provider_name == "pref-provider" else int(budget * 0.1))

    registry = ProviderRegistry()
    p_trigger = DummyProvider(name="pref-provider", cost_rank={"tierX": 7})
    p_other = DummyProvider(name="other-provider", cost_rank={"tierX": 2})
    original = [p_trigger, p_other]

    returned, changed = registry._apply_usage_window_overrides(original, tier="tierX", config=cfg, db=db)
    assert changed is True
    # triggered provider should be last
    assert provider_names(returned)[-1] == "pref-provider"
    # other provider should now come before it
    assert provider_names(returned)[0] == "other-provider"
    # original order unchanged
    assert provider_names(original) == ["pref-provider", "other-provider"]
