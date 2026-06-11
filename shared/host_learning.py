"""Host-native execution learning ingest — closes the feedback loop for swarms/plans."""
from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Any, Mapping

from .agents import check_draft_ready, derive_learning_quality, pattern_hash, structured_pattern_example
from .config import TGsConfig
from .db import Database
from .eval import BackgroundEvaluator, WaveFileTracker, cold_path_adjust
from .memory import memory_refresh_swarm_state_from_db
from .outcomes import record_swarm_outcome
from .router import TaskRouter
from .style import DecompositionPrefs, StyleLearner

log = logging.getLogger(__name__)

_HOST_RUN_META: dict[str, dict[str, Any]] = {}
_HOST_WAVE_TRACKERS: dict[str, WaveFileTracker] = {}

_FILE_PATH_RE = re.compile(r"(?:^|\s)((?:\./|/)?[\w./-]+\.\w{1,6})")


def host_task_id(run_id: str, spawn_id: str) -> str:
    return f"{run_id}:{spawn_id}"


def plan_run_id(task_text: str) -> str:
    digest = hashlib.sha256(task_text.encode()).hexdigest()[:16]
    return f"plan-{digest}"


def _extract_file_paths(text: str) -> set[str]:
    paths: set[str] = set()
    for match in _FILE_PATH_RE.finditer(text):
        candidate = match.group(1)
        if len(candidate) > 3:
            paths.add(candidate)
    return paths


def _normalize_outcome(raw: object) -> str:
    value = str(raw or "").strip().lower()
    if value not in {"accepted", "revised", "reworked", "rejected"}:
        raise ValueError("outcome must be one of: accepted, revised, reworked, rejected")
    return value


def _wave_tracker(run_id: str) -> WaveFileTracker:
    tracker = _HOST_WAVE_TRACKERS.get(run_id)
    if tracker is None:
        tracker = WaveFileTracker()
        _HOST_WAVE_TRACKERS[run_id] = tracker
    return tracker


def register_host_run_handoff(
    db: Database,
    *,
    run_id: str,
    host_spawn_waves: list[dict[str, Any]],
    planned_subtasks: int,
    workspace_root: str | None = None,
    project_id: str | None = None,
    topology: str | None = None,
) -> None:
    """Persist handoff metadata and per-agent telemetry stubs."""
    handoff_caller: str | None = None
    for wave in host_spawn_waves:
        if not isinstance(wave, dict):
            continue
        agents = wave.get("agents")
        if not isinstance(agents, list):
            continue
        for agent in agents:
            if isinstance(agent, dict) and isinstance(agent.get("caller"), str) and agent["caller"].strip():
                handoff_caller = agent["caller"].strip()
                break
        if handoff_caller:
            break

    _HOST_RUN_META[run_id] = {
        "planned_subtasks": max(0, int(planned_subtasks)),
        "workspace_root": workspace_root,
        "project_id": project_id or workspace_root or "default-project",
        "topology": topology or "linear",
        "reported_agents": 0,
        "host_waves_completed": 0,
        "registered_ts": time.time(),
        "caller": handoff_caller,
    }
    _wave_tracker(run_id)

    for wave_idx, wave in enumerate(host_spawn_waves, start=1):
        if not isinstance(wave, dict):
            continue
        agents = wave.get("agents")
        if not isinstance(agents, list):
            continue
        for agent_index, agent in enumerate(agents):
            if not isinstance(agent, dict):
                continue
            spawn_id = str(agent.get("id") or agent_index)
            task_id = host_task_id(run_id, spawn_id)
            agent["task_id"] = task_id
            tier = str(agent.get("tier") or "medium")
            model = str(agent.get("model") or "host-native")
            try:
                db.log_agent_result(
                    session_id=run_id,
                    task_hash=task_id,
                    agent_id=int(spawn_id) if str(spawn_id).isdigit() else agent_index,
                    tier=tier,
                    model=model,
                    success=True,
                    provider_name=str(agent.get("caller") or "host-native"),
                    reason="host_handoff_stub",
                    version="host_native",
                )
                snapshot = {
                    "spawn_id": spawn_id,
                    "task_id": task_id,
                    "tier": tier,
                    "model": model,
                    "prompt": agent.get("prompt"),
                    "target_files": agent.get("target_files") or [],
                    "wave": wave_idx,
                }
                db.persist_worker_snapshot(
                    run_id,
                    worker_index=agent_index,
                    snapshot_json=snapshot,
                )
            except Exception:
                log.debug("host handoff stub failed for %s", task_id, exc_info=True)


