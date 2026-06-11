#!/usr/bin/env python3
"""Legacy ProviderAdapter wrapper for Codex provider.

Exposes CodexProvider through ProviderAdapter contract for MCP registry consumption.
Per D-11 (both registry/MCP path and thin entry-point pattern) and D-01 (truthful routing).

The adapter provides metadata for cross-shell routing including:
- Tier models mapping (low/medium/high) to Codex tier models
- Cost ranking for tier selection
- Detection status (auth required, D-01 truthfulness)
"""
from __future__ import annotations

from codex.providers import CODEX_TIER_MAP, CodexProvider
from shared.adapters import ProviderAdapter, ProviderCapability


def adapter_from_legacy(
    provider_module: type[CodexProvider] | CodexProvider = CodexProvider,
) -> ProviderAdapter:
    """Wrap Codex provider in the adapter contract for MCP registry."""
    instance = provider_module() if isinstance(provider_module, type) else provider_module
    return ProviderAdapter(
        name="codex",
        version="legacy-2",
        capabilities=[ProviderCapability.EXECUTE, ProviderCapability.TOKEN_USAGE],
        metadata={
            "shell_names": ["codex", "openai-codex"],
            "provider": "codex.providers",
            "legacy_provider": "codex.providers.CodexProvider",
            "tier_models": dict(CODEX_TIER_MAP),
            "cost_rank": {
                "low": 1,
                "medium": 2,
                "high": 3,
            },
            "requirement": "CLIP-01",
            "auth_required": True,
            "auth_env_var": "OPENAI_API_KEY",
            "opt_out": True,
            "opt_out_reason": "codex",
        },
        callables={
            "build_provider": lambda: instance,
            "run": lambda *args, **kwargs: instance.execute(*args, **kwargs),
        },
    )
