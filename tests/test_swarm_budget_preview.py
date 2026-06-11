#!/usr/bin/env python3
"""Tests for the Phase 36 execute_swarm budget preview flow."""
from __future__ import annotations

import hmac
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_server
import shared.db as shared_db_module
from shared.config import TGsConfig
from shared.db import Database


def _stub_init(monkeypatch, tmp_path: Path) -> Database:
    db_path = tmp_path / "swarm-budget-preview.db"
    cfg = TGsConfig(db_path=db_path)
    db = Database(db_path=db_path)
    db._init_schema(db._get_connection())
    monkeypatch.setattr(
        mcp_server,
        "_ensure_init",
        lambda: (cfg, db, None, None, None),
    )
    return db


def _preview_token_hmac(preview_token: str, *, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        preview_token.encode("utf-8"),
        "sha256",
    ).hexdigest()


def test_preview_token_lifecycle(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "preview-token-lifecycle.db"
    db = Database(db_path=db_path)
    db._init_schema(db._get_connection())
    try:
        monkeypatch.setattr(shared_db_module.time, "time", lambda: 100.0)
        db.persist_preview_token("token-hmac-1", "swarm-1", 150.0)
        assert db.lookup_preview_token_swarm_id("token-hmac-1") == "swarm-1"
        assert db.consume_preview_token("token-hmac-1") is True
        assert db.consume_preview_token("token-hmac-1") is False

        db.persist_preview_token("token-hmac-2", "swarm-2", 120.0)
        monkeypatch.setattr(shared_db_module.time, "time", lambda: 121.0)
        assert db.lookup_preview_token_swarm_id("token-hmac-2") is None
        assert db.consume_preview_token("token-hmac-2") is False
    finally:
        db.close()


def test_preview_token_helpers_handle_invalid_inputs(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "preview-token-invalid.db"
    db = Database(db_path=db_path)
    db._init_schema(db._get_connection())
    try:
        assert db.lookup_preview_token_swarm_id("") is None
        assert db.lookup_preview_token_swarm_id(None) is None  # type: ignore[arg-type]
        assert db.consume_preview_token("") is False
        assert db.consume_preview_token(None) is False  # type: ignore[arg-type]
        try:
            db.persist_preview_token("token-hmac", "swarm-1", float("nan"))
        except ValueError as exc:
            assert str(exc) == "expires_ts must be finite"
        else:
            raise AssertionError("expected ValueError for non-finite expires_ts")
    finally:
        db.close()


def test_consume_preview_token_rejects_used_rows(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "preview-token-used-row.db"
    db = Database(db_path=db_path)
    db._init_schema(db._get_connection())
    try:
        monkeypatch.setattr(shared_db_module.time, "time", lambda: 100.0)
        db.persist_preview_token("used-token", "swarm-1", 150.0)
        with db.conn() as conn:
            conn.execute(
                "UPDATE preview_tokens SET used = 1 WHERE token_hmac = ?",
                ("used-token",),
            )

        assert db.consume_preview_token("used-token") is False
        with db.conn() as conn:
            row = conn.execute(
                "SELECT used FROM preview_tokens WHERE token_hmac = ?",
                ("used-token",),
            ).fetchone()
        assert row == (1,)
    finally:
        db.close()


def test_persist_preview_token_does_not_resurrect_used_rows(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "preview-token-no-resurrect.db"
    db = Database(db_path=db_path)
    db._init_schema(db._get_connection())
    try:
        monkeypatch.setattr(shared_db_module.time, "time", lambda: 100.0)
        assert db.persist_preview_token("used-token", "swarm-1", 150.0) is True
        with db.conn() as conn:
            conn.execute(
                "UPDATE preview_tokens SET used = 1 WHERE token_hmac = ?",
                ("used-token",),
            )

        assert db.persist_preview_token("used-token", "swarm-2", 200.0) is False
        assert db.persist_preview_token_with_event(
            "used-token",
            "swarm-3",
            250.0,
            event_type="preview_required",
            payload={"ok": True},
            ts=120.0,
        ) is False

        with db.conn() as conn:
            token_row = conn.execute(
                """
                SELECT swarm_id, expires_ts, used
                FROM preview_tokens
                WHERE token_hmac = ?
                """,
                ("used-token",),
            ).fetchone()
            event_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM swarm_events
                WHERE swarm_id = ?
                """,
                ("swarm-3",),
            ).fetchone()

        assert token_row == ("swarm-1", 150.0, 1)
        assert event_count == (0,)
    finally:
        db.close()


def test_execute_swarm_over_budget_returns_preview(
    monkeypatch,
    tmp_path: Path,
    caplog,
) -> None:
    secret = "preview-secret"
    db = _stub_init(monkeypatch, tmp_path)
    mcp_server._execute_swarm_rate_limit.clear()
    monkeypatch.setenv("PREVIEW_TOKEN_SECRET", secret)
    monkeypatch.setattr(mcp_server, "_resolve_caller", lambda: "preview-caller")
    caplog.set_level(logging.WARNING)

    result = mcp_server.handle_execute_swarm(
        {
            "task": {"id": "preview-task"},
            "max_agents": 3,
            "budget_limit": 1.0,
        }
    )

    assert result["started"] is False
    payload = result["result"]
    preview_token = payload["preview_token"]
    preview_token_hmac = _preview_token_hmac(preview_token, secret=secret)
    assert payload["preview"] is True
    assert payload["expires_in"] == 300
    assert payload["estimated_cost"] > 1.0
    assert payload["budget_delta"] == round(payload["estimated_cost"] - 1.0, 2)
    assert payload["swarm_id"].startswith("swarm-")

    with db.conn() as conn:
        row = conn.execute(
            """
            SELECT token_hmac, swarm_id, used
            FROM preview_tokens
            WHERE token_hmac = ?
            """,
            (preview_token_hmac,),
        ).fetchone()

    assert row is not None
    assert row[0] == preview_token_hmac
    assert row[1] == payload["swarm_id"]
    assert row[2] == 0
    assert preview_token not in caplog.text
    assert db.lookup_preview_token_swarm_id(preview_token_hmac) == payload["swarm_id"]


def test_execute_swarm_confirmation_with_token_starts_execution(
    monkeypatch,
    tmp_path: Path,
) -> None:
    secret = "preview-secret"
    db = _stub_init(monkeypatch, tmp_path)
    mcp_server._execute_swarm_rate_limit.clear()
    monkeypatch.setenv("PREVIEW_TOKEN_SECRET", secret)
    monkeypatch.setattr(mcp_server, "_resolve_caller", lambda: "confirm-caller")

    preview = mcp_server.handle_execute_swarm(
        {
            "task": {"id": "confirm-task"},
            "max_agents": 2,
            "budget_limit": 0.5,
        }
    )

    preview_payload = preview["result"]
    preview_token = preview_payload["preview_token"]
    preview_token_hmac = _preview_token_hmac(preview_token, secret=secret)

    confirmed = mcp_server.handle_execute_swarm(
        {
            "task": {"id": "confirm-task"},
            "max_agents": 2,
            "preview_token": preview_token,
        }
    )

    assert confirmed["started"] is True
    assert confirmed["result"]["confirmed"] is True
    assert confirmed["result"]["swarm_id"] == preview_payload["swarm_id"]
    assert db.consume_preview_token(preview_token_hmac) is False

    with db.conn() as conn:
        row = conn.execute(
            """
            SELECT used
            FROM preview_tokens
            WHERE token_hmac = ?
            """,
            (preview_token_hmac,),
        ).fetchone()

    assert row is None


def test_execute_swarm_confirmation_rejects_mismatched_request(
    monkeypatch,
    tmp_path: Path,
) -> None:
    secret = "preview-secret"
    db = _stub_init(monkeypatch, tmp_path)
    mcp_server._execute_swarm_rate_limit.clear()
    monkeypatch.setenv("PREVIEW_TOKEN_SECRET", secret)
    monkeypatch.setattr(mcp_server, "_resolve_caller", lambda: "confirm-mismatch-caller")

    preview = mcp_server.handle_execute_swarm(
        {
            "task": {"id": "original-task"},
            "max_agents": 2,
            "budget_limit": 0.5,
        }
    )

    preview_payload = preview["result"]
    preview_token = preview_payload["preview_token"]
    preview_token_hmac = _preview_token_hmac(preview_token, secret=secret)

    mismatched = mcp_server.handle_execute_swarm(
        {
            "task": {"id": "different-task"},
            "max_agents": 9,
            "preview_token": preview_token,
        }
    )

    assert mismatched == {
        "error": "invalid_preview_token",
        "details": "preview_token does not match the previewed request",
    }
    assert db.lookup_preview_token_swarm_id(preview_token_hmac) == preview_payload["swarm_id"]

    confirmed = mcp_server.handle_execute_swarm(
        {
            "task": {"id": "original-task"},
            "max_agents": 2,
            "preview_token": preview_token,
        }
    )

    assert confirmed["started"] is True
    assert confirmed["result"]["swarm_id"] == preview_payload["swarm_id"]


def test_preview_token_prunes_stale_rows(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "preview-token-prune.db"
    db = Database(db_path=db_path)
    db._init_schema(db._get_connection())
    try:
        monkeypatch.setattr(shared_db_module.time, "time", lambda: 100.0)
        db.persist_preview_token("used-token", "swarm-used", 150.0)
        assert db.consume_preview_token("used-token") is True

        db.persist_preview_token("expired-token", "swarm-expired", 90.0)
        with db.conn() as conn:
            pre_prune_count = conn.execute(
                "SELECT COUNT(*) FROM preview_tokens"
            ).fetchone()[0]
        assert pre_prune_count == 1

        monkeypatch.setattr(shared_db_module.time, "time", lambda: 200.0)
        db.persist_preview_token("fresh-token", "swarm-fresh", 260.0)

        with db.conn() as conn:
            rows = conn.execute(
                "SELECT token_hmac, swarm_id, used FROM preview_tokens ORDER BY token_hmac"
            ).fetchall()

        assert rows == [("fresh-token", "swarm-fresh", 0)]
    finally:
        db.close()


def test_execute_swarm_over_budget_creates_local_preview_secret(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _stub_init(monkeypatch, tmp_path)
    mcp_server._execute_swarm_rate_limit.clear()
    monkeypatch.delenv("PREVIEW_TOKEN_SECRET", raising=False)
    secret_path = tmp_path / "state" / "preview-token-secret"
    monkeypatch.setattr(
        mcp_server,
        "_EXECUTE_SWARM_PREVIEW_SECRET_FILE",
        secret_path,
    )

    result = mcp_server.handle_execute_swarm(
        {
            "task": {"id": "missing-secret"},
            "budget_limit": 0.5,
        }
    )

    assert result["started"] is False
    assert result["result"]["preview"] is True
    assert secret_path.is_file()
    assert secret_path.read_text(encoding="ascii").strip()
    assert secret_path.stat().st_mode & 0o777 == 0o600


def test_preview_secret_environment_override_does_not_create_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    secret_path = tmp_path / "state" / "preview-token-secret"
    monkeypatch.setattr(
        mcp_server,
        "_EXECUTE_SWARM_PREVIEW_SECRET_FILE",
        secret_path,
    )
    monkeypatch.setenv("PREVIEW_TOKEN_SECRET", "configured-secret")

    assert mcp_server._execute_swarm_preview_secret() == b"configured-secret"
    assert not secret_path.exists()
