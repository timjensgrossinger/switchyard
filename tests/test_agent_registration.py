#!/usr/bin/env python3
"""Tests for Wave 2b: Approval, activation, and registration with operator control."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.adapters import ProviderAdapter, ProviderCapability
from shared.agents import (
    build_learned_agent_runtime_context,
    pattern_hash,
    register_agent_definition,
    activate_agent_locally,
    register_agent_to_capable_clis,
)
from shared.db import Database
from shared.discovery import CLIProvider, ProviderRegistry


def test_agent_registers_only_to_compatible_adapters(tmp_path: Path) -> None:
    """Registration persists only when the adapter explicitly supports REGISTER."""
    db = Database(tmp_path / "agents.db")
    exported: list[dict] = []
    adapter = ProviderAdapter(
        name="registering-adapter",
        version="1.0",
        capabilities=[ProviderCapability.REGISTER],
        metadata={},
        callables={"export": lambda payload: exported.append(payload)},
    )
    agent_def = {"name": "phase2-agent", "instructions": "Do the thing"}

    assert register_agent_definition(agent_def, adapter, db) is True
    assert len(exported) == 1
    assert exported[0]["name"] == agent_def["name"]
    assert exported[0]["instructions"] == agent_def["instructions"]
    assert exported[0]["pattern_hash"] == pattern_hash(agent_def["name"])

    canonical_id = pattern_hash(agent_def["name"])
    stored = db.get_agent_definition(canonical_id)
    assert stored is not None
    assert "phase2-agent" in stored["definition"]

    incompatible = ProviderAdapter(
        name="read-only-adapter",
        version="1.0",
        capabilities=[ProviderCapability.EXECUTE],
        metadata={},
    )
    assert register_agent_definition(agent_def, incompatible, db) is False


def test_claude_provider_exports_agent_to_project_repo(tmp_path: Path) -> None:
    provider = CLIProvider(
        name="claude-code",
        binary="claude",
        display_name="Claude Code",
        tier_models={"low": "haiku", "medium": "sonnet", "high": "opus"},
        cost_rank={"low": 1, "medium": 2, "high": 3},
        supports_registration=True,
    )
    agent_def = {
        "name": "Router Learned Agent",
        "project_path": str(tmp_path),
        "instructions": "---\nname: \"router-learned-agent\"\ndescription: \"Reusable agent\"\ntools: \"Read, Edit\"\nmodel: \"sonnet\"\n---\n\n## Workflow\n- Do the work.\n",
    }

    export_result = provider.export_agent(agent_def)

    export_path = tmp_path / ".claude" / "agents" / "router-learned-agent.md"
    assert export_result == str(export_path)
    assert export_path.exists()
    assert export_path.read_text(encoding="utf-8").startswith("---\n")


def test_register_agent_to_capable_clis_reads_modern_draft_payload(tmp_path: Path) -> None:
    db = Database(tmp_path / "agents.db")
    fingerprint = "draft-fingerprint-123"
    draft = {
        "id": "draft-1",
        "name": "Router Learned Agent",
        "project_id": str(tmp_path),
        "instructions": "---\nname: \"router-learned-agent\"\ndescription: \"Reusable agent\"\ntools: \"Read, Edit\"\nmodel: \"sonnet\"\n---\n\n## Workflow\n- Do the work.\n",
        "status": "active",
    }
    db.save_agent_definition(
        fingerprint,
        draft["name"],
        json.dumps(draft, sort_keys=True),
        promotion_state="active",
        match_count=1,
    )

    provider = CLIProvider(
        name="claude-code",
        binary="claude",
        display_name="Claude Code",
        tier_models={"low": "haiku", "medium": "sonnet", "high": "opus"},
        cost_rank={"low": 1, "medium": 2, "high": 3},
        supports_registration=True,
    )
    registry_instance = MagicMock()
    registry_instance.list_providers = MagicMock(return_value=[provider])
    registry_instance.get_provider_capability = MagicMock(
        side_effect=lambda provider_id, capability: (
            provider_id == "claude-code" and capability == ProviderCapability.REGISTER
        )
    )

    result = register_agent_to_capable_clis(db, fingerprint, registry_instance)

    export_path = tmp_path / ".claude" / "agents" / "router-learned-agent.md"
    assert result["success_targets"] == ["claude-code"]
    assert result["failed_targets"] == []
    assert export_path.exists()
    assert "## Workflow" in export_path.read_text(encoding="utf-8")
    stored = db.agent_definition_get(fingerprint)
    assert stored is not None
    assert stored["pattern_hash"] == fingerprint


# =============================================================================
# Wave 2b Tests: Approval, Activation, and Registration
# =============================================================================


def test_agent_registers_only_to_compatible_providers(tmp_path: Path) -> None:
    """Test that registration only targets providers with REGISTER capability."""
    db = Database(tmp_path / "test.db")
    
    # Create an agent in the database
    agent_id = "test-agent-123"
    db.agent_definition_insert(
        project_id=None,
        lane="shared",
        pattern_hash="hash123",
        pattern_desc="Test pattern",
        description="Test agent",
        agent_id=agent_id,
        status="active"
    )
    
    # Mock provider registry with two providers: one with REGISTER, one without
    with patch("shared.discovery.ProviderRegistry") as MockRegistry:
        provider_a = MagicMock(spec=CLIProvider)
        provider_a.provider_id = "provider-a"
        provider_a.export_agent = MagicMock(return_value=True)
        
        provider_b = MagicMock(spec=CLIProvider)
        provider_b.provider_id = "provider-b"
        provider_b.export_agent = MagicMock(return_value=False)
        
        registry_instance = MagicMock()
        registry_instance.list_providers = MagicMock(return_value=[provider_a, provider_b])
        registry_instance.get_provider_capability = MagicMock(side_effect=lambda p_id, cap: (
            p_id == "provider-a" and cap == ProviderCapability.REGISTER
        ))
        MockRegistry.return_value = registry_instance
        
        # Call registration
        result = register_agent_to_capable_clis(db, agent_id, registry_instance)
        
        # Verify only REGISTER-capable provider was called
        assert "provider-a" in result["success_targets"]
        provider_a.export_agent.assert_called_once()
        provider_b.export_agent.assert_not_called()


def test_registration_non_blocking_on_failure(tmp_path: Path) -> None:
    """Test that registration failures don't raise and are logged as warnings."""
    db = Database(tmp_path / "test.db")
    
    # Create an agent in the database
    agent_id = "test-agent-456"
    db.agent_definition_insert(
        project_id=None,
        lane="shared",
        pattern_hash="hash456",
        pattern_desc="Test pattern",
        description="Test agent",
        agent_id=agent_id,
        status="active"
    )
    
    # Mock provider registry with a provider that raises an exception
    with patch("shared.discovery.ProviderRegistry") as MockRegistry:
        provider_fail = MagicMock(spec=CLIProvider)
        provider_fail.provider_id = "failing-provider"
        provider_fail.export_agent = MagicMock(side_effect=Exception("Network error"))
        
        registry_instance = MagicMock()
        registry_instance.list_providers = MagicMock(return_value=[provider_fail])
        registry_instance.get_provider_capability = MagicMock(return_value=True)
        MockRegistry.return_value = registry_instance
        
        # Call registration - should NOT raise
        result = register_agent_to_capable_clis(db, agent_id, registry_instance)
        
        # Verify result structure
        assert result["success_targets"] == []
        assert len(result["failed_targets"]) == 1
        assert result["failed_targets"][0]["provider_id"] == "failing-provider"
        assert "Network error" in result["failed_targets"][0]["error"]


