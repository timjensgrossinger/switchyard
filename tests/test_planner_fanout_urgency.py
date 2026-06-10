import pytest

from shared.planner import (
    evaluate_fanout,
    FanOutConfig,
    ExecutionPlan,
    Subtask,
)


def _make_plan(num_subtasks=3, estimated_tokens=10000, strategy="parallel"):
    subtasks = [
        Subtask(id=i + 1, description=f"task {i+1}", tier="low", depends_on=[])
        for i in range(num_subtasks)
    ]
    plan = ExecutionPlan(
        analysis="test",
        subtasks=subtasks,
        waves=[list(range(1, num_subtasks + 1))],
        total_agents=num_subtasks,
        strategy=strategy,
        topology="linear",
        token_budget=None,
        planner_tokens=None,
        estimated_agent_tokens=estimated_tokens,
    )
    return plan


def test_urgency_lowers_threshold():
    """High urgency should conservatively lower router_count compared to default."""
    config = FanOutConfig(opt_in_fanout=True, max_routers=3)
    plan = _make_plan(num_subtasks=3, estimated_tokens=10_000)

    base = evaluate_fanout(plan, config)
    urgent = evaluate_fanout(plan, config, urgency_score=0.8)

    assert base.enabled is True
    assert urgent.enabled is True
    assert urgent.router_count < base.router_count


def test_urgency_prefers_star():
    """For a fan-out-friendly plan, high urgency should prefer star topology."""
    config = FanOutConfig(opt_in_fanout=True, max_routers=4)
    plan = _make_plan(num_subtasks=4, estimated_tokens=20_000, strategy="parallel")

    dec = evaluate_fanout(plan, config, urgency_score=0.9)

    # Either topology_hint explicitly set to 'star' or the bias reason mentions star
    assert dec.enabled is True
    assert dec.router_count <= config.max_routers
    assert dec.topology_hint == "star" or (dec.topology_bias_reason and "star" in dec.topology_bias_reason)
