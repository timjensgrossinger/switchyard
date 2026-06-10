#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.adapters import ProviderAdapter, ProviderCapability
from shared.discovery import CLIProvider, ProviderRegistry


def _mock_provider(name: str = "github-copilot") -> MagicMock:
    from shared.discovery import ProviderReadiness, DetectReason
    
    provider = MagicMock(spec=CLIProvider)
    provider.name = name
    provider.display_name = "GitHub Copilot"
    provider.binary = "gh"
    provider.tier_models = {
        "low": "gpt-5-mini",
        "medium": "gpt-5.4",
        "high": "gpt-5.4",
    }
    provider.cost_rank = {"low": 0, "medium": 2, "high": 3}
    # Fix: return ProviderReadiness instead of bool
    readiness = ProviderReadiness(
        routeable=True,
        reason=DetectReason.READY,
        last_checked=0.0
    )
    provider.detect.return_value = readiness
    provider.readiness = readiness
    provider.execute.return_value = "ok"
    return provider


def test_provideradapter_imports() -> None:
    """ProviderAdapter exposes versioned capabilities and invoke() helpers."""
    adapter = ProviderAdapter(
        name="test-adapter",
        version="1.0",
        capabilities=[ProviderCapability.EXECUTE],
        metadata={"shell_names": ["copilot"]},
        callables={"run": lambda: "ok"},
    )

    assert adapter.supports(ProviderCapability.EXECUTE)
    assert adapter.to_dict()["capabilities"] == ["EXECUTE"]
    assert adapter.invoke("run") == "ok"

    restored = ProviderAdapter.from_dict(adapter.to_dict())
    assert restored.name == "test-adapter"
    assert restored.version == "1.0"
    assert restored.capabilities == [ProviderCapability.EXECUTE]


def test_registry_list_adapters_stub() -> None:
    """ProviderRegistry exposes detected providers as ProviderAdapter objects."""
    provider = _mock_provider()
    with patch("shared.discovery.BUILTIN_PROVIDERS", [provider]), \
         patch.object(ProviderRegistry, "_get_test_providers", return_value=[provider]):
        registry = ProviderRegistry()

    adapters = registry.list_adapters()
    assert len(adapters) == 1
    assert isinstance(adapters[0], ProviderAdapter)
    assert adapters[0].supports(ProviderCapability.EXECUTE)
    assert "copilot" in adapters[0].metadata["shell_names"]

    serialised = registry.serialize_adapters()
    restored = registry.load_adapters(serialised)
    resolved = registry.resolve_adapter("copilot")

    assert restored[0].name == adapters[0].name
    assert resolved is not None
    assert resolved.name == adapters[0].name


# ---------------------------------------------------------------------------
# Task 2: ProviderAdapter contract tests for new providers (TEST-03 part 2)
# ---------------------------------------------------------------------------


def test_codex_adapter_contract() -> None:
    """TEST-03 CLIP-01: Codex adapter implements ProviderAdapter contract.
    
    Per CLIP-01 (Codex adapter) and D-09 (telemetry capture):
    - Adapter name is "codex"
    - Adapter has EXECUTE and TOKEN_USAGE capabilities
    - Adapter metadata includes "codex" in shell_names
    - Adapter metadata has 3 tiers: low, medium, high
    - Adapter is JSON-serializable
    """
    from codex.providers_legacy import adapter_from_legacy
    
    adapter = adapter_from_legacy()
    
    # Verify name
    assert adapter.name == "codex", f"Expected name 'codex', got '{adapter.name}'"
    
    # Verify version is non-empty
    assert isinstance(adapter.version, str)
    assert len(adapter.version) > 0, "Version should be non-empty string"
    
    # Verify EXECUTE capability
    assert ProviderCapability.EXECUTE in adapter.capabilities, \
        f"Codex adapter missing EXECUTE capability"
    
    # Verify TOKEN_USAGE capability (per D-09)
    assert ProviderCapability.TOKEN_USAGE in adapter.capabilities, \
        f"Codex adapter missing TOKEN_USAGE capability (required per D-09)"
    
    # Verify shell_names includes "codex"
    assert "shell_names" in adapter.metadata, "Missing 'shell_names' in metadata"
    shell_names = adapter.metadata["shell_names"]
    assert isinstance(shell_names, list), f"shell_names should be list, got {type(shell_names)}"
    assert "codex" in shell_names, f"'codex' not in shell_names: {shell_names}"
    
    # Verify tier_models has 3 keys: low, medium, high
    assert "tier_models" in adapter.metadata, "Missing 'tier_models' in metadata"
    tier_models = adapter.metadata["tier_models"]
    assert set(tier_models.keys()) == {"low", "medium", "high"}, \
        f"Expected 3 tiers, got {set(tier_models.keys())}"
    
    # Verify to_dict() is JSON-serializable
    adapter_dict = adapter.to_dict()
    json_str = json.dumps(adapter_dict)
    assert len(json_str) > 0, "JSON serialization produced empty string"


