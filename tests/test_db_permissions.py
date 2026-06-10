#!/usr/bin/env python3
"""Regression tests for SQLite file permission hardening."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import Database


def test_database_restricts_permissions_to_owner(tmp_path: Path) -> None:
    db_path = tmp_path / "private-router.db"
    db = Database(db_path=db_path)
    try:
        assert db_path.parent.stat().st_mode & 0o777 == 0o700
        conn = db._connect()
        conn.execute("CREATE TABLE IF NOT EXISTS perms_probe (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        for candidate in (
            db_path,
            Path(f"{db_path}-wal"),
            Path(f"{db_path}-shm"),
        ):
            if candidate.exists():
                assert candidate.stat().st_mode & 0o777 == 0o600
    finally:
        db.close()


def test_database_restricts_custom_owned_parent_directory(tmp_path: Path) -> None:
    custom_parent = tmp_path / "custom-db-dir"
    custom_parent.mkdir(mode=0o755)
    db_path = custom_parent / "router.db"
    db = Database(db_path=db_path)
    try:
        assert custom_parent.stat().st_mode & 0o777 == 0o700
    finally:
        db.close()
