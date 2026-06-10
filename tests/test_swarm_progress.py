from __future__ import annotations

import json
import tempfile
from pathlib import Path

from shared.config import TGsConfig
from shared.db import Database
from shared.swarm import build_wave_progress_payload
import mcp_server


def test_emit_wave_progress_calls_send_notification(monkeypatch) -> None:
    notifications: list[tuple[str, dict[str, object]]] = []
    refresh_calls: list[tuple[str, Database]] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "swarm-progress.db")
        cfg = TGsConfig(db_path=Path(tmpdir) / "swarm-progress.db")
        try:
            monkeypatch.setattr(
                mcp_server,
                "send_notification",
                lambda method, payload: notifications.append((method, payload)),
            )
            monkeypatch.setattr(
                mcp_server,
                "_ensure_init",
                lambda: (cfg, db, None, None, None),
            )
            monkeypatch.setattr(
                mcp_server,
                "memory_refresh_swarm_state_from_db",
                lambda swarm_id, *, db: refresh_calls.append((swarm_id, db)) or {"swarm_id": swarm_id},
            )

            payload = mcp_server.emit_wave_progress(
                "swarm-abc",
                1,
                0,
                3,
                0,
                round=0,
            )

            expected = build_wave_progress_payload("swarm-abc", 1, 0, 3, 0, round=0)
            assert payload == expected
            assert notifications == [("notifications/progress", expected)]
            assert refresh_calls == [("swarm-abc", db)]
        finally:
            db.close()


def test_emit_wave_progress_post_persist_hook(monkeypatch) -> None:
    notifications: list[tuple[str, dict[str, object]]] = []
    refresh_calls: list[tuple[str, Database]] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "swarm-progress-hook.db"
        db = Database(db_path)
        cfg = TGsConfig(db_path=db_path)
        try:
            monkeypatch.setattr(
                mcp_server,
                "send_notification",
                lambda method, payload: notifications.append((method, payload)),
            )
            monkeypatch.setattr(
                mcp_server,
                "_ensure_init",
                lambda: (cfg, db, None, None, None),
            )
            monkeypatch.setattr(
                mcp_server,
                "memory_refresh_swarm_state_from_db",
                lambda swarm_id, *, db: refresh_calls.append((swarm_id, db)) or {"swarm_id": swarm_id},
            )

            payload = build_wave_progress_payload("swarm-hook", 2, 2, 1, 4, round=0)
            mcp_server._log_swarm_event_safe(db, "swarm-hook", "wave_progress", payload)

            with db.conn() as conn:
                rows = conn.execute(
                    """
                    SELECT event_type, payload
                    FROM swarm_events
                    WHERE swarm_id = ?
                    ORDER BY id
                    """,
                    ("swarm-hook",),
                ).fetchall()

            assert notifications == [("notifications/progress", payload)]
            assert refresh_calls == [("swarm-hook", db)]
            assert [row[0] for row in rows] == ["wave_progress", "progress_emitted"]
            assert json.loads(rows[0][1]) == payload
            assert json.loads(rows[1][1]) == payload
        finally:
            db.close()


def test_emit_wave_progress_rejects_blank_swarm_id(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "swarm-progress-invalid.db")
        cfg = TGsConfig(db_path=Path(tmpdir) / "swarm-progress-invalid.db")
        try:
            monkeypatch.setattr(
                mcp_server,
                "_ensure_init",
                lambda: (cfg, db, None, None, None),
            )
            monkeypatch.setattr(
                mcp_server,
                "send_notification",
                lambda *_args, **_kwargs: None,
            )

            try:
                mcp_server.emit_wave_progress("   ", 1, 0, 1, 0)
                assert False, "expected ValueError"
            except ValueError as exc:
                assert str(exc) == "swarm_id must be a non-empty string"
        finally:
            db.close()


def test_emit_wave_progress_rejects_non_string_swarm_id(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "swarm-progress-non-string.db")
        cfg = TGsConfig(db_path=Path(tmpdir) / "swarm-progress-non-string.db")
        try:
            monkeypatch.setattr(
                mcp_server,
                "_ensure_init",
                lambda: (cfg, db, None, None, None),
            )
            monkeypatch.setattr(
                mcp_server,
                "send_notification",
                lambda *_args, **_kwargs: None,
            )

            try:
                mcp_server.emit_wave_progress(None, 1, 0, 1, 0)  # type: ignore[arg-type]
                assert False, "expected ValueError"
            except ValueError as exc:
                assert str(exc) == "swarm_id must be a non-empty string"
        finally:
            db.close()


