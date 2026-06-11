#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.heuristic_plan import build_heuristic_plan_payload, extract_task_file_entries


CALCULATOR_TASK = (
    "Build a calculator app: (1) models.py with Operation dataclass, "
    "(2) ops.py with add/sub/mul/div, (3) main.py CLI entrypoint"
)


def test_extract_task_file_entries_numbered_calculator_files() -> None:
    entries = extract_task_file_entries(CALCULATOR_TASK)
    paths = [path for path, _ in entries]
    assert paths == ["models.py", "ops.py", "main.py"]
    assert entries[0][1].startswith("Create models.py:")
    assert "Operation dataclass" in entries[0][1]


def test_build_heuristic_plan_payload_calculator_three_file_case() -> None:
    payload = build_heuristic_plan_payload(CALCULATOR_TASK, default_tier="medium")
    subtasks = payload["subtasks"]
    assert len(subtasks) == 3
    assert [st["target_file"] for st in subtasks] == ["models.py", "ops.py", "main.py"]
    assert payload["strategy"] == "dag"
    assert payload["topology"] == "dag"


def test_build_heuristic_plan_payload_main_py_depends_on_foundation_files() -> None:
    payload = build_heuristic_plan_payload(CALCULATOR_TASK, default_tier="medium")
    by_file = {st["target_file"]: st for st in payload["subtasks"]}
    assert by_file["main.py"]["depends_on"] == [1, 2]
    assert by_file["models.py"]["depends_on"] == []
    assert by_file["ops.py"]["depends_on"] == []


def test_build_heuristic_plan_single_file_uses_low_tier() -> None:
    payload = build_heuristic_plan_payload(
        "Create greet.py in sandbox/demo-v4",
        default_tier="medium",
    )
    assert len(payload["subtasks"]) == 1
    assert payload["subtasks"][0]["tier"] == "low"


def test_extract_task_file_entries_expands_numbered_fanout() -> None:
    task = "Create 4 greet.py numbered in sandbox/swarm-demo-v5 that prints Hello, world!"
    entries = extract_task_file_entries(task)
    paths = [path for path, _ in entries]
    assert paths == [
        "sandbox/swarm-demo-v5/greet1.py",
        "sandbox/swarm-demo-v5/greet2.py",
        "sandbox/swarm-demo-v5/greet3.py",
        "sandbox/swarm-demo-v5/greet4.py",
    ]


def test_build_heuristic_plan_numbered_fanout_parallel_wave() -> None:
    task = "Create 4 greet.py numbered in sandbox/swarm-demo-v5 that prints Hello, world!"
    payload = build_heuristic_plan_payload(task, default_tier="medium")
    assert len(payload["subtasks"]) == 4
    assert payload["topology"] == "linear"
    assert payload["strategy"] == "parallel"


CLAUDE_NEWS_TASK = (
    "Build a small web app in sandbox/claude-news-app that checks for news about Claude. "
    "Python backend with HTML, CSS, and JavaScript frontend."
)


def test_webapp_intent_without_explicit_paths_fans_out() -> None:
    payload = build_heuristic_plan_payload(CLAUDE_NEWS_TASK, default_tier="medium")
    subtasks = payload["subtasks"]
    assert len(subtasks) == 4
    paths = [st["target_file"] for st in subtasks]
    assert paths == [
        "sandbox/claude-news-app/app.py",
        "sandbox/claude-news-app/templates/index.html",
        "sandbox/claude-news-app/static/css/style.css",
        "sandbox/claude-news-app/static/js/app.js",
    ]


def test_fullstack_intent_builds_contract_first_dag() -> None:
    task = (
        "Build a fullstack todo app in sandbox/todo under openapi contract "
        "with parallel frontend and backend"
    )
    payload = build_heuristic_plan_payload(task, default_tier="medium")
    subtasks = payload["subtasks"]
    assert len(subtasks) == 4
    assert payload["topology"] == "dag"
    by_file = {st["target_file"]: st for st in subtasks}
    assert by_file["sandbox/todo/openapi.yaml"]["depends_on"] == []
    assert by_file["sandbox/todo/app.py"]["depends_on"] == [1]
    assert by_file["sandbox/todo/templates/index.html"]["depends_on"] == [1]
    assert by_file["sandbox/todo/tests/integration.py"]["depends_on"] == [2, 3]


def test_intent_templates_disabled_keeps_single_subtask() -> None:
    payload = build_heuristic_plan_payload(
        CLAUDE_NEWS_TASK,
        default_tier="medium",
        intent_templates=False,
    )
    assert len(payload["subtasks"]) == 1