def record_host_agent_result(
    db: Database,
    *,
    run_id: str,
    agent_spec: Mapping[str, Any],
    result: Mapping[str, Any],
    project_id: str | None = None,
) -> dict[str, Any]:
    """Record one host agent completion into pattern tracking and telemetry."""
    spawn_id = str(agent_spec.get("spawn_id") or agent_spec.get("id") or "")
    task_id = str(agent_spec.get("task_id") or host_task_id(run_id, spawn_id))
    description = str(
        agent_spec.get("description")
        or agent_spec.get("prompt")
        or f"host agent {spawn_id}"
    )
    tier = str(agent_spec.get("tier") or "medium")
    model = str(agent_spec.get("model") or "host-native")
    success = bool(result.get("success", True))
    output_excerpt = str(result.get("output_excerpt") or "")
    touched_files_raw = result.get("touched_files")
    touched_files: list[str] = []
    if isinstance(touched_files_raw, list):
        touched_files = [str(path).strip() for path in touched_files_raw if str(path).strip()]
    if not touched_files and output_excerpt:
        touched_files = sorted(_extract_file_paths(output_excerpt))

    rework_hint = bool(result.get("rework_detected", False))
    eval_quality = derive_learning_quality(
        success=success,
        escalated=False,
        rework_count=1 if rework_hint else 0,
        used_fallback=False,
        used_speculation=False,
        output=output_excerpt,
    )
    if success and output_excerpt.strip():
        outcome_summary = "completed"
    elif success:
        outcome_summary = "completed with no captured output"
    else:
        outcome_summary = "failed"

    example = structured_pattern_example(
        task=description,
        tier=tier,
        model=model,
        provider="host-native",
        touched_files=touched_files,
        outcome_summary=outcome_summary,
        quality_score=eval_quality,
    )
    ph = pattern_hash(description)
    resolved_project = project_id or _HOST_RUN_META.get(run_id, {}).get("project_id") or "default-project"

    try:
        db.track_pattern(
            pattern_hash=ph,
            pattern_desc=description,
            tier=tier,
            example=example,
            quality_score=eval_quality,
            rework_detected=rework_hint,
        )
        check_draft_ready(db, resolved_project, ph)
    except Exception:
        log.warning("host pattern tracking failed for %s", task_id, exc_info=True)

    try:
        db.log_agent_result(
            session_id=run_id,
            task_hash=task_id,
            agent_id=int(spawn_id) if spawn_id.isdigit() else 0,
            tier=tier,
            model=model,
            success=success,
            rework=rework_hint,
            provider_name="host-native",
            reason="host_agent_complete",
            version="host_native",
            timing_ms=int(result.get("duration_ms") or 0) if result.get("duration_ms") else None,
        )
    except Exception:
        log.debug("host agent telemetry update failed for %s", task_id, exc_info=True)

    meta = _HOST_RUN_META.setdefault(run_id, {})
    meta["reported_agents"] = int(meta.get("reported_agents") or 0) + 1

    return {
        "task_id": task_id,
        "pattern_hash": ph,
        "eval_quality": eval_quality,
        "touched_files": touched_files,
    }


