"""Tests for routing accuracy report generator."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.routing_report import build_routing_report, render_routing_accuracy_markdown


def test_build_routing_report_in_test_mode(monkeypatch) -> None:
    monkeypatch.setenv("THRENODY_TEST_MODE", "1")
    report = build_routing_report(filter_categories=["low"])
    assert "summary" in report
    assert "config_hash" in report
    assert report["summary"]["fixture_count"] >= 1
    markdown = render_routing_accuracy_markdown(report)
    assert "Routing accuracy" in markdown
    assert "Executed accuracy" in markdown
