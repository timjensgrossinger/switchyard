#!/usr/bin/env python3
"""
Tests for shared/planner.py — task decomposition and plan caching.
"""
from __future__ import annotations

import json
import sys
from typing import Any, cast
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import RoutingPreference, TGsConfig
from shared.db import Database
from shared.planner import (
    BudgetExceededError,
    CLIBackend,
    ExecutionPlan,
    FanOutConfig,
    FanOutDecision,
    GhCopilotBackend,
    PLAN_END,
    PLAN_START,
    Planner,
    PlannerParseError,
    Subtask,
    TIER_ALIASES,
    _extract_json,
    build_waves,
    evaluate_fanout,
    match_template,
    validate_plan,
    validate_topology,
)


class MockBackend(CLIBackend):
    """Mock backend that returns pre-set responses."""

    def __init__(self, response: str | None = None) -> None:
        self._response = response
        self.prompts: list[str] = []

    def call(self, prompt: str, model: str | None = None, timeout: int = 120) -> str | None:
        self.prompts.append(prompt)
        return self._response


def test_gh_copilot_backend_uses_disable_flag_when_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = GhCopilotBackend()
    backend._model_flag = True
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    def _fake_run(cmd: list[str], **kwargs: object) -> _Result:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr("shared.planner.subprocess.run", _fake_run)
    monkeypatch.setattr(
        "shared.discovery._copilot_supports_model_flag",
        lambda: True,
    )
    monkeypatch.setattr(
        "shared.discovery._copilot_supports_disable_builtin_mcps",
        lambda: True,
    )

    assert backend.call("hello", model="gpt-5-mini", timeout=7) == "ok"
    assert captured["cmd"] == [
        "gh",
        "copilot",
        "--",
        "-p",
        "hello",
        "--model",
        "gpt-5-mini",
        "--disable-builtin-mcps",
    ]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["timeout"] == 7
    assert kwargs["cwd"].endswith("copilot-sandbox")
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert env["COPILOT_HOME"].endswith("copilot-sandbox")


def test_gh_copilot_backend_handles_sandbox_setup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = GhCopilotBackend()
    backend._model_flag = True
    monkeypatch.setattr(
        "shared.planner._copilot_subprocess_env",
        lambda: (_ for _ in ()).throw(OSError("boom")),
    )

    assert backend.call("hello", model="gpt-5-mini", timeout=7) is None


def test_gh_copilot_backend_does_not_retry_without_env_on_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = GhCopilotBackend()
    backend._model_flag = True
    calls: list[dict[str, object]] = []

    class _Result:
        def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(cmd: list[str], **kwargs: object) -> _Result:
        calls.append(dict(kwargs))
        if len(calls) == 1:
            return _Result(1, "", "Authentication required")
        return _Result(0, "ok\n", "")

    monkeypatch.setattr("shared.planner.subprocess.run", _fake_run)
    monkeypatch.setattr("shared.discovery._copilot_supports_disable_builtin_mcps", lambda: True)

    assert backend.call("hello", model="gpt-5-mini", timeout=7) is None
    assert len(calls) == 1
    assert "env" in calls[0]


def test_gh_copilot_backend_handles_probe_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = GhCopilotBackend()
    backend._model_flag = None
    monkeypatch.setattr(
        "shared.planner._copilot_supports_model_flag",
        lambda: (_ for _ in ()).throw(RuntimeError("probe failed")),
    )

    assert backend.call("hello", model="gpt-5-mini", timeout=7) is None


def _wrap_plan(payload: dict) -> str:
    return f"{PLAN_START}\n{json.dumps(payload)}\n{PLAN_END}"


def _build_execution_plan(
    subtask_count: int,
    *,
    estimated_agent_tokens: int = 0,
) -> ExecutionPlan:
    subtasks = [
        Subtask(id=i, description=f"task {i}", tier="medium", depends_on=[])
        for i in range(1, subtask_count + 1)
    ]
    waves = [list(range(1, subtask_count + 1))] if subtasks else []
    return ExecutionPlan(
        analysis="fanout-test",
        subtasks=subtasks,
        waves=waves,
        total_agents=subtask_count,
        strategy="parallel",
        estimated_agent_tokens=estimated_agent_tokens,
    )


