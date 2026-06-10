"""Tests for plan 15 — context compression."""
from __future__ import annotations

import pytest

from shared.config import ContextCompressionConfig, TGsConfig
from shared.context import (
    CompressedContext,
    ContextCompressor,
    FileReference,
    build_context_block,
    build_artifact_context_block,
)


# ---------------------------------------------------------------------------
# CompressedContext
# ---------------------------------------------------------------------------

def test_compressed_context_ratio_normal():
    # ratio = 1.0 - (compressed / original) = reduction fraction
    cc = CompressedContext(text="hi", original_len=100, compressed_len=50, layers_applied=["summary_truncation"])
    assert cc.ratio == pytest.approx(0.5)


def test_compressed_context_ratio_no_reduction():
    cc = CompressedContext(text="hi", original_len=100, compressed_len=100, layers_applied=[])
    assert cc.ratio == pytest.approx(0.0)


def test_compressed_context_ratio_zero_original():
    # sentinel: no content = no reduction
    cc = CompressedContext(text="", original_len=0, compressed_len=0, layers_applied=[])
    assert cc.ratio == pytest.approx(0.0)


def test_compressed_context_layers_recorded():
    cc = CompressedContext(text="x", original_len=10, compressed_len=5, layers_applied=["diff_only", "structural_strip"])
    assert "diff_only" in cc.layers_applied
    assert "structural_strip" in cc.layers_applied


# ---------------------------------------------------------------------------
# ContextCompressionConfig
# ---------------------------------------------------------------------------

def test_compression_config_defaults():
    cfg = ContextCompressionConfig()
    assert cfg.enabled is True
    assert cfg.max_context_chars == 8000
    assert cfg.min_ratio_to_log == 0.5


def test_tgs_config_has_context_compression():
    cfg = TGsConfig()
    assert hasattr(cfg, "context_compression")
    assert isinstance(cfg.context_compression, ContextCompressionConfig)


# ---------------------------------------------------------------------------
# ContextCompressor — disabled passthrough
# ---------------------------------------------------------------------------

def test_compressor_disabled_passthrough():
    c = ContextCompressor(enabled=False)
    text = "hello world " * 100
    result = c.compress(text, "full")
    assert result.text == text
    assert result.layers_applied == []
    assert result.ratio == pytest.approx(0.0)  # no reduction applied


def test_compressor_empty_text_passthrough():
    c = ContextCompressor(enabled=True)
    result = c.compress("", "full")
    assert result.text == ""
    assert result.ratio == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Layer 2: truncation (summary_truncation)
# ---------------------------------------------------------------------------

def test_layer2_truncates_long_text():
    # Use small max_context_chars so 700-char text triggers truncation
    c = ContextCompressor(enabled=True, max_context_chars=300)
    long_text = "A" * 700
    result = c.compress(long_text, "output")
    assert len(result.text) < len(long_text)
    assert "omitted" in result.text
    assert "summary_truncation" in result.layers_applied


def test_layer2_short_text_not_truncated():
    c = ContextCompressor(enabled=True, max_context_chars=8000)
    short_text = "small content"
    result = c.compress(short_text, "output")
    assert short_text in result.text
    assert result.compressed_len <= result.original_len


def test_layer2_idempotent():
    """Compressing already-truncated output should not re-truncate it."""
    # max_context_chars=800 > keep_head(400): truncated result (~629 chars) fits
    c1 = ContextCompressor(enabled=True, max_context_chars=800)
    c2 = ContextCompressor(enabled=True, max_context_chars=800)
    long_text = "B" * 900
    first = c1.compress(long_text, "output")
    assert "summary_truncation" in first.layers_applied
    # Already-truncated text is smaller than threshold — no second truncation
    second = c2.compress(first.text, "output")
    assert first.text == second.text


# ---------------------------------------------------------------------------
# Layer 3: deduplication (dedup)
# ---------------------------------------------------------------------------

def test_layer3_dedup_repeated_segment():
    c = ContextCompressor(enabled=True)
    segment = "x" * 100
    # First call: registers hash
    result1 = c.compress(segment, "output")
    # Second call on same instance with same text: deduplicates
    result2 = c.compress(segment, "output")
    assert result2.compressed_len < result1.original_len
    assert "dedup" in result2.layers_applied


def test_layer3_fresh_instance_no_dedup():
    c = ContextCompressor(enabled=True)
    text = "unique content alpha beta gamma delta"
    result = c.compress(text, "output")
    # First call on fresh compressor: no dedup
    assert "dedup" not in result.layers_applied


# ---------------------------------------------------------------------------
# Layer 4: structural stripping (structural_strip)
# ---------------------------------------------------------------------------

