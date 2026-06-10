#!/usr/bin/env python3
"""Legacy shim that exposes GeminiProvider through ProviderAdapter."""
from __future__ import annotations

from typing import Any

from gemini.providers import GeminiProvider
from shared.adapters import ProviderAdapter, ProviderCapability


def _provider_factory(provider_module: Any) -> Any:
    return provider_module if callable(provider_module) else (lambda: provider_module)


def adapter_from_legacy(provider_module: Any = GeminiProvider) -> ProviderAdapter:
    """Wrap the Gemini provider class in the adapter contract."""
    factory = _provider_factory(provider_module)
    instance = factory()
    return ProviderAdapter(
        name="gemini",
        version="legacy-1",
        capabilities=[ProviderCapability.EXECUTE],
        metadata={
            "shell_names": ["gemini", "gemini-cli"],
            "legacy_provider": "gemini.providers.GeminiProvider",
            "opt_out": True,
            "opt_out_reason": "gemini-cli",
        },
        callables={
            "build_provider": lambda: instance,
            "run": lambda *args, **kwargs: instance.execute(*args, **kwargs),
        },
    )
