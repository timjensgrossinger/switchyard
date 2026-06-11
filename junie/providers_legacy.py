#!/usr/bin/env python3
"""Legacy ProviderAdapter wrapper for Junie provider.

Exposes JunieProvider through ProviderAdapter contract for MCP registry consumption.
Per D-06, D-07, D-09 (single-tier truthfulness, dual-auth support, telemetry capability).

The adapter provides metadata for cross-shell routing including:
- Single-tier model (no per-call --model flag; configured at auth time per D-06)
- Dual-auth support (BYOK + JetBrains-managed per D-07)
- TOKEN_USAGE capability for telemetry extraction per D-09
- Cost ranking for tier selection
"""
from __future__ import annotations

from typing import Any

from shared.adapters import ProviderAdapter, ProviderCapability


def adapter_from_legacy() -> ProviderAdapter:
    """Wrap Junie provider in the adapter contract for MCP registry."""
    return ProviderAdapter(
        name="junie",
        version="1.0",
        capabilities=[ProviderCapability.EXECUTE, ProviderCapability.TOKEN_USAGE],
        metadata={
            "shell_names": ["junie", "jetbrains-junie"],
            "provider": "junie.providers",
            "tier_models": {
                "medium": "configured-model",  # Single tier per D-06
            },
            "cost_rank": {
                "medium": 2,
            },
            "requirement": "CLIP-03",
            "auth_required": True,
            "auth_methods": [
                "JUNIE_API_KEY",  # BYOK per D-07
                "~/.local/share/junie/config.json",  # BYOK config file
                "~/.jetbrains/junie/auth",  # JetBrains-managed per D-07
            ],
            "telemetry_field": "llmUsage",  # Per D-09, D-10
            "single_tier": True,  # Per D-06 truthfulness
        },
        callables={
            "build_provider": lambda: None,  # Placeholder; actual provider instantiation in entry.py
            "run": lambda *args, **kwargs: None,  # Placeholder; see entry.py
        },
    )