def test_build_waves_simple() -> None:
    """Independent subtasks should all be in wave 1."""
    subtasks = [
        Subtask(id=1, description="a", tier="low"),
        Subtask(id=2, description="b", tier="low"),
        Subtask(id=3, description="c", tier="low"),
    ]
    waves = build_waves(subtasks)
    assert len(waves) == 1
    assert set(waves[0]) == {1, 2, 3}


def test_build_waves_with_deps() -> None:
    """Dependencies should create multiple waves."""
    subtasks = [
        Subtask(id=1, description="a", tier="low"),
        Subtask(id=2, description="b", tier="medium", depends_on=[1]),
        Subtask(id=3, description="c", tier="low"),
    ]
    waves = build_waves(subtasks)
    assert len(waves) == 2
    assert 1 in waves[0] and 3 in waves[0]
    assert 2 in waves[1]


def test_build_waves_circular() -> None:
    """Circular deps should force all into one wave."""
    subtasks = [
        Subtask(id=1, description="a", tier="low", depends_on=[2]),
        Subtask(id=2, description="b", tier="low", depends_on=[1]),
    ]
    waves = build_waves(subtasks)
    assert len(waves) >= 1


def test_build_waves_ignores_unknown_dependencies() -> None:
    """Unknown dependency IDs should not force circular fallback behavior."""
    subtasks = [
        Subtask(id=1, description="a", tier="low", depends_on=[99]),
        Subtask(id=2, description="b", tier="low"),
    ]
    waves = build_waves(subtasks)
    assert len(waves) == 1
    assert set(waves[0]) == {1, 2}


def test_validate_topology_matches_linear() -> None:
    plan = ExecutionPlan(
        analysis="linear",
        subtasks=[
            Subtask(id=1, description="one", tier="low"),
            Subtask(id=2, description="two", tier="low", depends_on=[1]),
            Subtask(id=3, description="three", tier="low", depends_on=[2]),
        ],
        waves=[[1], [2], [3]],
        total_agents=3,
        strategy="dag",
        topology="linear",
        _topology_explicit=True,
    )
    valid, issues, fallback = validate_topology(plan)
    assert valid is True
    assert issues == []
    assert fallback is None


def test_validate_topology_mismatch_reports_issue_and_fallback() -> None:
    plan = ExecutionPlan(
        analysis="mismatch",
        subtasks=[
            Subtask(id=1, description="one", tier="low"),
            Subtask(id=2, description="two", tier="low", depends_on=[1]),
            Subtask(id=3, description="three", tier="low", depends_on=[1, 2]),
        ],
        waves=[[1], [2], [3]],
        total_agents=3,
        strategy="dag",
        topology="star",
        _topology_explicit=True,
    )
    valid, issues, fallback = validate_topology(plan)
    assert valid is False
    assert fallback == "linear"
    assert any("star" in issue for issue in issues)


def test_validate_topology_accepts_dag_when_acyclic() -> None:
    plan = ExecutionPlan(
        analysis="dag",
        subtasks=[
            Subtask(id=1, description="one", tier="low"),
            Subtask(id=2, description="two", tier="low", depends_on=[1]),
            Subtask(id=3, description="three", tier="low", depends_on=[1]),
            Subtask(id=4, description="four", tier="low", depends_on=[2, 3]),
        ],
        waves=[[1], [2, 3], [4]],
        total_agents=4,
        strategy="dag",
        topology="dag",
        _topology_explicit=True,
    )
    valid, issues, fallback = validate_topology(plan)
    assert valid is True
    assert issues == []
    assert fallback is None


def test_extract_json_plain() -> None:
    """Extract JSON from plain text."""
    raw = '{"key": "value"}'
    result = _extract_json(raw)
    assert result == {"key": "value"}


def test_extract_json_markdown() -> None:
    """Extract JSON from markdown fence."""
    raw = '```json\n{"key": "value"}\n```'
    result = _extract_json(raw)
    assert result == {"key": "value"}


def test_extract_json_with_preamble() -> None:
    """Extract JSON with surrounding text."""
    raw = 'Here is the plan:\n{"subtasks": [{"id": 1}]}\nDone.'
    result = _extract_json(raw)
    assert result is not None
    assert "subtasks" in result


