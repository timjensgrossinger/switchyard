#!/usr/bin/env python3
"""Versioned provider adapter contract for cross-shell routing."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any
import re


class ProviderCapability(str, Enum):
    """Explicit features an adapter can expose."""

    EXECUTE = "execute"
    STREAM = "stream"
    REGISTER = "register"
    TOKEN_USAGE = "token_usage"


def _coerce_capability(value: ProviderCapability | str) -> ProviderCapability:
    if isinstance(value, ProviderCapability):
        return value
    if not isinstance(value, str):
        raise ValueError(f"Unsupported capability value: {value!r}")
    normalized = value.strip().upper()
    try:
        return ProviderCapability[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown capability: {value!r}") from exc


def _serialize_metadata_value(value: Any) -> Any:
    if callable(value):
        return getattr(value, "__name__", value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            field.name: _serialize_metadata_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): _serialize_metadata_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_serialize_metadata_value(item) for item in value]
    return value


@dataclass(slots=True)
class ProviderAdapter:
    """Serializable adapter metadata plus optional provider-side callables."""

    name: str
    version: str
    capabilities: list[ProviderCapability] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    callables: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.capabilities = [_coerce_capability(cap) for cap in self.capabilities]
        self.metadata = dict(self.metadata)

    def supports(self, capability: ProviderCapability | str) -> bool:
        """Return True when the adapter exposes the requested capability."""
        return _coerce_capability(capability) in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the adapter contract."""
        metadata: dict[str, Any] = {}
        for key, value in self.metadata.items():
            metadata[key] = _serialize_metadata_value(value)
        readiness = metadata.get("readiness")
        if isinstance(readiness, Mapping):
            metadata["readiness"] = dict(readiness)
        return {
            "name": self.name,
            "version": self.version,
            "capabilities": [cap.name for cap in self.capabilities],
            "metadata": metadata,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProviderAdapter":
        """Validate and rebuild an adapter from serialized metadata."""
        name = raw.get("name")
        version = raw.get("version")
        capabilities = raw.get("capabilities", [])
        metadata = raw.get("metadata", {})

        if not isinstance(name, str) or not name:
            raise ValueError("ProviderAdapter.name must be a non-empty string")
        if not isinstance(version, str) or not version:
            raise ValueError("ProviderAdapter.version must be a non-empty string")
        if not isinstance(capabilities, list):
            raise ValueError("ProviderAdapter.capabilities must be a list")
        if not isinstance(metadata, Mapping):
            raise ValueError("ProviderAdapter.metadata must be a mapping")

        return cls(
            name=name,
            version=version,
            capabilities=[_coerce_capability(cap) for cap in capabilities],
            metadata=dict(metadata),
        )

    def invoke(self, fn_name: str, *args: Any, **kwargs: Any) -> Any:
        """Call a provider-side helper when the adapter exposes it."""
        if self.callables is not None:
            call_map: Mapping[str, Any] = self.callables
        else:
            metadata_callables = self.metadata.get("callables")
            call_map = metadata_callables if isinstance(metadata_callables, Mapping) else {}
        fn = call_map.get(fn_name)
        if fn is None or not callable(fn):
            raise NotImplementedError(
                f"No callable {fn_name!r} exposed by adapter {self.name}"
            )
        return fn(*args, **kwargs)


# ============================================================================
# ExecutionResult: Normalized execution outcome across all providers
# ============================================================================


@dataclass(slots=True)
class ExecutionResult:
    """Normalized execution result from any provider adapter.
    
    For stdout-first providers (Amazon Q/Kiro, Codex, etc.):
    - text contains the full stdout output
    - metadata may contain parsed tokens, costs, usage stats
    
    For file-editing providers (Aider):
    - text contains a summary of files modified or diff output
    - metadata["files_modified"] lists edited files
    - metadata["result_type"] = "file_edits" to distinguish from text output
    """
    
    text: str
    model_used: str
    provider_name: str
    cost_estimate: float = 0.0
    metadata: dict[str, Any] | None = None
    exit_code: int = 0
    
    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


# ============================================================================
# Aider Result Extraction
# ============================================================================


def _extract_aider_result(
    provider_name: str,
    command: list[str],
    stdout: str,
    stderr: str,
    exit_code: int,
    model_used: str
) -> ExecutionResult:
    """Extract Aider execution result.
    
    Aider modifies files in-place; its stdout is explanatory chat, not the work product.
    This extractor:
    1. Captures files mentioned in command
    2. Returns a summary of what Aider claims to have done (from stderr cost tracking or stdout summary)
    3. Extracts cost estimate from Aider's stderr output (e.g., "Total cost: $0.0025")
    
    Per D-04: Treat Aider as a file-editing adapter, not stdout-first.
    Result.text is diff-style summary or modified-file list, not the full chat output.
    """
    
    # Extract target files from command
    # Command pattern: ["aider", "--model", model, "--message", prompt, "--yes-always", ..., file1, file2, ...]
    # Files are identified as non-flag arguments that appear after aider binary
    target_files = []
    i = 0
    while i < len(command):
        arg = command[i]
        
        # Skip the aider binary
        if arg == "aider":
            i += 1
            continue
        
        # Skip flags and their values
        if arg.startswith("-"):
            # Some flags take values (--model VALUE, --message VALUE, etc.)
            if arg in ("--model", "--message", "-m", "--timeout", "--max-thinking-length"):
                i += 2  # Skip flag and its value
            else:
                i += 1  # Skip flag-only (like --yes-always)
            continue
        
        # This is a positional argument (file)
        target_files.append(arg)
        i += 1
    
    # Extract cost from stderr (Aider prints cost tracking like "Total cost: $0.0025")
    cost_estimate = 0.0
    cost_match = re.search(r"Total cost:\s+\$([0-9.]+)", stderr)
    if cost_match:
        try:
            cost_estimate = float(cost_match.group(1))
        except ValueError:
            pass
    
    # Build result summary
    # Prefer stderr cost summary over stdout chatter
    result_text = ""
    if cost_match:
        result_text = f"Modified {len(target_files)} file(s). {cost_match.group(0)}"
    else:
        # Fallback: summarize what we know
        result_text = f"Modified {len(target_files)} file(s)"
    
    if stderr.strip():
        # Append key info from stderr (e.g., error messages if exit_code != 0)
        if exit_code != 0:
            result_text += f"\nWarning: exit code {exit_code}. Details: {stderr[:200]}"
    
    return ExecutionResult(
        text=result_text,
        model_used=model_used,
        provider_name=provider_name,
        cost_estimate=cost_estimate,
        metadata={
            "files_modified": target_files,
            "result_type": "file_edits",
            "exit_code": exit_code,
        },
        exit_code=exit_code
    )


# ============================================================================
# Amazon Q/Kiro Result Extraction
# ============================================================================


def _extract_q_kiro_result(
    provider_name: str,
    command: list[str],
    stdout: str,
    stderr: str,
    exit_code: int,
    model_used: str
) -> ExecutionResult:
    """Extract Amazon Q/Kiro execution result.
    
    Amazon Q/Kiro is stdout-first (unlike Aider). Output from `q chat --no-interactive`
    or `kiro chat --no-interactive` is returned as normalized text.
    
    This extractor:
    1. Returns stdout as the work product (normalized and cleaned)
    2. Handles error cases (exit_code != 0, auth failures)
    3. Cost estimate defaults to 0 (Amazon Q/Kiro doesn't expose per-invocation costs via CLI)
    """
    
    # Amazon Q/Kiro stdout is the work product
    result_text = stdout.strip()
    
    # If execution failed, include error context
    if exit_code != 0:
        result_text = f"Error (exit code {exit_code}): {stderr.strip()[:200]}"
    
    # Amazon Q/Kiro doesn't expose per-invocation cost via CLI; estimate as 0
    cost_estimate = 0.0
    
    return ExecutionResult(
        text=result_text,
        model_used=model_used,
        provider_name=provider_name,
        cost_estimate=cost_estimate,
        metadata={
            "result_type": "text_output",
            "exit_code": exit_code,
            "stderr_length": len(stderr),
        },
        exit_code=exit_code
    )
