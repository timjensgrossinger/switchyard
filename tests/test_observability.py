#!/usr/bin/env python3
"""Tests for inspect_task observability output."""
from __future__ import annotations

import tempfile
from pathlib import Path

import mcp_server
from shared.config import TGsConfig
from shared.db import Database


def test_task_inspection(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "inspect.db"
        config = TGsConfig(db_path=db_path)
        db = Database(db_path=db_path)
        task_id = "inspect-task-1"

        db.log_agent_result(
            session_id="obs",
            task_hash=task_id,
            agent_id=1,
            tier="low",
            model="gpt-5-mini",
            success=True,
            tokens_used=123,
            provider_name="GitHub Copilot",
            used_fallback=True,
            used_speculation=False,
            reason="execute_subtask",
            version="mcp",
        )

        monkeypatch.setattr(
            mcp_server,
            "_ensure_init",
            lambda: (config, db, None, None, None),
        )

        inspection = mcp_server.inspect_task(task_id)

        assert inspection["task_id"] == task_id
        assert len(inspection["subtasks"]) == 1
        row = inspection["subtasks"][0]
        assert row["provider"] == "GitHub Copilot"
        assert row["model"] == "gpt-5-mini"
        assert row["tier"] == "low"
        assert row["used_fallback"] is True
        assert row["used_speculation"] is False


def test_inspect_status_readiness_summary(monkeypatch) -> None:
    """D-01/D-03/D-16: readiness output should stay compact but still inspectable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "repo"
        project_path.mkdir()
        project_id = str(project_path.resolve())
        db_path = Path(tmpdir) / "status.db"
        config = TGsConfig(db_path=db_path)
        db = Database(db_path=db_path)

        with db.conn() as conn:
            conn.execute(
                """
                INSERT INTO project_settings
                    (project_path, concurrency_limit, budget_hard_cap_tokens,
                     fanout_cap, pending_approval_limit, ts)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (project_id, 6, 1800, 3, 4),
            )
            conn.execute(
                """
                INSERT INTO approval_queue
                    (project_path, draft_fingerprint, draft_name, draft_json,
                     status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    project_id,
                    "draft-obs",
                    "observability-agent",
                    '{"instructions":"## Context\\nObserve."}',
                    "2026-04-10T00:00:00Z",
                    "2026-04-10T00:00:00Z",
                ),
            )

        monkeypatch.setattr(
            mcp_server,
            "_ensure_init",
            lambda: (config, db, None, None, None),
        )
        monkeypatch.setattr(mcp_server, "_active_workspace_root", lambda: Path(tmpdir).resolve())

        inspection = mcp_server.inspect_status(project_id)

        assert inspection["readiness"]["summary"]["pending_approval_count"] == 1
        assert inspection["limits"]["concurrency"] == 6
        assert inspection["limits"]["budget_hard_cap_tokens"] == 1800
        assert inspection["pending_approvals"][0]["name"] == "observability-agent"
        assert "draft" not in inspection["pending_approvals"][0]