def test_layer4_strips_python_comments():
    c = ContextCompressor(enabled=True)
    text = "# this is a comment\nx = 1\n# another comment\ny = 2\n"
    result = c.compress(text, "file")
    assert "x = 1" in result.text
    assert "y = 2" in result.text
    assert "structural_strip" in result.layers_applied


def test_layer4_strips_blank_lines():
    c = ContextCompressor(enabled=True)
    text = "line1\n\n\n\nline2\n\n\nline3\n"
    result = c.compress(text, "file")
    assert "line1" in result.text
    assert "line2" in result.text
    assert result.compressed_len <= result.original_len


def test_layer4_preserves_code_lines():
    c = ContextCompressor(enabled=True)
    code = "def foo():\n    return 42\n"
    result = c.compress(code, "file")
    assert "def foo" in result.text
    assert "return 42" in result.text


def test_layer4_no_change_returns_empty_layers():
    c = ContextCompressor(enabled=True)
    code = "x = 1\ny = 2\nz = 3\n"
    result = c.compress(code, "file")
    # No comments or blanks → structural_strip returns no change → empty layers
    assert "structural_strip" not in result.layers_applied


# ---------------------------------------------------------------------------
# Mode routing
# ---------------------------------------------------------------------------

def test_mode_file_applies_structural_strip():
    c = ContextCompressor(enabled=True)
    text = "# comment\ncode = 1\n# another\nmore = 2\n"
    result = c.compress(text, "file")
    assert "structural_strip" in result.layers_applied


def test_mode_output_applies_truncation_when_large():
    c = ContextCompressor(enabled=True, max_context_chars=300)
    long_text = "Z" * 700
    result = c.compress(long_text, "output")
    assert "summary_truncation" in result.layers_applied


def test_mode_full_applies_both_paths():
    c = ContextCompressor(enabled=True)
    # Comments to trigger strip, run through full mode
    text = "# comment\n" + "code = 1\n" * 10
    result = c.compress(text, "full")
    assert "structural_strip" in result.layers_applied


# ---------------------------------------------------------------------------
# build_context_block integration
# ---------------------------------------------------------------------------

def test_build_context_block_with_compressor(tmp_path):
    src = tmp_path / "foo.py"
    src.write_text("# comment\nx = 1\n# another\ny = 2\n")
    compressor = ContextCompressor(enabled=True)
    ref = FileReference(path=str(src))
    block = build_context_block(
        refs=[ref],
        project_root=str(tmp_path),
        compressor=compressor,
    )
    assert block is not None


def test_build_context_block_without_compressor(tmp_path):
    src = tmp_path / "bar.py"
    src.write_text("x = 1\ny = 2\n")
    ref = FileReference(path=str(src))
    block = build_context_block(
        refs=[ref],
        project_root=str(tmp_path),
    )
    assert block is not None


# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------

def test_cost_telemetry_has_compression_ratio_column(tmp_path):
    from shared.db import Database
    db = Database(tmp_path / "test.db")
    with db.conn() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cost_telemetry)").fetchall()}
    assert "context_compression_ratio" in cols


def test_compression_ratio_nullable(tmp_path):
    from shared.db import Database
    db = Database(tmp_path / "test.db")
    with db.conn() as conn:
        conn.execute(
            "INSERT INTO cost_telemetry"
            " (task_id, tier, provider_id, model, input_tokens, output_tokens,"
            "  est_cost_usd, counterfactual_cost_usd, ts)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("t-compress-1", "low", "copilot", "gpt-5-mini", 100, 50, 0.0, 0.01, 1700000000.0),
        )
    with db.conn() as conn:
        row = conn.execute(
            "SELECT context_compression_ratio FROM cost_telemetry WHERE task_id = ?",
            ("t-compress-1",),
        ).fetchone()
    assert row is not None
    assert row[0] is None


def test_compression_ratio_storable(tmp_path):
    from shared.db import Database
    db = Database(tmp_path / "test.db")
    with db.conn() as conn:
        conn.execute(
            "INSERT INTO cost_telemetry"
            " (task_id, tier, provider_id, model, input_tokens, output_tokens,"
            "  est_cost_usd, counterfactual_cost_usd, ts, context_compression_ratio)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("t-compress-2", "low", "copilot", "gpt-5-mini", 100, 50, 0.0, 0.01, 1700000001.0, 0.62),
        )
    with db.conn() as conn:
        row = conn.execute(
            "SELECT context_compression_ratio FROM cost_telemetry WHERE task_id = ?",
            ("t-compress-2",),
        ).fetchone()
    assert row is not None
    assert abs(row[0] - 0.62) < 1e-6
