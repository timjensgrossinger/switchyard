"""Tests for standalone PreToolUse routing hook bridge."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.routing_hook import parse_hook_payload, validate_routing_guard


def test_parse_hook_payload_extracts_edit_target() -> None:
    payload = {
        "tool_name": "Edit",
        "cwd": "/tmp/project",
        "tool_input": {"file_path": "src/main.py"},
    }
    fields = parse_hook_payload(payload)
    assert fields["tool_name"] == "Edit"
    assert fields["cwd"] == "/tmp/project"
    assert fields["target_file"] == "src/main.py"
    assert fields["caller"] == "claude-code"


def test_validate_routing_guard_blocks_without_guard(monkeypatch, tmp_path) -> None:
    import mcp_server
    from shared.config import TGsConfig
    from shared.db import Database

    db_path = tmp_path / "hook.db"
    cfg = TGsConfig(db_path=db_path)
    db = Database(db_path=db_path)
    monkeypatch.setattr(
        mcp_server,
        "_ensure_init",
        lambda: (cfg, db, None, None, None),
    )
    monkeypatch.setattr(mcp_server, "_resolve_caller", lambda: "claude-code")

    result = validate_routing_guard(
        caller="claude-code",
        cwd=str(tmp_path),
        target_file="foo.py",
        tool_name="Edit",
    )
    assert result["valid"] is False
    assert "route_task" in str(result.get("reason", "")).lower()


def test_routing_hook_cli_blocks_without_guard(monkeypatch, tmp_path, capsys) -> None:
    import mcp_server
    from shared.config import TGsConfig
    from shared.db import Database

    import shared.routing_hook as routing_hook

    db_path = tmp_path / "hook-cli.db"
    cfg = TGsConfig(db_path=db_path)
    db = Database(db_path=db_path)
    monkeypatch.setattr(
        mcp_server,
        "_ensure_init",
        lambda: (cfg, db, None, None, None),
    )
    monkeypatch.setattr(mcp_server, "_resolve_caller", lambda: "claude-code")

    payload = json.dumps({
        "tool_name": "Write",
        "cwd": str(tmp_path),
        "tool_input": {"file_path": "bar.py"},
    })
    exit_code = routing_hook.main(["validate", "--json", payload])
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert exit_code == 2
    assert body["valid"] is False
