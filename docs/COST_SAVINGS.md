# Cost savings workflows

Threnody helps cost-conscious operators **spend less across the AI CLIs they
already pay for**. Credentials stay in provider-native stores; Threnody does not
manage API keys.

Cost rank and `est_cost_usd` values are **routing hints**, not invoices. Use
`inspect_spend`, `threnody gain`, or `inspect_status` for local telemetry
receipts.

## Decision tree: host-native vs delegate

```text
route_task(task)
  │
  ├─ execution_hint.mode == host_native
  │    ├─ low tier  → direct edits or host Task tool (avoid subprocess billing)
  │    ├─ medium     → host Task agent (sonnet-class)
  │    └─ high       → host Task agent or execute_swarm
  │
  └─ execution_hint.mode == delegate
       └─ execute_subtask → cheapest routable backend in delegation_targets
            (Copilot, Codex, Cursor, endpoints, Aider, …)
            NOT claude-code / gemini-cli by default (router-only hosts)
```

Read `execution_hint.economics` on every `route_task` response for
`is_free`, `cost_rank`, `cheapest_path_rationale`, and optional
`why_not_delegate`.

## Typical savings patterns by host

| Host | Prefer | Delegate when |
|------|--------|---------------|
| **GitHub Copilot** | Host edits; `gpt-5-mini` for low tier | Cross-backend work needs Codex/Cursor |
| **Claude Code** | Task tool; router-only — no subprocess to `claude` | Explicit opt-in only (`router_only_allow_execution`) |
| **Gemini CLI** | Task tool; router-only host | Delegate to Copilot/Codex for cross-backend |
| **Codex / Cursor** | Host-native Task + edits | Another CLI is cheaper for low-tier boilerplate |
| **Junie / OpenCode** | Host defaults for their tier pins | Medium/high work via swarm or delegate |

## Multi-CLI arbitrage

When two or more CLIs are installed:

1. Let Threnody classify tier (`route_task`).
2. Follow `execution_hint` — host-native first.
3. For delegation, pick the lowest `cost_rank` in `delegation_targets`.
4. Use free paths where entitled: Copilot `gpt-5-mini`, Gemini flash-lite,
   OpenCode nemotron free tier.

Configure preferences in `config.yaml`:

```yaml
providers:
  preferred_routing_by_caller:
    github-copilot:
      low:
        - provider: github-copilot
        - provider: codex
  usage_windows:
    github-copilot:
      - window: daily
        limit_tokens: 500000
```

## Operator commands

```bash
# MCP or CLI spend snapshot (default window: 7d)
inspect_spend(since="7d")
threnody inspect spend --since 7d
threnody inspect spend --since 30d --by provider

# Table / JSON dashboard (delegated subtask cost telemetry)
threnody gain --since 7d
threnody gain --since 7d --json

# Project readiness includes compact spend_summary
threnody inspect status --project .

# Usage window headroom (when configured in config.yaml)
# inspect_spend / inspect_status expose usage_state: tokens_used, limit, pct, action
```

## Remember cheap patterns (searchable memory)

Store reusable cost wins under predictable keys, then recall them without knowing the exact key:

```python
memory_set("project", "cost_pattern:jwt_auth", "Used low-tier execute_subtask for JWT middleware", project_id=".")
memory_search("jwt auth low tier", project_id=".")
```

FTS5 is local-only (no embeddings). Rebuild the index after manual DB surgery with `threnody db check`.

## Host routing hooks

Claude Code guarded mode installs `shell/threnody-routing-hook.sh` via `./install.sh`. See [HOOKS.md](HOOKS.md).

## Measuring savings

Delegated subtasks record rows in `cost_telemetry` with:

- `est_cost_usd` — estimated spend for the chosen tier/model
- `counterfactual_cost_usd` — estimated spend if routed as high tier
- `savings_usd` — counterfactual minus actual (aggregated in `inspect_spend`)

Host-native work (Task tool, direct edits) does not subprocess through Threnody
and therefore avoids extra delegated-subtask billing — the main savings lever
for subscription-backed hosts.

## Related docs

- [Architecture](ARCHITECTURE.md) — two-path execution model
- [Configuration template](../config.example.yaml) — usage windows and routing
- [Routing accuracy report](ROUTING_ACCURACY.md) — fixture-based tier stats (`python3 -m shared.routing_report --write-docs`)
- [Host routing hooks](HOOKS.md) — Claude PreToolUse enforcement
- [Release limitations](RELEASE_LIMITATIONS.md) — comparison boundaries vs full platforms
