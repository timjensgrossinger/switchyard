#!/usr/bin/env python3
"""Legacy shim that exposes CopilotProvider through ProviderAdapter."""
from __future__ import annotations

from typing import Any

from copilot.providers import CopilotProvider
from shared.adapters import ProviderAdapter, ProviderCapability


def _provider_factory(provider_module: Any) -> Any:
    return provider_module if callable(provider_module) else (lambda: provider_module)


def adapter_from_legacy(provider_module: Any = CopilotProvider) -> ProviderAdapter:
    """Wrap the Copilot provider class in the adapter contract."""
    factory = _provider_factory(provider_module)
    instance = factory()
    return ProviderAdapter(
        name="copilot",
        version="legacy-1",
        capabilities=[ProviderCapability.EXECUTE],
        metadata={
            "shell_names": ["copilot", "github-copilot", "gh"],
            "legacy_provider": "copilot.providers.CopilotProvider",
        },
        callables={
            "build_provider": lambda: instance,
            "run": lambda *args, **kwargs: instance.execute(*args, **kwargs),
        },
    )
