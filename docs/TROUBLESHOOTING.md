# Troubleshooting

## MCP server not found

Check registration in your host shell config:

```bash
cat ~/.copilot/mcp-config.json   # Copilot
cat ~/.claude.json                # Claude Code
cat ~/.gemini/settings.json       # Gemini CLI
cat ./opencode.json              # OpenCode (project-local)
cat ~/.codex/config.toml          # Codex
cat ~/.cursor/mcp.json            # Cursor
cat ~/.junie/mcp/mcp.json         # Junie
```

All should contain a `Switchyard` entry pointing to `python3 ~/.local/lib/switchyard/mcp_server.py`.

## python3 not found

Switchyard requires Python 3.10+. Ensure `python3` is on your PATH.

## Models not available

- **GitHub Copilot**: Requires a Copilot subscription.
- **Gemini CLI**: Free tier includes flash models. Run `gemini` → `/model`.
- **Claude Code**: Requires Claude Pro or Team.
- **OpenCode**: Auto-routes low-tier only by default.

Use `switchyard inspect status --project . --details` or MCP `check_providers()` for live diagnostics.

## Single CLI — does routing still help?

Yes. Even with one CLI, Switchyard:
- Picks the cheapest model for the task tier
- Caches plans to skip repeated decomposition
- Shows agent transparency for every wave

## switchyard-watch shows nothing

The MCP server starts when your AI tool connects. The status file at `/tmp/switchyard-status.json` is written on each subtask execution.

## Uninstall

```bash
~/.local/lib/switchyard/uninstall.sh
~/.local/lib/switchyard/uninstall.sh --purge-data
```

Project-local OpenCode registrations must be removed manually from each project's `opencode.json`.