def test_match_template_error_handling() -> None:
    """Should match 'add error handling' template."""
    tmpl = match_template("add error handling to auth module")
    assert tmpl is not None
    assert tmpl.tier == "low"


def test_match_template_type_hints() -> None:
    """Should match 'add type hints' template."""
    tmpl = match_template("add type hints to all functions")
    assert tmpl is not None
    assert tmpl.tier == "low"


def test_match_template_tests() -> None:
    """Should match 'write tests for' template."""
    tmpl = match_template("write unit tests for the database module")
    assert tmpl is not None
    assert tmpl.tier == "low"


def test_match_template_no_match() -> None:
    """Should return None for non-matching description."""
    tmpl = match_template("architect a new microservice framework")
    assert tmpl is None


def test_tier_aliases() -> None:
    """Legacy tier names should map correctly."""
    assert TIER_ALIASES["mini"] == "low"
    assert TIER_ALIASES["sonnet"] == "medium"
    assert TIER_ALIASES["opus"] == "high"


def test_planner_fallback_no_output() -> None:
    """No backend output should raise PlannerParseError."""
    planner = Planner(TGsConfig(), MockBackend(None))
    with pytest.raises(PlannerParseError):
        planner.plan("test task")


def test_planner_with_valid_json() -> None:
    """Valid delimited JSON response should be parsed into a plan."""
    response = _wrap_plan({
        "analysis": "Test",
        "subtasks": [{"id": 1, "description": "do stuff", "tier": "low", "depends_on": []}],
        "strategy": "parallel",
    })
    planner = Planner(TGsConfig(), MockBackend(response))
    plan = planner.plan("test task")
    assert plan.total_agents == 1
    assert plan.subtasks[0].tier == "low"
    assert plan.subtasks[0].model == "low"


def test_planner_prompt_explains_runtime_file_materialization() -> None:
    response = _wrap_plan({
        "analysis": "Test",
        "subtasks": [{
            "id": 1,
            "description": "return app.py",
            "tier": "low",
            "target_file": "app.py",
            "depends_on": [],
        }],
        "strategy": "parallel",
    })
    backend = MockBackend(response)
    Planner(TGsConfig(), backend).plan("create app.py")

    assert "return only the complete" in backend.prompts[0]
    assert "runtime, not the agent CLI, writes it" in backend.prompts[0]


def test_planner_legacy_tier_mapping() -> None:
    """Planner should map legacy 'mini'/'sonnet'/'opus' to new tier names."""
    response = _wrap_plan({
        "analysis": "Test",
        "subtasks": [
            {"id": 1, "description": "simple thing", "tier": "mini", "depends_on": []},
            {"id": 2, "description": "complex thing", "tier": "opus", "depends_on": []},
        ],
        "strategy": "parallel",
    })
    planner = Planner(TGsConfig(), MockBackend(response))
    plan = planner.plan("test")
    assert plan.subtasks[0].tier == "low"
    assert plan.subtasks[1].tier == "high"


def test_planner_preserves_forward_dependencies() -> None:
    """Dependencies on later-defined subtasks should be preserved."""
    response = _wrap_plan({
        "analysis": "Test",
        "subtasks": [
            {"id": 1, "description": "wait for 2", "tier": "low", "depends_on": [2]},
            {"id": 2, "description": "run first", "tier": "low", "depends_on": []},
        ],
        "strategy": "dag",
    })
    planner = Planner(TGsConfig(), MockBackend(response))
    plan = planner.plan("forward dependency")
    assert plan.subtasks[0].depends_on == [2]
    assert plan.waves == [[2], [1]]


def test_planner_invalid_token_budget_is_ignored() -> None:
    """Non-numeric token_budget values should not survive into the plan."""
    response = _wrap_plan({
        "analysis": "Test",
        "subtasks": [
            {"id": 1, "description": "do stuff", "tier": "low", "depends_on": []},
        ],
        "strategy": "parallel",
        "token_budget": "not-a-number",
    })
    planner = Planner(TGsConfig(), MockBackend(response))
    plan = planner.plan("bad token budget")
    assert plan.token_budget is None