def test_approval_activates_agent_locally(tmp_path: Path) -> None:
    """Test that approval activation changes agent status to 'active'."""
    db = Database(tmp_path / "test.db")
    
    # Create an agent in pending state
    agent_id = "test-agent-789"
    db.agent_definition_insert(
        project_id=None,
        lane="shared",
        pattern_hash="hash789",
        pattern_desc="Test pattern",
        description="Test agent",
        agent_id=agent_id,
        status="pending"
    )
    
    # Verify initial state
    initial = db.agent_definition_get(agent_id)
    assert initial is not None
    assert initial.get("status") == "pending"
    
    # Activate the agent
    result = activate_agent_locally(db, agent_id)
    
    # Verify activation succeeded
    assert result is True
    
    # Verify status changed to active
    activated = db.agent_definition_get(agent_id)
    assert activated is not None
    assert activated.get("status") == "active"
    assert activated.get("promotion_state") == "active"
    assert activated.get("activated_at") is not None


def test_canonical_agent_representation_shared_between_export_and_runtime(tmp_path: Path) -> None:
    db = Database(tmp_path / "agents.db")
    fingerprint = "shared-canonical-pattern"
    draft = {
        "name": "Shared Canonical Agent",
        "project_id": str(tmp_path),
        "pattern_hash": fingerprint,
        "pattern_desc": "Handle canonical learned-agent context",
        "instructions": "---\nname: \"shared-canonical-agent\"\ndescription: \"Reusable agent\"\ntools: \"Read\"\nmodel: \"sonnet\"\n---\n\n## Context\nUse canonical evidence.\n",
        "examples": [{"task": "Handle canonical learned-agent context", "outcome_summary": "completed"}],
    }
    db.save_agent_definition(
        fingerprint,
        draft["pattern_desc"],
        json.dumps(draft, sort_keys=True),
        promotion_state="active",
        match_count=1,
    )

    provider = CLIProvider(
        name="claude-code",
        binary="claude",
        display_name="Claude Code",
        tier_models={"low": "haiku", "medium": "sonnet", "high": "opus"},
        cost_rank={"low": 1, "medium": 2, "high": 3},
        supports_registration=True,
    )
    registry_instance = MagicMock()
    registry_instance.list_providers = MagicMock(return_value=[provider])
    registry_instance.get_provider_capability = MagicMock(return_value=True)

    reg_result = register_agent_to_capable_clis(db, fingerprint, registry_instance)
    payload = db.agent_definition_get(fingerprint)
    runtime_context = build_learned_agent_runtime_context(payload)

    assert reg_result["success_targets"] == ["claude-code"]
    assert (tmp_path / ".claude" / "agents" / "shared-canonical-agent.md").exists()
    assert "Pattern Hash: shared-canonical-pattern" in runtime_context
    assert "Use canonical evidence" in runtime_context


