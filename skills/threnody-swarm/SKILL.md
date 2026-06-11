---
name: threnody-swarm
description: >-
  Start and run Threnody execute_swarm with host-native host_spawn_waves,
  topology selection (dag/star/hierarchical/auto), budget preview, and resume.
  Use when user asks to swarm, fan out agents, or run multi-agent waves with
  swarm_id persistence.
---

# Threnody swarm orchestration

## Default: host-native swarms

For MCP host callers (Claude, Copilot, Cursor, Codex, etc.), `execute_swarm`
defaults to **`host_native`**:

1. Threnody plans the task.
2. Returns `awaiting_host_execution: true` + `host_spawn_waves`.
3. **You** spawn host `Task`/`Agent` per wave — Threnody does not subprocess.

This path is **unaffected** by utility-only delegation rules.

## Workflow

0. If not already planned, follow **`threnody-plan`** (plan-only swarm preview stops before spawn).
1. Optionally `route_task` for tier context.
2. **`execute_swarm(task, topology?, max_agents?, budget_limit?)`**
3. Handle response:
   - **`awaiting_host_execution` + `host_spawn_waves`** — execute waves via host agents.
   - **`preview: true` + `preview_token`** — cost over budget; confirm then re-call with token.
   - **`started: true`** (delegate mode only) — Threnody subprocess orchestrator running.
4. **After each wave:** call `report_host_wave` with `workspace_root` from the handoff (`workspace_root` / `learning_report_contract`) and per-agent results: `task_id`, `spawn_id`, `success`, `touched_files`, **`output_excerpt`** (1–2 sentence summary).
5. **Mid-run expansion:** after scaffold/contract waves, call `expand_host_plan(discovered_files=[...])` or `report_host_wave(expand_plan=true, discovered_files=[...])` to spawn additional file-scoped agents.
6. **Terminal wave:** set `terminal=true` and `outcome=accepted|revised|reworked|rejected`, or call `report_host_swarm_complete`. Verify `finalize.swarm_outcome.stored`.
7. Monitor:
   - Host-native: `inspect_swarm` for status; optional `inspect_status`.
   - Delegate: `list_subtasks`, `resume_swarm_inspect`, `resume_swarm_confirm`.

## Must (when awaiting_host_execution)

- Spawn **one** host `Task`/`Agent` per entry in `host_spawn_waves[].agents` — all agents in a wave in **one parallel message**.
- Pass each agent its `prompt`, `target_files`, `model`, and `subagent_type` from the handoff.
- Do **not** use `Write`/`Edit` yourself on any `target_files` from the plan.
- Do not follow `route_task`'s `direct_edit` hint while a handoff is active (`execution_hint.active_handoff` or pending `host_spawn_waves`).
- After the wave: `report_host_wave` (with `workspace_root` + `output_excerpt`) → `inspect_swarm`.

## Wave report example

```python
report_host_wave(
  swarm_id="<from handoff>",
  wave=1,
  workspace_root="<workspace_root from handoff>",
  agents=[{
    "task_id": "swarm-...:2",
    "spawn_id": "<host-agent-id>",
    "success": True,
    "touched_files": ["styles.css"],
    "output_excerpt": "Created card layout CSS with loading/error states",
  }],
)
```

Check `learning_enrichment` in the response when the server auto-fills excerpts from disk.

## Topology

| Value | Use when |
|-------|----------|
| `auto` | Let Threnody pick from urgency/complexity heuristics |
| `dag` | Explicit `depends_on` chains (recommended for full-stack) |
| `hierarchical` | Parent/child subtask trees |
| `star` | One **coordinator** + workers; reconciliation rounds (**delegate mode only**) |

**Not multi-queen:** at most one coordinator subtask per wave. Star topology uses a single coordinator with verdicts `complete` | `another-pass` | `fallback` — not peer voting.

## Delegate mode (legacy/expert)

Override only when intentional (`~/.local/lib/threnody/config.yaml`):

```yaml
swarm:
  host_execution_mode: delegate  # default for hosts is host_native
```

Delegate mode subprocesses via the orchestrator. Higher billing and policy surface.

## Full-stack parallel work

For frontend + backend + API simultaneously, see **`threnody-fullstack`** — contract-first DAG waves, integration subtask, optional coordinator star in delegate mode.

## Do not

- Call `execute_subtask` for same-host swarm agents.
- Substitute direct writes for swarm agents, even for low-tier or trivial tasks.
- Assume Threnody merges conflicting parallel edits — include an integration wave or review yourself.