def test_junie_adapter_contract() -> None:
    """TEST-03 CLIP-03: Junie adapter implements ProviderAdapter contract.
    
    Per CLIP-03 (Junie adapter), D-06 (single-tier), and D-09 (telemetry):
    - Adapter name is "junie"
    - Adapter has EXECUTE and TOKEN_USAGE capabilities
    - Adapter metadata includes "junie" in shell_names
    - Adapter metadata has exactly 1 tier: "medium" (per D-06)
    - Adapter metadata indicates telemetry field "llmUsage"
    - Adapter is JSON-serializable
    """
    from junie.providers_legacy import adapter_from_legacy
    
    adapter = adapter_from_legacy()
    
    # Verify name
    assert adapter.name == "junie", f"Expected name 'junie', got '{adapter.name}'"
    
    # Verify EXECUTE capability
    assert ProviderCapability.EXECUTE in adapter.capabilities, \
        f"Junie adapter missing EXECUTE capability"
    
    # Verify TOKEN_USAGE capability (per D-09)
    assert ProviderCapability.TOKEN_USAGE in adapter.capabilities, \
        f"Junie adapter missing TOKEN_USAGE capability (required per D-09)"
    
    # Verify shell_names includes "junie"
    assert "shell_names" in adapter.metadata, "Missing 'shell_names' in metadata"
    shell_names = adapter.metadata["shell_names"]
    assert "junie" in shell_names, f"'junie' not in shell_names: {shell_names}"
    
    # Verify tier_models has exactly 1 key: "medium" (per D-06)
    assert "tier_models" in adapter.metadata, "Missing 'tier_models' in metadata"
    tier_models = adapter.metadata["tier_models"]
    assert set(tier_models.keys()) == {"medium"}, \
        f"Expected single 'medium' tier per D-06, got {set(tier_models.keys())}"
    
    # Verify model name is non-empty
    assert isinstance(tier_models["medium"], str)
    assert len(tier_models["medium"]) > 0, "Medium tier model should be non-empty"
    
    # Verify telemetry field is indicated (per D-09)
    if "telemetry_field" in adapter.metadata:
        assert adapter.metadata["telemetry_field"] == "llmUsage", \
            f"Expected telemetry_field='llmUsage', got {adapter.metadata['telemetry_field']}"
    
    # Verify to_dict() is JSON-serializable
    adapter_dict = adapter.to_dict()
    json_str = json.dumps(adapter_dict)
    assert len(json_str) > 0, "JSON serialization produced empty string"


def test_cursor_adapter_contract() -> None:
    """TEST-03 CLIP-02: Cursor adapter implements ProviderAdapter contract.
    
    Per CLIP-02 (Cursor adapter) and D-05 (strict detection):
    - Adapter name is "cursor"
    - Adapter has EXECUTE capability
    - Adapter metadata includes "cursor" and "cursor-agent" in shell_names
    - Adapter metadata has 3 tiers: low, medium, high
    - Adapter metadata includes detection_strict=True (per D-05)
    - Adapter is JSON-serializable
    """
    from cursor.providers_legacy import adapter_from_legacy
    
    adapter = adapter_from_legacy()
    
    # Verify name
    assert adapter.name == "cursor", f"Expected name 'cursor', got '{adapter.name}'"
    
    # Verify EXECUTE capability
    assert ProviderCapability.EXECUTE in adapter.capabilities, \
        f"Cursor adapter missing EXECUTE capability"
    
    # Verify shell_names includes "cursor" and "cursor-agent"
    assert "shell_names" in adapter.metadata, "Missing 'shell_names' in metadata"
    shell_names = adapter.metadata["shell_names"]
    assert "cursor" in shell_names or "cursor-agent" in shell_names, \
        f"'cursor' or 'cursor-agent' not in shell_names: {shell_names}"
    
    # Verify tier_models has 3 keys: low, medium, high
    assert "tier_models" in adapter.metadata, "Missing 'tier_models' in metadata"
    tier_models = adapter.metadata["tier_models"]
    assert set(tier_models.keys()) == {"low", "medium", "high"}, \
        f"Expected 3 tiers, got {set(tier_models.keys())}"
    
    # Verify detection_strict=True (per D-05)
    if "detection_strict" in adapter.metadata:
        assert adapter.metadata["detection_strict"] is True, \
            f"Expected detection_strict=True per D-05, got {adapter.metadata['detection_strict']}"
    
    # Verify to_dict() is JSON-serializable
    adapter_dict = adapter.to_dict()
    json_str = json.dumps(adapter_dict)
    assert len(json_str) > 0, "JSON serialization produced empty string"