def test_approval_activation_refreshes_registry(tmp_path: Path) -> None:
    """Activation reloads the registry when one is provided."""
    db = Database(tmp_path / "test.db")

    agent_id = "test-agent-registry-refresh"
    db.agent_definition_insert(
        project_id=None,
        lane="shared",
        pattern_hash="hash-registry-refresh",
        pattern_desc="Test pattern",
        description="Test agent",
        agent_id=agent_id,
        status="pending"
    )

    registry = MagicMock()

    result = activate_agent_locally(db, agent_id, registry)

    assert result is True
    registry.load_active_agents.assert_called_once_with()


def test_approval_queue_approve_orchestrates_flow(tmp_path: Path) -> None:
    """Test that approval orchestrates approve -> activate -> register -> queue update flow."""
    db = Database(tmp_path / "test.db")
    
    # Create an agent in the database with pending status
    agent_id = "test-agent-orchestrate"
    db.agent_definition_insert(
        project_id=None,
        lane="shared",
        pattern_hash="hash_orchestrate",
        pattern_desc="Test orchestration",
        description="Test agent",
        agent_id=agent_id,
        status="pending"
    )
    
    # Verify initial state is pending
    initial = db.agent_definition_get(agent_id)
    assert initial.get("status") == "pending"
    
    # Mock provider registry with successful registration
    with patch("shared.discovery.ProviderRegistry") as MockRegistry:
        provider = MagicMock(spec=CLIProvider)
        provider.provider_id = "test-provider"
        provider.export_agent = MagicMock(return_value={"exported": True})
        
        registry_instance = MagicMock()
        registry_instance.list_providers = MagicMock(return_value=[provider])
        registry_instance.get_provider_capability = MagicMock(return_value=True)
        MockRegistry.return_value = registry_instance
        
        # Activate the agent (simulating approval flow)
        activation_result = activate_agent_locally(db, agent_id)
        assert activation_result is True
        
        # Registration attempt
        reg_result = register_agent_to_capable_clis(db, agent_id, registry_instance)
        
        # Verify full flow results
        assert activation_result is True  # Activation succeeded
        assert len(reg_result["success_targets"]) > 0  # Registration attempted
        
        # Verify final state
        final = db.agent_definition_get(agent_id)
        assert final.get("status") == "active"
        assert final.get("activated_at") is not None
