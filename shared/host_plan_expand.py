"""Mid-run host-native plan expansion for discovered files."""
from __future__ import annotations

import logging
from typing import Any

from .config import TGsConfig
from .db import Database
from .heuristic_plan import file_entries_from_paths, _is_integration_file, _tier_for_subtask
from .host_learning import _HOST_RUN_META, _ensure_host_run_meta, register_host_run_handoff
from .host_spawn import build_host_spawn_waves
from .planner import build_waves, Subtask

log = logging.getLogger(__name__)


def _normalize_paths(paths: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        path = str(raw).strip().replace("\\", "/")
        if not path or path.lower() in seen:
            continue
        seen.add(path.lower())
        result.append(path)
    return result


def _assigned_files(meta: dict[str, Any], snapshots: list[dict[str, object]]) -> set[str]:
    assigned: set[str] = set()
    raw_assigned = meta.get("assigned_files")
    if isinstance(raw_assigned, list):
        for path in raw_assigned:
            if isinstance(path, str) and path.strip():
                assigned.add(path.strip().replace("\\", "/").lower())
    for snap in snapshots:
        targets = snap.get("target_files")
        if isinstance(targets, list):
            for path in targets:
                if isinstance(path, str) and path.strip():
                    assigned.add(path.strip().replace("\\", "/").lower())
    return assigned


def expand_host_plan(
    db: Database,
    *,
    run_id: str,
    discovered_files: list[str],
    workspace_root: str | None = None,
    config: TGsConfig,
    caller: str | None = None,
    reason: str = "host_plan_expand",
    descriptions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Append file-scoped subtasks and return pending host_spawn_waves."""
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise ValueError("run_id is required")

    summary = db.get_swarm_summary(normalized_run_id)
    if summary is None:
        raise ValueError(f"run_id {normalized_run_id!r} was not found")

    status = str(summary.get("status") or "")
    resume_status = str(summary.get("resume_status") or "")
    if status not in {"awaiting_host_execution", "running"} and resume_status not in {
        "awaiting_host_execution",
        "running",
    }:
        raise ValueError(
            f"run {normalized_run_id} is not expandable (status={status}, resume={resume_status})"
        )

    meta = _ensure_host_run_meta(db, normalized_run_id)
    if workspace_root:
        meta["workspace_root"] = workspace_root
    resolved_workspace = str(meta.get("workspace_root") or workspace_root or "")
    snapshots = db.get_handoff_agent_snapshots(normalized_run_id)
    assigned = _assigned_files(meta, snapshots)

    normalized_discovered = _normalize_paths(discovered_files)
    new_paths = [p for p in normalized_discovered if p.lower() not in assigned]
    if not new_paths:
        return {
            "expanded": False,
            "run_id": normalized_run_id,
            "reason": "no_new_files",
            "host_spawn_waves": [],
        }

    task_hint = str(meta.get("task_hint") or reason)
    entries = file_entries_from_paths(new_paths, task_hint=task_hint)
    if descriptions:
        entries = [
            (path, descriptions.get(path, hint) if descriptions.get(path) else hint)
            for path, hint in entries
        ]

    start_id = int(meta.get("next_subtask_id") or 0)
    if start_id < 1:
        max_spawn = 0
        for snap in snapshots:
            spawn_raw = snap.get("spawn_id")
            if isinstance(spawn_raw, str) and spawn_raw.isdigit():
                max_spawn = max(max_spawn, int(spawn_raw))
            elif isinstance(spawn_raw, int):
                max_spawn = max(max_spawn, spawn_raw)
        start_id = max_spawn + 1 if max_spawn > 0 else 1

    default_tier = "medium"
    subtasks: list[dict[str, object]] = []
    integration_ids: list[int] = []
    foundation_ids: list[int] = []
    for offset, (path, hint) in enumerate(entries):
        subtask_id = start_id + offset
        tier = _tier_for_subtask(file_count=len(entries), default_tier=default_tier)
        subtask: dict[str, object] = {
            "id": subtask_id,
            "description": hint,
            "tier": tier,
            "target_file": path,
            "single_file_insertion": False,
            "depends_on": [],
        }
        subtasks.append(subtask)
        if _is_integration_file(path):
            integration_ids.append(subtask_id)
        else:
            foundation_ids.append(subtask_id)

    if integration_ids and foundation_ids:
        foundation_set = set(foundation_ids)
        for subtask in subtasks:
            if int(subtask["id"]) in integration_ids:
                subtask["depends_on"] = sorted(foundation_set)

    subtask_objs = [
        Subtask(
            id=int(st["id"]),
            description=str(st["description"]),
            tier=str(st["tier"]),
            model="",
            depends_on=list(st.get("depends_on") or []),
            target_file=str(st.get("target_file") or "") or None,
        )
        for st in subtasks
    ]
    wave_ids = build_waves(subtask_objs)
    plan_dict: dict[str, object] = {
        "subtasks": subtasks,
        "waves": wave_ids,
        "topology": str(meta.get("topology") or "dag"),
    }
    host_waves = build_host_spawn_waves(
        plan_dict,
        config=config,
        caller=caller or str(meta.get("caller") or "mcp"),
    )
    start_wave = int(meta.get("host_waves_completed") or 0) + 1
    for wave in host_waves:
        if isinstance(wave, dict):
            wave["wave"] = start_wave
            start_wave += 1

    revision_number = int(meta.get("plan_revision") or 0) + 1
    diff_blob = {
        "discovered_files": new_paths,
        "subtasks": subtasks,
        "waves": wave_ids,
        "reason": reason,
    }
    try:
        db.insert_plan_revision(
            normalized_run_id,
            revision_number,
            diff_blob,
            proposer_id=str(caller or meta.get("caller") or "host-native"),
            reason=reason,
        )
    except Exception:
        log.debug("plan revision persist failed for %s", normalized_run_id, exc_info=True)

    meta["plan_revision"] = revision_number
    meta["next_subtask_id"] = start_id + len(subtasks)
    for path in new_paths:
        if path not in meta.get("assigned_files", []):
            assigned_list = list(meta.get("assigned_files") or [])
            assigned_list.append(path)
            meta["assigned_files"] = assigned_list
    meta["planned_subtasks"] = int(meta.get("planned_subtasks") or 0) + len(subtasks)
    _HOST_RUN_META[normalized_run_id] = meta

    register_host_run_handoff(
        db,
        run_id=normalized_run_id,
        host_spawn_waves=host_waves,
        planned_subtasks=int(meta.get("planned_subtasks") or len(subtasks)),
        workspace_root=resolved_workspace or None,
        project_id=str(meta.get("project_id") or resolved_workspace or "default-project"),
        topology=str(meta.get("topology") or "dag"),
        task_hint=task_hint,
    )

    return {
        "expanded": True,
        "run_id": normalized_run_id,
        "new_files": new_paths,
        "host_spawn_waves": host_waves,
        "start_wave": int(meta.get("host_waves_completed") or 0) + 1,
        "plan_revision": revision_number,
        "execution_contract": "spawn_subagents",
    }


__all__ = ["expand_host_plan"]
