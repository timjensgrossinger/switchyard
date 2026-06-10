#!/usr/bin/env python3
"""Legacy ProviderAdapter wrapper for Cursor provider.

Exposes CursorProvider through ProviderAdapter contract for MCP registry consumption.
Per D-01, D-02, D-05 (first-class execution adapter, workspace-write behavior, strict headless detection).

The adapter provides metadata for cross-shell routing including:
- Tier models mapping (low/medium/high) to Cursor (Claude) tier models
- Cost ranking for tier selection
- Strict headless binary detection metadata per D-05
- Trust boundary awareness per D-02, D-03
"""
from __future__ import annotations

from typing import Any

from shared.adapters import ProviderAdapter, ProviderCapability


def adapter_from_legacy() -> ProviderAdapter:
    """Wrap Cursor provider in the adapter contract for MCP registry."""
    return ProviderAdapter(
        name="cursor",
        version="1.0",
        capabilities=[ProviderCapability.EXECUTE],
        metadata={
            "shell_names": ["cursor", "cursor-agent"],
            "provider": "cursor.providers",
            "tier_models": {
                "low": "claude-haiku",
                "medium": "claude-sonnet",
                "high": "claude-opus",
            },
            "cost_rank": {
                "low": 2,
                "medium": 3,
                "high": 4,
            },
            "requirement": "CLIP-02",
            "detection_strict": True,  # Per D-05: only cursor-agent headless binary
            "supports_workspace_write": True,  # Per D-02 intent
            "trust_boundary_aware": True,  # Per D-03: enforced in shared/context.py
        },
        callables={
            "build_provider": lambda: None,  # Placeholder; actual provider instantiation in entry.py
            "run": lambda *args, **kwargs: None,  # Placeholder; see entry.py
        },
    )