def test_log_swarm_event_safe_skips_invalid_wave_progress_payload(monkeypatch) -> None:
    notifications: list[tuple[str, dict[str, object]]] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "swarm-progress-invalid-payload.db"
        db = Database(db_path)
        cfg = TGsConfig(db_path=db_path)
        try:
            monkeypatch.setattr(
                mcp_server,
                "_ensure_init",
                lambda: (cfg, db, None, None, None),
            )
            monkeypatch.setattr(
                mcp_server,
                "send_notification",
                lambda method, payload: notifications.append((method, payload)),
            )

            mcp_server._log_swarm_event_safe(
                db,
                "swarm-invalid",
                "wave_progress",
                {
                    "wave": "not-an-int",
                    "completed_subtasks": 1,
                    "pending_subtasks": 0,
                    "artifacts_produced": 1,
                    "round": 0,
                },
            )

            with db.conn() as conn:
                rows = conn.execute(
                    """
                    SELECT event_type, payload
                    FROM swarm_events
                    WHERE swarm_id = ?
                    ORDER BY id
                    """,
                    ("swarm-invalid",),
                ).fetchall()

            assert notifications == []
            assert [row[0] for row in rows] == ["wave_progress"]
            assert json.loads(rows[0][1])["wave"] == "not-an-int"
        finally:
            db.close()


def test_log_swarm_event_safe_skips_progress_refresh_failure(monkeypatch) -> None:
    notifications: list[tuple[str, dict[str, object]]] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "swarm-progress-refresh-failure.db"
        db = Database(db_path)
        cfg = TGsConfig(db_path=db_path)
        try:
            monkeypatch.setattr(
                mcp_server,
                "_ensure_init",
                lambda: (cfg, db, None, None, None),
            )
            monkeypatch.setattr(
                mcp_server,
                "send_notification",
                lambda method, payload: notifications.append((method, payload)),
            )
            monkeypatch.setattr(
                mcp_server,
                "memory_refresh_swarm_state_from_db",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("refresh failed")),
            )

            payload = build_wave_progress_payload("swarm-refresh", 1, 1, 0, 1, round=0)
            mcp_server._log_swarm_event_safe(db, "swarm-refresh", "wave_progress", payload)

            with db.conn() as conn:
                rows = conn.execute(
                    """
                    SELECT event_type, payload
                    FROM swarm_events
                    WHERE swarm_id = ?
                    ORDER BY id
                    """,
                    ("swarm-refresh",),
                ).fetchall()

            assert notifications == [("notifications/progress", payload)]
            assert [row[0] for row in rows] == ["wave_progress"]
            assert json.loads(rows[0][1]) == payload
        finally:
            db.close()


def test_log_swarm_event_safe_normalizes_swarm_id(monkeypatch) -> None:
    notifications: list[tuple[str, dict[str, object]]] = []
    refresh_calls: list[tuple[str, Database]] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "swarm-progress-normalized.db"
        db = Database(db_path)
        cfg = TGsConfig(db_path=db_path)
        try:
            monkeypatch.setattr(
                mcp_server,
                "_ensure_init",
                lambda: (cfg, db, None, None, None),
            )
            monkeypatch.setattr(
                mcp_server,
                "send_notification",
                lambda method, payload: notifications.append((method, payload)),
            )
            monkeypatch.setattr(
                mcp_server,
                "memory_refresh_swarm_state_from_db",
                lambda swarm_id, *, db: refresh_calls.append((swarm_id, db)) or {"swarm_id": swarm_id},
            )

            payload = build_wave_progress_payload("swarm-normalized", 1, 1, 0, 1, round=0)
            mcp_server._log_swarm_event_safe(db, "  swarm-normalized  ", "wave_progress", payload)

            with db.conn() as conn:
                rows = conn.execute(
                    """
                    SELECT swarm_id, event_type, payload
                    FROM swarm_events
                    WHERE swarm_id = ?
                    ORDER BY id
                    """,
                    ("swarm-normalized",),
                ).fetchall()

            assert notifications == [("notifications/progress", payload)]
            assert refresh_calls == [("swarm-normalized", db)]
            assert [(row[0], row[1]) for row in rows] == [
                ("swarm-normalized", "wave_progress"),
                ("swarm-normalized", "progress_emitted"),
            ]
            assert json.loads(rows[0][2]) == payload
            assert json.loads(rows[1][2]) == payload
        finally:
            db.close()