def test_planner_non_integer_subtask_id_falls_back() -> None:
    """Non-integer subtask IDs should be coerced to a safe fallback integer."""
    response = _wrap_plan({
        "analysis": "Test",
        "subtasks": [
            {"id": "bad-id", "description": "do stuff", "tier": "low", "depends_on": []},
        ],
        "strategy": "parallel",
    })
    planner = Planner(TGsConfig(), MockBackend(response))
    plan = planner.plan("bad subtask id")
    assert plan.subtasks[0].id == 1


def test_delimiter_escape_case() -> None:
    """Literal delimiter text inside a JSON string should not break parsing."""
    response = _wrap_plan({
        "analysis": f"Test with literal {PLAN_END} inside",
        "subtasks": [
            {
                "id": 1,
                "description": f"handle literal {PLAN_START} and {PLAN_END}",
                "tier": "low",
                "depends_on": [],
            },
        ],
        "strategy": "parallel",
    })
    planner = Planner(TGsConfig(), MockBackend(response))
    plan = planner.plan("delimiter escape")
    assert PLAN_END in plan.analysis
    assert PLAN_START in plan.subtasks[0].description


def test_planner_preserves_explicit_route_metadata() -> None:
    response = _wrap_plan({
        "analysis": "Test",
        "subtasks": [
            {
                "id": 1,
                "description": "do stuff",
                "tier": "low",
                "model": "claude-haiku-4.5",
                "provider": "Claude Code",
                "provider_id": "claude-code",
                "depends_on": [],
            }
        ],
        "strategy": "parallel",
    })
    planner = Planner(TGsConfig(), MockBackend(response))
    plan = planner.plan("route metadata")

    assert plan.subtasks[0].model == "claude-haiku-4.5"
    assert plan.subtasks[0].provider == "Claude Code"
    assert plan.subtasks[0].provider_id == "claude-code"
    assert planner.plan_to_dict(plan)["subtasks"][0]["provider_id"] == "claude-code"


def test_validate_plan_rejects_blank_model_metadata() -> None:
    plan = ExecutionPlan(
        analysis="invalid",
        subtasks=[Subtask(id=1, description="missing model", tier="low", model="")],
        waves=[[1]],
        total_agents=1,
        strategy="parallel",
    )

    with pytest.raises(ValueError, match="1"):
        validate_plan(plan)


def test_planner_does_not_stitch_route_preferences() -> None:
    response = _wrap_plan({
        "analysis": "Test",
        "subtasks": [{"id": 1, "description": "do stuff", "tier": "low", "depends_on": []}],
        "strategy": "parallel",
    })
    cfg = TGsConfig(
        preferred_routing={
            "low": [
                RoutingPreference(provider="Claude Code"),
                RoutingPreference(model="gpt-5-mini"),
            ]
        }
    )
    planner = Planner(cfg, MockBackend(response))
    plan = planner.plan("route metadata")

    assert plan.subtasks[0].provider == "Claude Code"
    assert plan.subtasks[0].provider_id is None
    assert plan.subtasks[0].model == "low"


def test_planner_explicit_model_prevents_template_tier_override() -> None:
    response = _wrap_plan({
        "analysis": "Test",
        "subtasks": [
            {
                "id": 1,
                "description": "write unit tests for the database module",
                "tier": "high",
                "model": "claude-sonnet-4.6",
                "depends_on": [],
            }
        ],
        "strategy": "parallel",
    })
    planner = Planner(TGsConfig(), MockBackend(response))
    plan = planner.plan("route metadata")

    assert plan.subtasks[0].tier == "high"
    assert plan.subtasks[0].model == "claude-sonnet-4.6"


def test_planner_explicit_provider_prevents_template_tier_override() -> None:
    response = _wrap_plan({
        "analysis": "Test",
        "subtasks": [
            {
                "id": 1,
                "description": "write unit tests for the database module",
                "tier": "high",
                "provider": "Claude Code",
                "depends_on": [],
            }
        ],
        "strategy": "parallel",
    })
    planner = Planner(TGsConfig(), MockBackend(response))
    plan = planner.plan("route metadata")

    assert plan.subtasks[0].tier == "high"
    assert plan.subtasks[0].provider == "Claude Code"
    assert plan.subtasks[0].model == "high"


