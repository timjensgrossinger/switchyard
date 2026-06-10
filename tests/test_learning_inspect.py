#!/usr/bin/env python3
"""
Wave-0 scaffolds for Phase 3 learning-inspect surfaces.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_server
import shared.agents as agents_module


def test_inspect_learning_signals() -> None:
    assert hasattr(mcp_server, "inspect_task")
    assert hasattr(mcp_server, "handle_inspect_task")
    assert hasattr(agents_module, "AgentRegistry")
