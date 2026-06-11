#!/usr/bin/env bash
# Claude Code PreToolUse hook — validates routing guard without MCP stdio.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="$INSTALL_DIR${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -m shared.routing_hook validate --stdin
