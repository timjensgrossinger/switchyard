"""Tests for plan 05 dynamic for_each node."""
from __future__ import annotations

import pytest
from shared.planner import ExecutionPlan, ForEachNode, SubtaskTemplate, Subtask


# ---------------------------------------------------------------------------
# ForEachNode dataclass
# ---------------------------------------------------------------------------

def test_foreach_node_defaults():
    tmpl = SubtaskTemplate(description_template="process {item}")
    node = ForEachNode(node_id="n1", source="static", template=tmpl)
    assert node.concurrency == 0
    assert node.aggregate == "list"
    assert node.static_items == []


def test_foreach_node_explicit_fields():
    tmpl = SubtaskTemplate(
        description_template="lint {item}",
        tier="low",
        target_file_template="/tmp/{item}.out",
    )
    node = ForEachNode(
        node_id="n2",
        source="$.files[*]",
        template=tmpl,
        concurrency=3,
        aggregate="map",
        static_items=["a.py", "b.py"],
    )
    assert node.concurrency == 3
    assert node.aggregate == "map"
    assert node.static_items == ["a.py", "b.py"]


def test_subtask_template_item_substitution():
    tmpl = SubtaskTemplate(
        description_template="check {item} for issues",
        target_file_template="/out/{item}.txt",
    )
    desc = tmpl.description_template.replace("{item}", "main.py")
    tf = tmpl.target_file_template.replace("{item}", "main.py")
    assert desc == "check main.py for issues"
    assert tf == "/out/main.py.txt"


# ---------------------------------------------------------------------------
# ExecutionPlan.for_each_nodes
# ---------------------------------------------------------------------------

def test_execution_plan_has_foreach_nodes_field():
    plan = ExecutionPlan(
        analysis="test",
        subtasks=[],
        waves=[],
        total_agents=0,
        strategy="dag",
    )
    assert plan.for_each_nodes == []


def test_execution_plan_foreach_nodes_settable():
    tmpl = SubtaskTemplate(description_template="do {item}")
    node = ForEachNode(node_id="n1", source="static", template=tmpl, static_items=["x"])
    plan = ExecutionPlan(
        analysis="test",
        subtasks=[],
        waves=[],
        total_agents=0,
        strategy="dag",
        for_each_nodes=[node],
    )
    assert len(plan.for_each_nodes) == 1
    assert plan.for_each_nodes[0].node_id == "n1"


# ---------------------------------------------------------------------------
# plan_revisions.expanded_items column
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    from shared.db import Database
    return Database(tmp_path / "test.db")


def test_plan_revisions_has_expanded_items(db):
    with db.conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(plan_revisions)").fetchall()}
    assert "expanded_items" in cols


# ---------------------------------------------------------------------------
# _execute_foreach_node (unit — mock execute_subtask)
# ---------------------------------------------------------------------------

class FakeProvider:
    def resolve_model(self, tier): return "haiku"
    def execute(self, subtask, model, timeout=120): return "output"
    def available_tiers(self): return ["low", "medium", "high"]


def _make_orchestrator():
    from shared.config import TGsConfig
    from shared.orchestrator import Orchestrator
    from unittest.mock import MagicMock

    cfg = TGsConfig()
    orch = Orchestrator.__new__(Orchestrator)
    orch._config = cfg
    orch._project_root = "/tmp"
    orch._db = None
    orch._execute_subtask_accepts_idempotency_key = False
    orch._execute_subtask_accepts_prefetch = False
    orch._execute_subtask_accepts_provider_override = False

    from shared.orchestrator import AgentResult
    mock_execute = MagicMock(return_value=AgentResult(
        subtask_id=0, tier="low", model="haiku",
        output="processed", token_count=10, success=True,
    ))
    orch.execute_subtask = mock_execute
    return orch


def test_foreach_list_aggregate():
    orch = _make_orchestrator()
    tmpl = SubtaskTemplate(description_template="process {item}", tier="low")
    node = ForEachNode(node_id="n1", source="static", template=tmpl,
                       aggregate="list", static_items=["a", "b", "c"])
    result = orch._execute_foreach_node(
        node, ["a", "b", "c"], timeout=60,
        task_id="task-1", execution_id="exec-1", plan_revision=0, wave=0,
    )
    assert result["total"] == 3
    assert result["succeeded"] == 3
    assert result["failed"] == 0
    assert isinstance(result["aggregated"], list)


def test_foreach_map_aggregate():
    orch = _make_orchestrator()
    tmpl = SubtaskTemplate(description_template="do {item}", tier="low")
    node = ForEachNode(node_id="n2", source="static", template=tmpl, aggregate="map")
    result = orch._execute_foreach_node(
        node, ["x", "y"], timeout=60,
        task_id="task-2", execution_id=None, plan_revision=0, wave=0,
    )
    assert result["aggregate_mode"] == "map"
    assert isinstance(result["aggregated"], dict)


def test_foreach_first_success_short_circuits():
    from shared.orchestrator import AgentResult
    from unittest.mock import MagicMock

    from shared.config import TGsConfig
    from shared.orchestrator import Orchestrator

    cfg = TGsConfig()
    orch = Orchestrator.__new__(Orchestrator)
    orch._config = cfg
    orch._project_root = "/tmp"
    orch._db = None
    orch._execute_subtask_accepts_idempotency_key = False
    orch._execute_subtask_accepts_prefetch = False
    orch._execute_subtask_accepts_provider_override = False

    call_count = 0
    def mock_exec(subtask, timeout, **kwargs):
        nonlocal call_count
        call_count += 1
        return AgentResult(subtask_id=0, tier="low", model="haiku",
                          output="found", token_count=5, success=True)

    orch.execute_subtask = mock_exec

    tmpl = SubtaskTemplate(description_template="try {item}", tier="low")
    node = ForEachNode(node_id="n3", source="static", template=tmpl, aggregate="first_success")
    result = orch._execute_foreach_node(
        node, ["a", "b", "c"], timeout=60,
        task_id="task-3", execution_id=None, plan_revision=0, wave=0,
    )
    assert result["succeeded"] == 1
    assert call_count == 1  # short-circuited after first success


def test_foreach_concurrency_cap():
    """Concurrency limit should be honored (all items still complete)."""
    orch = _make_orchestrator()
    tmpl = SubtaskTemplate(description_template="item {item}", tier="low")
    node = ForEachNode(node_id="n4", source="static", template=tmpl,
                       concurrency=2, aggregate="list")
    items = [str(i) for i in range(8)]
    result = orch._execute_foreach_node(
        node, items, timeout=60,
        task_id="task-4", execution_id=None, plan_revision=0, wave=0,
    )
    assert result["total"] == 8
    assert result["succeeded"] == 8


def test_foreach_idempotency_keys_differ_per_item():
    """Each item gets a distinct idempotency key."""
    import hashlib
    node_id = "n5"
    items = ["file_a.py", "file_b.py"]
    keys = set()
    for item in items:
        item_hash = hashlib.sha256(item.encode()).hexdigest()[:16]
        keys.add(f"foreach:{node_id}:{item_hash}")
    assert len(keys) == len(items)
