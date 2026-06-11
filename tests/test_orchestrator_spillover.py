#!/usr/bin/env python3
"""
Targeted regressions for spillover anchoring in orchestrator/discovery.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from shared.config import TGsConfig
from shared.db import Database
from shared.orchestrator import Orchestrator, Provider
from shared.planner import Subtask
from shared.discovery import ProviderRegistry


class DummyProvider(Provider):
    def resolve_model(self, tier: str) -> str:
        return f"dummy-{tier}"

    def execute(self, subtask: Subtask, model: str, timeout: int = 120) -> str | None:
        return f"{model}:{subtask.id}"

    def available_tiers(self) -> list[str]:
        return ["low", "medium", "high"]

    def provider_info(self) -> dict:
        return {"primary": "dummy-provider"}


class AnchoringOrchestrator(Orchestrator):
    def __init__(self, config: TGsConfig, db: Database) -> None:
        super().__init__(config, DummyProvider(), None, db=db)

    def execute_subtask(self, subtask: Subtask, timeout: int = 120, provider_override: Provider | None = None, **kwargs) -> object:
        # Return a minimal AgentResult-like object with provider metadata so tests can inspect assignment
        class R:
            def __init__(self, subtask_id, provider_name):
                self.subtask_id = subtask_id
                self.tier = subtask.tier
                self.model = subtask.model
                self.output = ""
                self.token_count = 1
                self.provider_name = provider_name
                # Fields expected by orchestrator post-processing
                self.used_fallback = False
                self.used_speculation = False
                self.escalated = False
                self.success = True

        # Provider override will be passed as provider_override kwarg in orchestrator
        provider_name = provider_override.provider_info().get("primary") if provider_override else "none"
        return R(subtask.id, provider_name)


@pytest.fixture
def db(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    yield db
    db.close()


def _make_subtasks_with_provider_ids():
    # Build subtasks: two routed to provider 'a', two routed to provider 'b'
    return [
        Subtask(id=1, description="s1", tier="low", model="m", provider_id="a"),
        Subtask(id=2, description="s2", tier="low", model="m", provider_id="a"),
        Subtask(id=3, description="s3", tier="low", model="m", provider_id="b"),
        Subtask(id=4, description="s4", tier="low", model="m", provider_id="b"),
    ]


def test_explicit_provider_id_anchors_primary_even_if_cheaper(tmp_path: Path, db):
    config = TGsConfig()
    orchestrator = AnchoringOrchestrator(config, db)

    # Patch provider registry to return a plan where 'b' is cheaper but 'a' is explicitly routed
    with patch.object(orchestrator, "_provider_registry") as mock_registry:
        orchestrator._providers_map = {"a": MagicMock(provider_info=MagicMock(return_value={"primary":"a"})),
                                       "b": MagicMock(provider_info=MagicMock(return_value={"primary":"b"}))}
        # plan_spillover_allocation should be called with anchor_provider_id="a" for group (low,'a')
        def fake_plan(tier, count, anchor_provider_id=None, **kwargs):
            if anchor_provider_id == "a":
                return {"primary": {"provider_id": "a"}, "assignments": [{"provider_id":"a","slots":2},{"provider_id":"b","slots":2}], "remaining": 0}
            return {"primary": {"provider_id": "b"}, "assignments": [{"provider_id":"b","slots":4}], "remaining": 0}

        mock_registry.plan_spillover_allocation.side_effect = fake_plan

        results = orchestrator.execute_wave(0, _make_subtasks_with_provider_ids())

        # Assert that subtasks 1 and 2 were assigned to provider 'a'
        assigned = {r.subtask_id: getattr(r, 'provider_name') for r in results}
        assert assigned[1] == 'a'
        assert assigned[2] == 'a'


def test_same_tier_different_routed_providers_not_mixed(tmp_path: Path, db):
    config = TGsConfig()
    orchestrator = AnchoringOrchestrator(config, db)

    with patch.object(orchestrator, "_provider_registry") as mock_registry:
        orchestrator._providers_map = {"a": MagicMock(provider_info=MagicMock(return_value={"primary":"a"})),
                                       "b": MagicMock(provider_info=MagicMock(return_value={"primary":"b"}))}

        def fake_plan(tier, count, anchor_provider_id=None, **kwargs):
            # Ensure planner is invoked separately per routed group
            if anchor_provider_id == "a":
                return {"primary": {"provider_id": "a"}, "assignments": [{"provider_id":"a","slots":2}], "remaining": 0}
            if anchor_provider_id == "b":
                return {"primary": {"provider_id": "b"}, "assignments": [{"provider_id":"b","slots":2}], "remaining": 0}
            return {"primary": None, "assignments": [], "remaining": count}

        mock_registry.plan_spillover_allocation.side_effect = fake_plan

        results = orchestrator.execute_wave(0, _make_subtasks_with_provider_ids())
        assigned = {r.subtask_id: getattr(r, 'provider_name') for r in results}
        assert assigned[1] == 'a' and assigned[3] == 'b'


def test_missing_explicit_primary_fails_clearly(tmp_path: Path, db):
    config = TGsConfig()
    orchestrator = AnchoringOrchestrator(config, db)

    with patch.object(orchestrator, "_provider_registry") as mock_registry:
        orchestrator._providers_map = {"b": MagicMock(provider_info=MagicMock(return_value={"primary":"b"}))}

        def fake_plan(tier, count, anchor_provider_id=None, **kwargs):
            raise RuntimeError(f"Explicitly routed provider '{anchor_provider_id}' is not routeable/available for tier '{tier}'")

        mock_registry.plan_spillover_allocation.side_effect = fake_plan

        with pytest.raises(RuntimeError):
            orchestrator.execute_wave(0, _make_subtasks_with_provider_ids())


def test_missing_runtime_provider_mapping_fails_clearly(tmp_path: Path, db):
    config = TGsConfig()
    orchestrator = AnchoringOrchestrator(config, db)

    with patch.object(orchestrator, "_provider_registry") as mock_registry:
        orchestrator._providers_map = {
            "a": MagicMock(provider_info=MagicMock(return_value={"primary": "a"}))
        }

        def fake_plan(tier, count, anchor_provider_id=None, **kwargs):
            return {
                "primary": {"provider_id": "missing-provider"},
                "assignments": [{"provider_id": "missing-provider", "slots": count}],
                "remaining": 0,
            }

        mock_registry.plan_spillover_allocation.side_effect = fake_plan

        with pytest.raises(RuntimeError, match="missing-provider"):
            orchestrator.execute_wave(0, [Subtask(id=1, description="s1", tier="low", model="m")])


def test_runtime_provider_mapping_uses_normalized_assignment_ids(tmp_path: Path, db):
    config = TGsConfig()
    orchestrator = AnchoringOrchestrator(config, db)

    with patch.object(orchestrator, "_provider_registry") as mock_registry:
        orchestrator._providers_map = {
            "github-copilot": MagicMock(
                provider_info=MagicMock(return_value={"primary": "github-copilot"})
            )
        }

        def fake_plan(tier, count, anchor_provider_id=None, **kwargs):
            return {
                "primary": {"provider_id": "GitHub_Copilot"},
                "assignments": [{"provider_id": "GitHub_Copilot", "slots": count}],
                "remaining": 0,
            }

        mock_registry.plan_spillover_allocation.side_effect = fake_plan

        results = orchestrator.execute_wave(
            0,
            [Subtask(id=1, description="s1", tier="low", model="m")],
        )

        assert results[0].provider_name == "github-copilot"


def test_string_zero_remaining_does_not_raise(tmp_path: Path, db):
    config = TGsConfig()
    orchestrator = AnchoringOrchestrator(config, db)

    with patch.object(orchestrator, "_provider_registry") as mock_registry:
        orchestrator._providers_map = {
            "a": MagicMock(provider_info=MagicMock(return_value={"primary": "a"}))
        }

        def fake_plan(tier, count, anchor_provider_id=None, **kwargs):
            return {
                "primary": {"provider_id": "a"},
                "assignments": [{"provider_id": "a", "slots": count}],
                "remaining": "0",
            }

        mock_registry.plan_spillover_allocation.side_effect = fake_plan

        results = orchestrator.execute_wave(
            0,
            [Subtask(id=1, description="s1", tier="low", model="m")],
        )

        assert results[0].provider_name == "a"


def test_invalid_assignment_shape_fails_clearly(tmp_path: Path, db):
    config = TGsConfig()
    orchestrator = AnchoringOrchestrator(config, db)

    with patch.object(orchestrator, "_provider_registry") as mock_registry:
        orchestrator._providers_map = {
            "a": MagicMock(provider_info=MagicMock(return_value={"primary": "a"}))
        }

        def fake_plan(tier, count, anchor_provider_id=None, **kwargs):
            return {
                "primary": {"provider_id": "a"},
                "assignments": ["not-a-mapping"],
                "remaining": 0,
            }

        mock_registry.plan_spillover_allocation.side_effect = fake_plan

        with pytest.raises(RuntimeError, match="invalid assignment"):
            orchestrator.execute_wave(
                0,
                [Subtask(id=1, description="s1", tier="low", model="m")],
            )
