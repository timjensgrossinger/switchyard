"""Heuristic task decomposition without external LLM calls.

Used for host-native planning: MCP host shells decompose locally and execute
via host Task/Agent tools. No subprocess to Copilot, Codex, or other CLIs.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from .context import extract_references

_NUMBERED_FILE = re.compile(
    r"\(\d+\)\s*([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.[A-Za-z][A-Za-z0-9]*)",
    re.IGNORECASE,
)
_BARE_FILENAME = re.compile(
    r"(?<![\w/.])([A-Za-z0-9_.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|kt|rb|cs|yaml|yml|json|toml|md))\b",
    re.IGNORECASE,
)
_CLAUSE_SPLIT = re.compile(
    r"(?<=[,;])\s*(?=[A-Za-z0-9_.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|kt|rb|cs|yaml|yml|json|toml|md)\b)",
    re.IGNORECASE,
)
_INTEGRATION_STEMS = frozenset({"main", "cli", "app", "__init__", "index"})


def _normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def _basename(path: str) -> str:
    return PurePosixPath(_normalize_path(path)).name.lower()


def _stem(path: str) -> str:
    return PurePosixPath(_normalize_path(path)).stem.lower()


def _is_integration_file(path: str) -> bool:
    name = _basename(path)
    stem = _stem(path)
    if stem in _INTEGRATION_STEMS:
        return True
    return name in {"index.ts", "index.tsx", "index.js", "index.jsx"}


def extract_task_file_entries(task: str) -> list[tuple[str, str]]:
    """Return ordered (path, description_hint) pairs extracted from *task*."""
    if not isinstance(task, str) or not task.strip():
        return []

    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(path: str, hint: str = "") -> None:
        normalized = _normalize_path(path)
        key = normalized.lower()
        if key in seen:
            return
        seen.add(key)
        ordered.append((normalized, hint.strip()))

    for ref in extract_references(task):
        _add(ref.path)

    for match in _NUMBERED_FILE.finditer(task):
        _add(match.group(1))

    for match in _BARE_FILENAME.finditer(task):
        _add(match.group(1))

    if not ordered:
        return []

    hints = _description_hints_by_path(task, [path for path, _ in ordered])
    return [(path, hints.get(path.lower(), "")) for path, _ in ordered]


def _description_hints_by_path(task: str, paths: list[str]) -> dict[str, str]:
    hints: dict[str, str] = {}
    numbered = list(_NUMBERED_FILE.finditer(task))
    if numbered:
        for idx, match in enumerate(numbered):
            path = _normalize_path(match.group(1))
            start = match.end()
            end = numbered[idx + 1].start() if idx + 1 < len(numbered) else len(task)
            fragment = task[start:end].strip(" ,;:-")
            if fragment:
                hints[path.lower()] = f"Create {path}: {fragment}".strip()
        return hints

    clauses = _CLAUSE_SPLIT.split(task)
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        file_match = _BARE_FILENAME.search(clause) or _NUMBERED_FILE.search(clause)
        if not file_match:
            continue
        path = _normalize_path(file_match.group(1))
        hints[path.lower()] = clause.strip(" ,;")

    for path in paths:
        key = path.lower()
        if key in hints:
            continue
        name = PurePosixPath(path).name
        pattern = re.compile(
            rf"{re.escape(name)}[^.;,\n]{{0,120}}",
            re.IGNORECASE,
        )
        match = pattern.search(task)
        if match:
            hints[key] = match.group(0).strip(" ,;")
    return hints


def _tier_for_subtask(*, file_count: int, default_tier: str) -> str:
    if file_count <= 1:
        return default_tier if default_tier in {"low", "medium", "high"} else "medium"
    return "low"


def build_heuristic_plan_payload(
    task: str,
    *,
    default_tier: str = "medium",
    max_agents: int | None = None,
    topology: str | None = None,
) -> dict[str, object]:
    """Build planner JSON compatible with ``Planner._build_plan`` without an LLM."""
    entries = extract_task_file_entries(task)
    if max_agents is not None:
        try:
            cap = max(1, int(max_agents))
        except (TypeError, ValueError):
            cap = None
        else:
            entries = entries[:cap]

    if not entries:
        tier = default_tier if default_tier in {"low", "medium", "high"} else "medium"
        return {
            "analysis": (
                "Host-native heuristic plan: single subtask (no file paths detected). "
                "No external planner LLM was called."
            ),
            "subtasks": [
                {
                    "id": 1,
                    "description": task.strip(),
                    "tier": tier,
                    "depends_on": [],
                }
            ],
            "strategy": "sequential",
            "topology": topology or "linear",
        }

    integration_ids: list[int] = []
    foundation_ids: list[int] = []
    subtasks: list[dict[str, object]] = []
    for index, (path, hint) in enumerate(entries, start=1):
        description = hint or f"Create or update {path} as described in the task."
        tier = _tier_for_subtask(file_count=len(entries), default_tier=default_tier)
        subtasks.append(
            {
                "id": index,
                "description": description,
                "tier": tier,
                "target_file": path,
                "single_file_insertion": False,
                "depends_on": [],
            }
        )
        if _is_integration_file(path):
            integration_ids.append(index)
        else:
            foundation_ids.append(index)

    if integration_ids and foundation_ids:
        foundation_set = set(foundation_ids)
        for subtask in subtasks:
            if int(subtask["id"]) in integration_ids:
                subtask["depends_on"] = sorted(foundation_set)

    has_deps = any(subtask.get("depends_on") for subtask in subtasks)
    normalized_topology = str(topology or "").strip().lower()
    if normalized_topology in {"star", "hierarchical", "dag", "linear"}:
        plan_topology = normalized_topology
    else:
        plan_topology = "dag" if has_deps else "linear"

    return {
        "analysis": (
            f"Host-native heuristic plan: {len(subtasks)} file-scoped subtask(s) "
            "from task text. No external planner LLM was called."
        ),
        "subtasks": subtasks,
        "strategy": "dag" if has_deps else "parallel",
        "topology": plan_topology,
    }
