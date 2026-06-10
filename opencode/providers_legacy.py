#!/usr/bin/env python3
"""Legacy ProviderAdapter wrapper for OpenCode provider."""
from __future__ import annotations

from typing import Any

from opencode.providers import build_opencode_provider, OPENCODE_LOW_MODEL
from shared.adapters import ProviderAdapter, ProviderCapability


def _provider_factory(provider_module: Any) -> Any:
    return provider_module if callable(provider_module) else (lambda: provider_module)


def adapter_from_legacy(provider_module: Any = build_opencode_provider) -> ProviderAdapter:
    """Wrap the OpenCode provider in the adapter contract."""
    factory = _provider_factory(provider_module)
    instance = factory()
    return ProviderAdapter(
        name="opencode",
        version="legacy-1",
        capabilities=[ProviderCapability.EXECUTE],
        metadata={
            "shell_names": ["opencode"],
            "legacy_provider": "opencode.providers.build_opencode_provider",
            "tier_models": {
                "low": OPENCODE_LOW_MODEL,
            },
            "cost_rank": {
                "low": 0,
            },
            "opt_out": True,
            "opt_out_reason": "opencode",
        },
        callables={
            "build_provider": lambda: instance,
            "run": lambda *args, **kwargs: instance.execute(*args, **kwargs),
        },
    )