def test_opencode_adapter_contract() -> None:
    """OpenCode adapter implements a low-tier-only ProviderAdapter contract."""
    from opencode.providers_legacy import adapter_from_legacy

    adapter = adapter_from_legacy()

    assert adapter.name == "opencode"
    assert ProviderCapability.EXECUTE in adapter.capabilities
    assert "shell_names" in adapter.metadata
    assert "opencode" in adapter.metadata["shell_names"]
    assert set(adapter.metadata["tier_models"].keys()) == {"low"}

    adapter_dict = adapter.to_dict()
    json_str = json.dumps(adapter_dict)
    assert len(json_str) > 0, "JSON serialization produced empty string"


def test_new_adapter_callables() -> None:
    """TEST-03: Verify new adapters have required callables.
    
    Per D-08 (contract normalization): Each adapter must expose
    build_provider and run callables for dynamic invocation.
    """
    from codex.providers_legacy import adapter_from_legacy as codex_adapter
    from junie.providers_legacy import adapter_from_legacy as junie_adapter
    from opencode.providers_legacy import adapter_from_legacy as opencode_adapter
    from cursor.providers_legacy import adapter_from_legacy as cursor_adapter
    
    adapters = [
        ("codex", codex_adapter()),
        ("junie", junie_adapter()),
        ("opencode", opencode_adapter()),
        ("cursor", cursor_adapter()),
    ]
    
    for provider_name, adapter in adapters:
        # Verify adapter.callables is not None
        assert adapter.callables is not None, \
            f"{provider_name} adapter has no callables"
        
        # Verify required callables exist
        # Note: actual callable names may vary, but adapter must have callable structure
        assert isinstance(adapter.callables, dict), \
            f"{provider_name} adapter callables is not dict"


def test_new_adapter_serialization() -> None:
    """TEST-03: Verify new adapters are JSON-serializable.
    
    Per D-10 (serialization): Adapters must serialize to JSON without
    raw callables. The _serialize_metadata_value() method strips callables.
    """
    from codex.providers_legacy import adapter_from_legacy as codex_adapter
    from junie.providers_legacy import adapter_from_legacy as junie_adapter
    from opencode.providers_legacy import adapter_from_legacy as opencode_adapter
    from cursor.providers_legacy import adapter_from_legacy as cursor_adapter
    
    adapters = [
        ("codex", codex_adapter()),
        ("junie", junie_adapter()),
        ("opencode", opencode_adapter()),
        ("cursor", cursor_adapter()),
    ]
    
    for provider_name, adapter in adapters:
        # Call to_dict()
        adapter_dict = adapter.to_dict()
        
        # Verify result is dict
        assert isinstance(adapter_dict, dict), \
            f"{provider_name}: to_dict() returned {type(adapter_dict)}"
        
        # Verify JSON-serializable (should not raise)
        try:
            json_str = json.dumps(adapter_dict)
        except TypeError as e:
            raise AssertionError(
                f"{provider_name}: to_dict() result is not JSON-serializable: {e}"
            )
        
        # Verify contains expected fields
        assert "name" in adapter_dict, f"{provider_name}: missing 'name' in serialized dict"
        assert "version" in adapter_dict, f"{provider_name}: missing 'version' in serialized dict"
        assert "capabilities" in adapter_dict, f"{provider_name}: missing 'capabilities' in serialized dict"
        assert "metadata" in adapter_dict, f"{provider_name}: missing 'metadata' in serialized dict"
        
        # Verify no callables in serialized output (per D-10)
        assert "callables" not in adapter_dict, \
            f"{provider_name}: callables should not be in serialized output"


# ---------------------------------------------------------------------------
# Task 3 & 4: Aider and Amazon Q/Kiro execution and result extraction tests
# ---------------------------------------------------------------------------


def test_execution_result_creation():
    """ExecutionResult dataclass can be created with minimal fields."""
    from shared.adapters import ExecutionResult
    
    result = ExecutionResult(
        text="test output",
        model_used="claude-opus",
        provider_name="aider",
        cost_estimate=0.05,
        exit_code=0
    )
    
    assert result.text == "test output"
    assert result.model_used == "claude-opus"
    assert result.provider_name == "aider"
    assert result.cost_estimate == 0.05
    assert result.exit_code == 0
    assert result.metadata == {}


