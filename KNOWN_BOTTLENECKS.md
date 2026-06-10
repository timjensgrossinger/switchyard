# Known Bottlenecks

## Current throughput bottlenecks

1. **Provider round-trip latency** — planner, subtask execution, and synthesis still rely on blocking CLI subprocess calls, so process startup and remote model latency are now the dominant cost.
2. **Serial planner and synthesis stages** — wave parallelism only speeds up the middle of execution because planning happens once up front and synthesis happens once at the end.
3. **Single-lane speculative fallback** — higher-tier speculative work is funneled through a single-worker pool, so borderline tasks serialize on the expensive path.
4. **Sequential warm-path eval** — background rework evaluation processes prompts one at a time, which can backlog when multiple rework events land together.

## Not primary bottlenecks right now

- **SQLite hot path** — WAL mode + `conn()` accessor throughout hot modules; direct `_db._conn` usage eliminated from shared modules.
- **Approval queue and inspect flows** — secondary to planner/provider latency today, but the list/audit path is unpaginated and could backlog at high queue volume.

## Auto-detection status

The previously tracked auto-detection defects are covered by regression tests:

- Binary-only installer scans report auth-aware providers as `auth_unknown`
  instead of routeable.
- Verified installer scans use provider readiness probes and preserve precise
  failure reasons.
- MCP `clientInfo` mapping is centralized in `shared.discovery`.
- Environment markers use consistent truthy parsing.
- Tests cover Copilot, Claude, Gemini, Codex, Cursor, Junie, OpenCode, conflict
  precedence, and transport/parent-process fallback.
