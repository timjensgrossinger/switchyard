# Shell Commands

After installation, restart your shell or `source ~/.zshrc`.

## Quick agent calls

```bash
# Orchestrated multi-agent ensemble (auto-decomposes into waves)
ghc agent "implement JWT auth for the user service"

# Quick single-agent calls (auto-routed to cheapest model)
ghcs "how to list files recursively in python"
ghce "what does awk '{print $2}' do"

# Show the plan without executing
ghc agent -w "refactor the database layer"

# Skip orchestration, run single agent
ghc agent --no-plan "add a docstring to this function"

# Cache stats
ghcw
```

## Operator CLI (`switchyard`)

```bash
# Inspect router / provider status
switchyard inspect status --project .
switchyard inspect status --project . --details
switchyard inspect task execute-1234

# Review and act on pending approvals
switchyard inspect approvals --project .
switchyard inspect approvals approve 12 --project . --operator alice
switchyard inspect approvals reject 12 --project . --operator alice --reason "too broad"
switchyard inspect approvals merge 12 existing-agent-id --project . --operator alice

# Tuning
switchyard tune show --project .
switchyard tune set concurrency_limit 5 --project .
switchyard tune reset concurrency_limit --project .

# Routing eval
switchyard eval run
switchyard eval run --filter low,urgency
switchyard eval baseline

# Provider health
switchyard doctor
switchyard doctor --repair

# Database maintenance
switchyard db check
switchyard db backup
```

## Live monitoring

```bash
switchyard-watch
```

Reads `/tmp/switchyard-status.json` (written by the MCP server on each subtask).
