import tempfile
from pathlib import Path

import shared.adaptive as adaptive_module
from shared.config import TGsConfig
from shared.db import Database
from shared.status import build_status_snapshot


def _make_test_db(path: Path) -> Database:
    if path.exists():
        path.unlink()
    return Database(db_path=path)


def _make_test_cfg(path: Path) -> TGsConfig:
    return TGsConfig(db_path=path)


def _make_project_id(base: Path) -> str:
    project_path = base / "repo"
    project_path.mkdir()
    return str(project_path.resolve())


def _seed_adaptive_row(db: Database) -> None:
    with db.conn() as conn:
        conn.execute(
            """
            INSERT INTO adaptive_thresholds
                (band, version, tier, success_ema, sample_count, ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("low", "test", "low", 0.91, 7, 0.0),
        )


def _seed_telemetry_row(db: Database) -> None:
    db.write_telemetry_row(
        session_id="test",
        task_hash="task-1",
        agent_id=0,
        tier="system",
        model="test-model",
        artifact_publish_count=2,
        artifact_consume_count=3,
        coordinator_amendment_count=1,
        urgency_score=0.42,
        parse_diagnostics='{"note":"ok"}',
    )


def test_snapshot_includes_adaptive_thresholds():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "status.db"
        db = _make_test_db(db_path)
        cfg = _make_test_cfg(db_path)
        project_id = _make_project_id(base)

        snapshot = build_status_snapshot(cfg, db, project_id)

        assert "adaptive_thresholds" in snapshot
        assert "rework_summary" in snapshot


def test_adaptive_summary_initialized():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "status.db"
        db = _make_test_db(db_path)
        cfg = _make_test_cfg(db_path)
        project_id = _make_project_id(base)
        _seed_adaptive_row(db)

        snapshot = build_status_snapshot(cfg, db, project_id)

        adaptive = snapshot["adaptive_thresholds"]
        assert adaptive["initialized"] is True
        assert adaptive["band_count"] == 1
        assert adaptive["total_samples"] == 7
        assert isinstance(adaptive["bands"], list)


def test_default_limits_report_unlimited_concurrency_and_fanout():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "status.db"
        db = _make_test_db(db_path)
        cfg = _make_test_cfg(db_path)
        project_id = _make_project_id(base)

        snapshot = build_status_snapshot(cfg, db, project_id)

        assert snapshot["limits"]["concurrency"] is None
        assert snapshot["limits"]["fanout_cap"] is None
        assert "fanout" in snapshot["readiness"]["enabled_features"]


def test_adaptive_summary_empty():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "status.db"
        db = _make_test_db(db_path)
        cfg = _make_test_cfg(db_path)
        project_id = _make_project_id(base)

        snapshot = build_status_snapshot(cfg, db, project_id)

        assert snapshot["adaptive_thresholds"] == {"initialized": False, "bands": []}


def test_adaptive_summary_missing_sample_count_defaults_to_zero(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "status.db"
        db = _make_test_db(db_path)
        cfg = _make_test_cfg(db_path)
        project_id = _make_project_id(base)

        monkeypatch.setattr(
            adaptive_module,
            "get_band_stats",
            lambda _db: [{"band": "low", "tier": "low", "version": "test", "ts": 0.0}],
        )

        snapshot = build_status_snapshot(cfg, db, project_id)

        assert snapshot["adaptive_thresholds"]["initialized"] is True
        assert snapshot["adaptive_thresholds"]["band_count"] == 1
        assert snapshot["adaptive_thresholds"]["total_samples"] == 0


def test_adaptive_summary_none_sample_count_defaults_to_zero(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "status.db"
        db = _make_test_db(db_path)
        cfg = _make_test_cfg(db_path)
        project_id = _make_project_id(base)

        monkeypatch.setattr(
            adaptive_module,
            "get_band_stats",
            lambda _db: [{
                "band": "low",
                "tier": "low",
                "version": "test",
                "ts": 0.0,
                "sample_count": None,
            }],
        )

        snapshot = build_status_snapshot(cfg, db, project_id)

        assert snapshot["adaptive_thresholds"]["initialized"] is True
        assert snapshot["adaptive_thresholds"]["band_count"] == 1
        assert snapshot["adaptive_thresholds"]["total_samples"] == 0


def test_rework_summary_fields():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "status.db"
        db = _make_test_db(db_path)
        cfg = _make_test_cfg(db_path)
        project_id = _make_project_id(base)
        db.log_rework("session-1", 1, 2, "mcp_server.py")

        snapshot = build_status_snapshot(cfg, db, project_id)

        rework = snapshot["rework_summary"]
        assert rework["initialized"] is True
        assert rework["scope"] == "global"
        assert isinstance(rework["recent_rework_count"], int)


def test_rework_summary_counts_global():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "status.db"
        db = _make_test_db(db_path)
        cfg = _make_test_cfg(db_path)
        project_id = _make_project_id(base)
        db.log_rework("session-1", 1, 2, "mcp_server.py")
        db.log_rework("session-2", 2, 3, "shared/db.py")

        snapshot = build_status_snapshot(cfg, db, project_id)

        assert snapshot["rework_summary"]["scope"] == "global"
        assert snapshot["rework_summary"]["recent_rework_count"] == 2


def test_empty_db_returns_defaults():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "status.db"
        db = _make_test_db(db_path)
        cfg = _make_test_cfg(db_path)
        project_id = _make_project_id(base)

        snapshot = build_status_snapshot(cfg, db, project_id)

        assert snapshot["pending_approvals"] == []
        assert snapshot["recent_summary"]["artifact_publish_count"] == 0
        assert snapshot["adaptive_thresholds"] == {"initialized": False, "bands": []}
        assert snapshot["rework_summary"] == {
            "initialized": False,
            "scope": "global",
            "recent_rework_count": 0,
        }


def test_missing_db_returns_defaults():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "missing" / "status.db"
        assert not db_path.exists()
        db = Database(db_path=db_path)
        cfg = _make_test_cfg(db_path)

        snapshot = build_status_snapshot(cfg, db, "")

        assert snapshot["adaptive_thresholds"] == {"initialized": False, "bands": []}
        assert snapshot["rework_summary"]["recent_rework_count"] == 0


def test_partial_db_adaptive_fallback():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "status.db"
        db = _make_test_db(db_path)
        cfg = _make_test_cfg(db_path)
        project_id = _make_project_id(base)
        _seed_telemetry_row(db)

        snapshot = build_status_snapshot(cfg, db, project_id)

        assert snapshot["recent_summary"]["artifact_publish_count"] == 2
        assert snapshot["adaptive_thresholds"] == {"initialized": False, "bands": []}


def test_existing_keys_preserved():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "status.db"
        db = _make_test_db(db_path)
        cfg = _make_test_cfg(db_path)
        project_id = _make_project_id(base)

        snapshot = build_status_snapshot(cfg, db, project_id)

        assert set(snapshot) >= {
            "readiness",
            "limits",
            "pending_approvals",
            "recent_summary",
            "explainability_link",
            "project_id",
        }


def test_conservative_defaults_when_no_project_id():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "status.db"
        db = _make_test_db(db_path)
        cfg = _make_test_cfg(db_path)

        snapshot = build_status_snapshot(cfg, db, "")

        assert snapshot["readiness"]["summary"]["conservative_defaults"] is True


def test_conservative_defaults_false_when_project_set():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "status.db"
        db = _make_test_db(db_path)
        cfg = _make_test_cfg(db_path)
        project_id = _make_project_id(base)

        snapshot = build_status_snapshot(cfg, db, project_id)

        assert snapshot["readiness"]["summary"]["conservative_defaults"] is False


def test_recent_summary_truncates_json_note_payload():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "status.db"
        db = _make_test_db(db_path)
        cfg = _make_test_cfg(db_path)
        project_id = _make_project_id(base)
        long_note = "x" * 500

        db.write_telemetry_row(
            session_id="test",
            task_hash="task-2",
            agent_id=0,
            tier="system",
            model="test-model",
            parse_diagnostics=f'{{"note":"{long_note}"}}',
        )

        snapshot = build_status_snapshot(cfg, db, project_id)

        assert snapshot["recent_summary"]["latest_notable_event"] == long_note[:400]


def test_recent_summary_stringifies_non_string_json_note_payload():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        db_path = base / "status.db"
        db = _make_test_db(db_path)
        cfg = _make_test_cfg(db_path)
        project_id = _make_project_id(base)

        db.write_telemetry_row(
            session_id="test",
            task_hash="task-3",
            agent_id=0,
            tier="system",
            model="test-model",
            parse_diagnostics='{"note":42}',
        )

        snapshot = build_status_snapshot(cfg, db, project_id)

        assert snapshot["recent_summary"]["latest_notable_event"] == "42"