def ingest_host_wave(
    db: Database,
    *,
    run_id: str,
    wave_index: int,
    agents: list[Mapping[str, Any]],
    workspace_root: str | None = None,
    terminal: bool = False,
    outcome: str | None = None,
    config: TGsConfig | None = None,
    router: TaskRouter | None = None,
) -> dict[str, Any]:
    """Ingest one host-reported wave and optionally finalize the run."""
    if wave_index < 1:
        raise ValueError("wave must be >= 1")
    meta = _HOST_RUN_META.setdefault(run_id, {})
    if workspace_root:
        meta["workspace_root"] = workspace_root
    project_id = str(meta.get("project_id") or workspace_root or "default-project")
    handoff_caller = str(meta.get("caller") or "mcp")
    handoff_cwd = workspace_root or meta.get("workspace_root")

    db.persist_swarm_run(
        {
            "swarm_id": run_id,
            "status": "running",
            "resume_status": "running",
        }
    )

    tracker = _wave_tracker(run_id)
    wave_files: set[str] = set()
    content_before: dict[str, str] = {}
    content_after: dict[str, str] = {}
    agent_results: list[dict[str, Any]] = []

    for agent in agents:
        if not isinstance(agent, Mapping):
            continue
        spawn_id = str(agent.get("spawn_id") or agent.get("id") or "")
        spec = {
            "spawn_id": spawn_id,
            "task_id": agent.get("task_id") or host_task_id(run_id, spawn_id),
            "tier": agent.get("tier"),
            "model": agent.get("model"),
            "prompt": agent.get("prompt"),
            "description": agent.get("description") or agent.get("prompt"),
        }
        result_payload = {
            "success": agent.get("success", True),
            "touched_files": agent.get("touched_files") or [],
            "output_excerpt": agent.get("output_excerpt") or "",
            "rework_detected": agent.get("rework_detected", False),
            "duration_ms": agent.get("duration_ms"),
        }
        recorded = record_host_agent_result(
            db,
            run_id=run_id,
            agent_spec=spec,
            result=result_payload,
            project_id=project_id,
        )
        agent_results.append(recorded)
        task_id = str(spec.get("task_id") or "")
        for path in recorded.get("touched_files") or []:
            if not isinstance(path, str) or not path.strip():
                continue
            try:
                db.routing_guard_record_execution(
                    caller=handoff_caller,
                    cwd=handoff_cwd,
                    task_id=task_id,
                    file_written=path.strip(),
                )
            except Exception:
                log.debug(
                    "routing_guard_record_execution failed for %s",
                    path,
                    exc_info=True,
                )
        for path in recorded.get("touched_files") or []:
            wave_files.add(path)
            if workspace_root:
                abs_path = Path(workspace_root) / path
                if abs_path.is_file():
                    try:
                        content_after[path] = abs_path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        log.debug("could not read %s for rework tracking", abs_path, exc_info=True)

    if wave_index > 1:
        prev_files = tracker.wave_files.get(wave_index - 1, set())
        for path in wave_files & prev_files:
            before = tracker.snapshots_after.get(path, tracker.snapshots_before.get(path, ""))
            if before:
                content_before[path] = before

    tracker.record_wave(
        wave_index,
        wave_files,
        content_before=content_before or None,
        content_after=content_after or None,
    )
    rework_events: list[dict[str, Any]] = []
    if wave_index > 1:
        rework_events = tracker.detect_rework(wave_index, db=db, session_id=run_id)

    if workspace_root:
        for path, after in content_after.items():
            before = content_before.get(path) or tracker.snapshots_before.get(path, "")
            if before and before != after:
                observe_host_style_edits(
                    db,
                    project_path=workspace_root,
                    file_path=path,
                    original=before,
                    edited=after,
                )

    meta["host_waves_completed"] = wave_index

    db.log_swarm_event(
        run_id,
        "wave_progress",
        {
            "wave": wave_index,
            "agent_count": len(agent_results),
            "rework_events": len(rework_events),
        },
    )
    db.log_swarm_event(
        run_id,
        "host_agent_complete",
        {"wave": wave_index, "agents": agent_results},
    )

    try:
        memory_refresh_swarm_state_from_db(run_id, db=db)
    except Exception:
        log.debug("swarm memory refresh failed for %s", run_id, exc_info=True)

    db.persist_swarm_run(
        {
            "swarm_id": run_id,
            "status": "running",
            "progress_counters": {
                "host_waves_completed": wave_index,
                "host_agents_reported": len(agent_results),
            },
            "resume_status": "running",
        }
    )

    response: dict[str, Any] = {
        "run_id": run_id,
        "wave": wave_index,
        "agents_recorded": len(agent_results),
        "rework_events": rework_events,
        "terminal": terminal,
    }

    if terminal:
        if outcome is None:
            raise ValueError("outcome is required when terminal=true")
        response["finalize"] = finalize_host_swarm(
            db,
            run_id,
            outcome,
            config=config,
            router=router,
            workspace_root=workspace_root,
            rework_events=rework_events,
        )
    return response