def test_planner_skips_blank_route_preferences() -> None:
    response = _wrap_plan({
        "analysis": "Test",
        "subtasks": [{"id": 1, "description": "do stuff", "tier": "low", "depends_on": []}],
        "strategy": "parallel",
    })
    cfg = TGsConfig(
        preferred_routing={
            "low": [
                RoutingPreference(),
                RoutingPreference(model="gpt-5-mini"),
            ]
        }
    )
    planner = Planner(cfg, MockBackend(response))
    plan = planner.plan("route metadata")

    assert plan.subtasks[0].model == "gpt-5-mini"


def test_planner_skips_malformed_route_preferences() -> None:
    response = _wrap_plan({
        "analysis": "Test",
        "subtasks": [{"id": 1, "description": "do stuff", "tier": "low", "depends_on": []}],
        "strategy": "parallel",
    })
    cfg = TGsConfig()
    cfg.preferred_routing = {"low": [cast(Any, "bad-entry"), RoutingPreference(model="gpt-5-mini")]}
    planner = Planner(cfg, MockBackend(response))
    plan = planner.plan("route metadata")

    assert plan.subtasks[0].model == "gpt-5-mini"


def test_evaluate_fanout_disabled_by_default() -> None:
    decision = evaluate_fanout(_build_execution_plan(3, estimated_agent_tokens=120))

    assert isinstance(decision, FanOutDecision)
    assert decision.enabled is False
    assert decision.reason == "disabled"


def test_evaluate_fanout_single_route_when_only_one_subtask() -> None:
    decision = evaluate_fanout(
        _build_execution_plan(1, estimated_agent_tokens=50),
        FanOutConfig(opt_in_fanout=True),
    )

    assert decision.enabled is False
    assert decision.reason == "single_route"


def test_evaluate_fanout_raises_budget_exceeded() -> None:
    plan = _build_execution_plan(3, estimated_agent_tokens=500)

    with pytest.raises(BudgetExceededError):
        evaluate_fanout(
            plan,
            FanOutConfig(opt_in_fanout=True, budget_limit=100),
        )


def test_evaluate_fanout_enables_fanout_and_caps_router_count() -> None:
    decision = evaluate_fanout(
        _build_execution_plan(4, estimated_agent_tokens=120),
        FanOutConfig(opt_in_fanout=True, max_routers=2, budget_limit=1000),
    )

    assert decision.enabled is True
    assert decision.router_count == 2
    assert decision.subtask_ids == [1, 2]


def test_evaluate_fanout_logs_telemetry_when_db_provided() -> None:
    with TemporaryDirectory() as td:
        db = Database(Path(td) / "test.db")
        decision = evaluate_fanout(
            _build_execution_plan(3, estimated_agent_tokens=120),
            FanOutConfig(opt_in_fanout=True, max_routers=2, budget_limit=1000),
            db=db,
        )

        with db.conn() as conn:
            row = conn.execute(
                "SELECT reason, tokens_used FROM telemetry ORDER BY id DESC LIMIT 1"
            ).fetchone()

        assert decision.reason == "fanout"
        assert row is not None
        assert row[0] == "fanout"
        assert row[1] == 120
        db.close()


if __name__ == "__main__":
    tests = [
        test_build_waves_simple,
        test_build_waves_with_deps,
        test_build_waves_circular,
        test_build_waves_ignores_unknown_dependencies,
        test_extract_json_plain,
        test_extract_json_markdown,
        test_extract_json_with_preamble,
        test_match_template_error_handling,
        test_match_template_type_hints,
        test_match_template_tests,
        test_match_template_no_match,
        test_tier_aliases,
        test_planner_fallback_no_output,
        test_planner_with_valid_json,
        test_planner_legacy_tier_mapping,
        test_planner_preserves_forward_dependencies,
        test_planner_invalid_token_budget_is_ignored,
        test_planner_non_integer_subtask_id_falls_back,
        test_delimiter_escape_case,
        test_evaluate_fanout_disabled_by_default,
        test_evaluate_fanout_single_route_when_only_one_subtask,
        test_evaluate_fanout_raises_budget_exceeded,
        test_evaluate_fanout_enables_fanout_and_caps_router_count,
        test_evaluate_fanout_logs_telemetry_when_db_provided,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  ✅ {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ {test.__name__}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
