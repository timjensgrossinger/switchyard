from __future__ import annotations

"""Tests for shared.learning_report."""

import json
import sys
import time
import unittest.mock
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.db import Database
from shared.learning_report import build_learning_report, render_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed(db: Database) -> None:
    """Insert minimal fixture rows covering all 5 report sections."""
    now = time.time()
    week_ago = now - 86400 * 3

    with db.conn() as conn:
        # subtask_patterns
        conn.execute(
            "INSERT OR IGNORE INTO subtask_patterns "
            "(pattern_hash, pattern_desc, occurrence_count, last_seen, eval_quality) "
            "VALUES (?, ?, ?, ?, ?)",
            ("hash1", "desc1", 5, week_ago, 0.9),
        )
        conn.execute(
            "INSERT OR IGNORE INTO subtask_patterns "
            "(pattern_hash, pattern_desc, occurrence_count, last_seen, eval_quality) "
            "VALUES (?, ?, ?, ?, ?)",
            ("hash2", "desc2", 3, week_ago, 0.7),
        )

        # agent_definitions with promotion_state
        conn.execute(
            "INSERT OR IGNORE INTO agent_definitions "
            "(pattern_hash, pattern_desc, definition, match_count, ts, promotion_state) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("hash1", "desc1", "{}", 10, now, "active"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO agent_definitions "
            "(pattern_hash, pattern_desc, definition, match_count, ts, promotion_state) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("hash2", "desc2", "{}", 2, now, "draft"),
        )

        # approval_queue
        conn.execute(
            "INSERT OR IGNORE INTO approval_queue "
            "(project_path, draft_fingerprint, draft_name, draft_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("/proj", "fp1", "agent1", "{}", "pending", str(now), str(now)),
        )

        # routing_outcomes
        for i, (outcome, tier) in enumerate([("accepted", "low"), ("accepted", "low"), ("reworked", "medium")]):
            conn.execute(
                "INSERT INTO routing_outcomes "
                "(task_id, current_outcome, recorded_at, tier, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"t-{outcome}-{tier}-{i}", outcome, week_ago, tier, week_ago),
            )

        # telemetry
        for i in range(4):
            conn.execute(
                "INSERT INTO telemetry (tier, success, rework_count, ts) VALUES (?, ?, ?, ?)",
                ("low", 1 if i < 3 else 0, i % 2, week_ago + i * 100),
            )

        # adaptive_thresholds
        conn.execute(
            "INSERT OR IGNORE INTO adaptive_thresholds "
            "(band, version, tier, success_ema, sample_count, ts) VALUES (?, ?, ?, ?, ?, ?)",
            ("0.5-0.6", 1, "low", 0.85, 10, now),
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_all_sections_present(temp_db_fixture: Database) -> None:
    _seed(temp_db_fixture)
    report = build_learning_report(temp_db_fixture, window_days=7)
    assert "patterns" in report
    assert "agents" in report
    assert "routing_outcomes" in report
    assert "rework" in report
    assert "adaptive_bands" in report


def test_window_filter_excludes_old_rows(temp_db_fixture: Database) -> None:
    now = time.time()
    old = now - 86400 * 30  # 30 days ago
    with temp_db_fixture.conn() as conn:
        conn.execute(
            "INSERT INTO routing_outcomes "
            "(task_id, current_outcome, recorded_at, tier, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("old-task", "accepted", old, "low", old),
        )
    report = build_learning_report(temp_db_fixture, window_days=7)
    total = report.get("routing_outcomes", {}).get("total", 0)
    assert total == 0


def test_window_zero_includes_all(temp_db_fixture: Database) -> None:
    _seed(temp_db_fixture)
    report = build_learning_report(temp_db_fixture, window_days=0)
    # outcome rows seeded are 3
    assert report.get("routing_outcomes", {}).get("total", 0) == 3


def test_json_output_roundtrips(temp_db_fixture: Database) -> None:
    _seed(temp_db_fixture)
    report = build_learning_report(temp_db_fixture, window_days=7)
    rendered = render_report(report, fmt="json")
    parsed = json.loads(rendered)
    assert "patterns" in parsed


def test_markdown_has_headers(temp_db_fixture: Database) -> None:
    _seed(temp_db_fixture)
    report = build_learning_report(temp_db_fixture, window_days=7)
    md = render_report(report, fmt="markdown")
    assert "## Patterns" in md
    assert "## Agents" in md
    assert "## Routing Outcomes" in md


def test_plain_text_without_rich(temp_db_fixture: Database) -> None:
    _seed(temp_db_fixture)
    report = build_learning_report(temp_db_fixture, window_days=7)
    import shared.learning_report as lr_mod
    with unittest.mock.patch.object(lr_mod, "HAS_RICH", False):
        rendered = render_report(report, fmt="text")
    assert "PATTERNS EMERGED" in rendered
    assert "AGENTS" in rendered
    assert "ROUTING OUTCOMES" in rendered


def test_empty_db_no_error(temp_db_fixture: Database) -> None:
    """Empty DB must produce a valid report dict, not raise."""
    report = build_learning_report(temp_db_fixture, window_days=7)
    assert report.get("patterns", {}).get("total", 0) == 0
    assert report.get("agents", {}).get("total_active", 0) == 0


def test_agent_state_counts(temp_db_fixture: Database) -> None:
    _seed(temp_db_fixture)
    report = build_learning_report(temp_db_fixture, window_days=7)
    by_state = report.get("agents", {}).get("by_state", {})
    assert by_state.get("active", 0) >= 1
    assert by_state.get("draft", 0) >= 1
    assert report.get("agents", {}).get("pending_approvals", 0) == 1