def finalize_host_swarm(
    db: Database,
    run_id: str,
    outcome: str,
    *,
    config: TGsConfig | None = None,
    router: TaskRouter | None = None,
    workspace_root: str | None = None,
    note: str | Mapping[str, object] | None = None,
    rework_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Terminalize a host-native run and fan out learning side-effects."""
    normalized_outcome = _normalize_outcome(outcome)
    meta = _HOST_RUN_META.get(run_id, {})
    project_id = str(meta.get("project_id") or workspace_root or "default-project")
    planned = int(meta.get("planned_subtasks") or 0)
    reported = int(meta.get("reported_agents") or 0)
    topology = str(meta.get("topology") or "linear")
    success = normalized_outcome in {"accepted", "revised"}

    status = "completed" if success else "failed"
    db.persist_swarm_run(
        {
            "swarm_id": run_id,
            "status": status,
            "resume_status": status,
            "progress_counters": {
                "host_waves_completed": meta.get("host_waves_completed"),
                "host_agents_reported": reported,
            },
        }
    )
    db.log_swarm_event(
        run_id,
        "host_swarm_complete",
        {"outcome": normalized_outcome, "reported_agents": reported},
    )

    swarm_outcome: dict[str, Any] | None = None
    try:
        swarm_outcome = record_swarm_outcome(
            db,
            run_id,
            normalized_outcome,
            selected_topology=topology,
            operator_id="host-native",
            note=note,
            project_id=project_id,
        )
    except Exception:
        log.warning("record_swarm_outcome failed for %s", run_id, exc_info=True)

    if router is not None and project_id and router.is_learning_enabled(project_id):
        try:
            was_correct = normalized_outcome in {"accepted", "revised"}
            tier = "medium"
            with db.conn() as conn:
                row = conn.execute(
                    "SELECT tier FROM telemetry WHERE session_id = ? ORDER BY ts DESC LIMIT 1",
                    (run_id,),
                ).fetchone()
            if row and row[0]:
                tier = str(row[0])
            router.learn_project_routing(project_id, tier, was_correct=was_correct)
            hour = time.localtime().tm_hour
            router.learn_time_pattern(hour, was_quality_focused=was_correct)
        except Exception:
            log.debug("routing bias learning failed for %s", run_id, exc_info=True)

    try:
        db.update_routing_decision_outcome(
            run_id,
            outcome_score=1.0 if success else 0.0,
            regret=0.0 if success else 1.0,
        )
    except Exception:
        log.debug("bandit outcome update skipped for %s", run_id, exc_info=True)

    if config is not None:
        try:
            cold_path_adjust(db, config)
        except Exception:
            log.debug("cold_path_adjust failed", exc_info=True)

    if workspace_root and reported > 0:
        try:
            DecompositionPrefs(db).record_plan_interaction(
                workspace_root,
                planned_count=max(planned, reported),
                actual_count=reported,
            )
        except Exception:
            log.debug("decomposition prefs record failed", exc_info=True)

    if config is not None and rework_events:
        try:
            tracker = _HOST_WAVE_TRACKERS.get(run_id)
            if tracker is not None:
                evaluator = BackgroundEvaluator(db=db, config=config)
                evaluator.spawn_warm_path(tracker, rework_events)
        except Exception:
            log.debug("warm path spawn failed for %s", run_id, exc_info=True)

    try:
        memory_refresh_swarm_state_from_db(run_id, db=db)
    except Exception:
        log.debug("final swarm memory refresh failed", exc_info=True)

    _HOST_WAVE_TRACKERS.pop(run_id, None)
    _HOST_RUN_META.pop(run_id, None)

    return {
        "run_id": run_id,
        "status": status,
        "outcome": normalized_outcome,
        "swarm_outcome": swarm_outcome,
        "reported_agents": reported,
    }


def inspect_host_swarm(db: Database, run_id: str) -> dict[str, Any] | None:
    """Return swarm summary plus host-run metadata when present."""
    summary = db.get_swarm_summary(run_id)
    if summary is None:
        return None
    payload = dict(summary)
    meta = _HOST_RUN_META.get(run_id)
    if meta:
        payload["host_run_meta"] = dict(meta)
    return payload


def observe_host_style_edits(
    db: Database,
    *,
    project_path: str,
    file_path: str,
    original: str,
    edited: str,
) -> None:
    """Best-effort style learning when before/after content is available."""
    if not original.strip() or not edited.strip() or original == edited:
        return
    try:
        StyleLearner(db).observe(project_path, original, edited)
    except Exception:
        log.debug("StyleLearner.observe failed for %s", file_path, exc_info=True)


__all__ = [
    "finalize_host_swarm",
    "host_task_id",
    "ingest_host_wave",
    "inspect_host_swarm",
    "observe_host_style_edits",
    "plan_run_id",
    "record_host_agent_result",
    "register_host_run_handoff",
]
