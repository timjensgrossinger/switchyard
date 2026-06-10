"""
Trace replay and state forking (plan 13).

Operator can replay any swarm run from a coordinator checkpoint,
optionally fork — alter tier/provider overrides, continue forward.

Requires:
  - plan 01 (idempotency): SIDE_EFFECTING subtasks deduped via idempotency_key
  - plan 02 (op_class): replay engine dispatches by op_class
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .db import Database

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class CheckpointEntry:
    """Single coordinator round checkpoint."""
    id: int
    swarm_id: str
    plan_revision: int
    round_index: int
    coordinator_subtask_id: str
    verdict: str | None
    amendment_json: str | None
    next_work_json: str | None
    synthesis_summary_json: str | None
    created_ts: float

    @property
    def amendment(self) -> dict:
        if not self.amendment_json:
            return {}
        try:
            return json.loads(self.amendment_json)
        except Exception:
            return {}

    @property
    def next_work(self) -> list:
        if not self.next_work_json:
            return []
        try:
            return json.loads(self.next_work_json)
        except Exception:
            return []


@dataclass
class ReplayPlan:
    """Result of planning a replay from a given checkpoint."""
    source_run_id: str
    fork_run_id: str
    from_checkpoint_id: int
    from_round_index: int
    is_fork: bool
    overrides: dict
    dry_run: bool
    subtasks_to_replay: list[dict] = field(default_factory=list)
    subtasks_to_skip: list[dict] = field(default_factory=list)
    approval_gates: list[dict] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        return {
            "source_run_id": self.source_run_id,
            "fork_run_id": self.fork_run_id,
            "from_checkpoint_id": self.from_checkpoint_id,
            "from_round_index": self.from_round_index,
            "is_fork": self.is_fork,
            "dry_run": self.dry_run,
            "overrides": self.overrides,
            "subtasks_to_replay": len(self.subtasks_to_replay),
            "subtasks_to_skip": len(self.subtasks_to_skip),
            "approval_gates": len(self.approval_gates),
        }


@dataclass
class DiffEntry:
    """Per-checkpoint diff between two runs."""
    round_index: int
    run_a_verdict: str | None
    run_b_verdict: str | None
    diverged: bool
    run_a_ts: float | None
    run_b_ts: float | None
    token_delta: int | None = None
    cost_delta: float | None = None


# ---------------------------------------------------------------------------
# Replay engine
# ---------------------------------------------------------------------------

class ReplayEngine:
    """Loads checkpoints and plans/executes replay or fork."""

    def __init__(self, db: "Database") -> None:
        self._db = db

    # ------------------------------------------------------------------
    # show: checkpoint timeline
    # ------------------------------------------------------------------

    def show_checkpoints(self, run_id: str) -> list[CheckpointEntry]:
        """Return all coordinator checkpoints for a run in chronological order."""
        with self._db.conn() as conn:
            rows = conn.execute(
                "SELECT id, swarm_id, plan_revision, round_index,"
                " coordinator_subtask_id, verdict, amendment_json,"
                " next_work_json, synthesis_summary_json, created_ts"
                " FROM coordinator_round_checkpoints"
                " WHERE swarm_id=? ORDER BY round_index, plan_revision",
                (run_id,),
            ).fetchall()
        return [
            CheckpointEntry(
                id=r[0], swarm_id=r[1], plan_revision=r[2], round_index=r[3],
                coordinator_subtask_id=r[4], verdict=r[5],
                amendment_json=r[6], next_work_json=r[7],
                synthesis_summary_json=r[8], created_ts=r[9],
            )
            for r in rows
        ]

    def show_run(self, run_id: str) -> dict:
        """Return run metadata + checkpoint timeline."""
        with self._db.conn() as conn:
            row = conn.execute(
                "SELECT swarm_id, task_hash, created_ts, status,"
                " requested_agents, effective_agents, topology, parent_swarm_id"
                " FROM swarm_runs WHERE swarm_id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            return {"error": f"Run {run_id!r} not found"}
        checkpoints = self.show_checkpoints(run_id)
        return {
            "run_id": row[0],
            "task_hash": row[1],
            "created_ts": row[2],
            "status": row[3],
            "requested_agents": row[4],
            "effective_agents": row[5],
            "topology": row[6],
            "parent_swarm_id": row[7],
            "checkpoints": [
                {
                    "id": c.id,
                    "round": c.round_index,
                    "revision": c.plan_revision,
                    "verdict": c.verdict,
                    "ts": c.created_ts,
                }
                for c in checkpoints
            ],
        }

    # ------------------------------------------------------------------
    # plan_replay: build a ReplayPlan without executing
    # ------------------------------------------------------------------

    def plan_replay(
        self,
        run_id: str,
        from_checkpoint_id: int | None = None,
        overrides: dict | None = None,
        dry_run: bool = True,
        is_fork: bool = False,
    ) -> ReplayPlan:
        """Plan a replay (or fork). Does not write anything if dry_run=True."""
        checkpoints = self.show_checkpoints(run_id)
        if not checkpoints:
            return ReplayPlan(
                source_run_id=run_id,
                fork_run_id="",
                from_checkpoint_id=from_checkpoint_id or 0,
                from_round_index=0,
                is_fork=is_fork,
                overrides=overrides or {},
                dry_run=dry_run,
            )

        # Find start checkpoint
        start_cp: CheckpointEntry | None = None
        if from_checkpoint_id is not None:
            for cp in checkpoints:
                if cp.id == from_checkpoint_id:
                    start_cp = cp
                    break
        if start_cp is None:
            start_cp = checkpoints[0]

        fork_run_id = str(uuid.uuid4()) if is_fork else run_id
        subsequent = [cp for cp in checkpoints if cp.round_index > start_cp.round_index]

        to_replay: list[dict] = []
        to_skip: list[dict] = []
        approval_gates: list[dict] = []

        # Classify each subtask in subsequent checkpoints by op_class
        for cp in subsequent:
            for subtask in cp.next_work:
                op_class = subtask.get("op_class", "side_effecting")
                entry = {
                    "subtask_id": subtask.get("id"),
                    "description": subtask.get("description", ""),
                    "op_class": op_class,
                    "round_index": cp.round_index,
                    "checkpoint_id": cp.id,
                    "overrides": overrides or {},
                }
                if op_class == "replayable":
                    to_replay.append(entry)
                elif op_class == "approval_required":
                    approval_gates.append(entry)
                else:
                    # side_effecting → skip via idempotency (don't re-run)
                    to_skip.append(entry)

        plan = ReplayPlan(
            source_run_id=run_id,
            fork_run_id=fork_run_id,
            from_checkpoint_id=start_cp.id,
            from_round_index=start_cp.round_index,
            is_fork=is_fork,
            overrides=overrides or {},
            dry_run=dry_run,
            subtasks_to_replay=to_replay,
            subtasks_to_skip=to_skip,
            approval_gates=approval_gates,
        )
        return plan

    # ------------------------------------------------------------------
    # execute_replay
    # ------------------------------------------------------------------

    def execute_replay(
        self,
        run_id: str,
        from_checkpoint_id: int | None = None,
        overrides: dict | None = None,
    ) -> dict:
        """Execute a replay from checkpoint. Returns plan summary."""
        plan = self.plan_replay(
            run_id,
            from_checkpoint_id=from_checkpoint_id,
            overrides=overrides,
            dry_run=False,
            is_fork=False,
        )
        log.info(
            "Replay %s from checkpoint %s: %d replayable, %d skip, %d approval gates",
            run_id, plan.from_checkpoint_id,
            len(plan.subtasks_to_replay), len(plan.subtasks_to_skip),
            len(plan.approval_gates),
        )
        if plan.approval_gates:
            return {
                "status": "halted",
                "reason": "approval_required",
                "approval_gates": plan.approval_gates,
                "plan": plan.summary,
            }
        return {"status": "planned", "plan": plan.summary}

    # ------------------------------------------------------------------
    # fork
    # ------------------------------------------------------------------

    def fork(
        self,
        run_id: str,
        from_checkpoint_id: int | None = None,
        overrides: dict | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Fork a run from a checkpoint. Creates a new swarm_run row."""
        plan = self.plan_replay(
            run_id,
            from_checkpoint_id=from_checkpoint_id,
            overrides=overrides,
            dry_run=dry_run,
            is_fork=True,
        )
        if not dry_run:
            now = time.time()
            try:
                with self._db.conn() as conn:
                    conn.execute(
                        "INSERT INTO swarm_runs"
                        " (swarm_id, task_hash, created_ts, status,"
                        "  requested_agents, effective_agents,"
                        "  progress_counters, topology, round, resumable,"
                        "  resume_status, parent_swarm_id)"
                        " VALUES (?, ?, ?, 'forked', 0, 0, '{}', 'linear', 0, 0,"
                        "         'not_resumable', ?)",
                        (plan.fork_run_id, "fork", now, run_id),
                    )
            except Exception:
                log.debug("Failed to write fork swarm_run row", exc_info=True)
        return {
            "status": "dry_run" if dry_run else "forked",
            "fork_run_id": plan.fork_run_id,
            "parent_run_id": run_id,
            "plan": plan.summary,
        }

    # ------------------------------------------------------------------
    # diff
    # ------------------------------------------------------------------

    def diff(self, run_a: str, run_b: str) -> dict:
        """Side-by-side comparison of two run checkpoint timelines."""
        cps_a = {cp.round_index: cp for cp in self.show_checkpoints(run_a)}
        cps_b = {cp.round_index: cp for cp in self.show_checkpoints(run_b)}
        all_rounds = sorted(set(cps_a) | set(cps_b))

        entries: list[DiffEntry] = []
        first_diverge: int | None = None
        for r in all_rounds:
            cp_a = cps_a.get(r)
            cp_b = cps_b.get(r)
            v_a = cp_a.verdict if cp_a else None
            v_b = cp_b.verdict if cp_b else None
            diverged = v_a != v_b
            if diverged and first_diverge is None:
                first_diverge = r
            entries.append(DiffEntry(
                round_index=r,
                run_a_verdict=v_a,
                run_b_verdict=v_b,
                diverged=diverged,
                run_a_ts=cp_a.created_ts if cp_a else None,
                run_b_ts=cp_b.created_ts if cp_b else None,
            ))

        return {
            "run_a": run_a,
            "run_b": run_b,
            "total_rounds": len(all_rounds),
            "diverge_at_round": first_diverge,
            "identical": first_diverge is None,
            "diffs": [
                {
                    "round": e.round_index,
                    "run_a_verdict": e.run_a_verdict,
                    "run_b_verdict": e.run_b_verdict,
                    "diverged": e.diverged,
                }
                for e in entries
                if e.diverged
            ],
            "all": [
                {
                    "round": e.round_index,
                    "run_a_verdict": e.run_a_verdict,
                    "run_b_verdict": e.run_b_verdict,
                    "diverged": e.diverged,
                }
                for e in entries
            ],
        }