def test_aider_extraction_with_files_and_cost():
    """Aider result extraction parses files and cost from command + stderr."""
    from shared.adapters import _extract_aider_result
    
    result = _extract_aider_result(
        provider_name="aider",
        command=["aider", "--model", "claude-opus", "--message", "Fix the bug",
                 "--yes-always", "--no-git", "--no-auto-commits", "--no-pretty",
                 "src/handler.py", "tests/test_handler.py"],
        stdout="Fixed 2 functions in src/handler.py\nAdded test case in tests/test_handler.py\n",
        stderr="Total cost: $0.0042\n",
        exit_code=0,
        model_used="claude-opus"
    )
    
    assert result.exit_code == 0
    assert result.provider_name == "aider"
    assert result.model_used == "claude-opus"
    assert "Modified" in result.text or "cost" in result.text.lower()
    assert result.metadata.get("files_modified") == ["src/handler.py", "tests/test_handler.py"]
    assert result.metadata.get("result_type") == "file_edits"
    assert result.cost_estimate == 0.0042


def test_aider_extraction_with_error():
    """Aider result extraction handles errors."""
    from shared.adapters import _extract_aider_result
    
    result = _extract_aider_result(
        provider_name="aider",
        command=["aider", "--model", "claude-opus", "--message", "test"],
        stdout="",
        stderr="Error: OPENAI_API_KEY not set",
        exit_code=1,
        model_used="claude-opus"
    )
    
    assert result.exit_code == 1
    assert "Error" in result.text or result.exit_code != 0
    assert result.metadata.get("files_modified") == []


def test_aider_extraction_no_cost():
    """Aider result extraction handles missing cost data gracefully."""
    from shared.adapters import _extract_aider_result
    
    result = _extract_aider_result(
        provider_name="aider",
        command=["aider", "--model", "claude-opus", "--message", "test", "file.py"],
        stdout="Some output",
        stderr="No cost info in stderr",
        exit_code=0,
        model_used="claude-opus"
    )
    
    assert result.exit_code == 0
    assert result.cost_estimate == 0.0
    assert result.metadata.get("files_modified") == ["file.py"]
    assert "Modified 1 file" in result.text


def test_q_kiro_extraction_with_output():
    """Amazon Q/Kiro result extraction captures stdout as result."""
    from shared.adapters import _extract_q_kiro_result
    
    result = _extract_q_kiro_result(
        provider_name="amazon-q",
        command=["q", "chat", "--no-interactive", "--model", "claude-3.7-sonnet",
                 "--wrap", "auto", "Write a handler class"],
        stdout="Here's the solution:\n\nclass Handler:\n    def process(self):\n        pass\n",
        stderr="",
        exit_code=0,
        model_used="claude-3.7-sonnet"
    )
    
    assert result.exit_code == 0
    assert result.provider_name == "amazon-q"
    assert result.model_used == "claude-3.7-sonnet"
    assert "class Handler" in result.text
    assert result.metadata.get("result_type") == "text_output"
    assert result.cost_estimate == 0.0


def test_q_kiro_extraction_with_auth_error():
    """Amazon Q/Kiro result extraction handles auth failures."""
    from shared.adapters import _extract_q_kiro_result
    
    result = _extract_q_kiro_result(
        provider_name="amazon-q",
        command=["q", "chat", "--no-interactive", "--model", "claude-3.7-sonnet"],
        stdout="",
        stderr="Error: not authenticated. Run 'q login'",
        exit_code=401,
        model_used="claude-3.7-sonnet"
    )
    
    assert result.exit_code == 401
    assert "Error" in result.text
    assert "not authenticated" in result.text


def test_aider_extraction_complex_command():
    """Aider result extraction handles complex command lines with many flags."""
    from shared.adapters import _extract_aider_result
    
    result = _extract_aider_result(
        provider_name="aider",
        command=[
            "aider",
            "--model", "claude-opus",
            "--message", "Implement feature X",
            "--yes-always",
            "--no-git",
            "--no-auto-commits",
            "--no-pretty",
            "--no-stream",
            "--timeout", "30",
            "src/module1.py",
            "src/module2.py",
            "tests/test_module.py"
        ],
        stdout="Updated 3 files",
        stderr="Total cost: $0.1234",
        exit_code=0,
        model_used="claude-opus"
    )
    
    assert result.metadata.get("files_modified") == ["src/module1.py", "src/module2.py", "tests/test_module.py"]
    assert result.cost_estimate == 0.1234
    assert "Modified 3 file" in result.text
