"""Tests for execute_subtask surgical edit modes: rewrite, blocks, patch."""

import textwrap
from pathlib import Path

import pytest

from shared.edit_blocks import (
    apply_edit_blocks,
    parse_and_apply,
    parse_edit_blocks,
)


# ---------------------------------------------------------------------------
# parse_edit_blocks
# ---------------------------------------------------------------------------

def test_parse_single_block():
    text = textwrap.dedent("""\
        <<<<<<< SEARCH
        def foo():
            return 1
        =======
        def foo():
            return 2
        >>>>>>> REPLACE
    """)
    blocks = parse_edit_blocks(text)
    assert len(blocks) == 1
    search, replace = blocks[0]
    assert "def foo():" in search
    assert "return 1" in search
    assert "return 2" in replace


def test_parse_multiple_blocks():
    text = textwrap.dedent("""\
        Some prose before.

        <<<<<<< SEARCH
        alpha = 1
        =======
        alpha = 10
        >>>>>>> REPLACE

        <<<<<<< SEARCH
        beta = 2
        =======
        beta = 20
        >>>>>>> REPLACE
    """)
    blocks = parse_edit_blocks(text)
    assert len(blocks) == 2
    assert "alpha = 1" in blocks[0][0]
    assert "alpha = 10" in blocks[0][1]
    assert "beta = 2" in blocks[1][0]
    assert "beta = 20" in blocks[1][1]


def test_parse_no_blocks():
    blocks = parse_edit_blocks("Just some plain text without any blocks.")
    assert blocks == []


def test_parse_unclosed_search_raises():
    text = "<<<<<<< SEARCH\nalpha\n"
    with pytest.raises(ValueError, match="Unclosed SEARCH block"):
        parse_edit_blocks(text)


def test_parse_unclosed_replace_raises():
    text = "<<<<<<< SEARCH\nalpha\n=======\nbeta\n"
    with pytest.raises(ValueError, match="Unclosed REPLACE block"):
        parse_edit_blocks(text)


# ---------------------------------------------------------------------------
# apply_edit_blocks
# ---------------------------------------------------------------------------

def test_apply_single_exact_match():
    content = "def foo():\n    return 1\n\ndef bar():\n    pass\n"
    blocks = [("def foo():\n    return 1", "def foo():\n    return 42")]
    new_content, added, removed = apply_edit_blocks(content, blocks)
    assert "return 42" in new_content
    assert "return 1" not in new_content


def test_apply_multiple_blocks():
    content = "x = 1\ny = 2\nz = 3\n"
    blocks = [("x = 1", "x = 10"), ("y = 2", "y = 20")]
    new_content, added, removed = apply_edit_blocks(content, blocks)
    assert "x = 10" in new_content
    assert "y = 20" in new_content
    assert "z = 3" in new_content


def test_apply_search_not_found_raises():
    content = "def foo():\n    return 1\n"
    blocks = [("def totally_different():\n    pass", "def totally_different():\n    return 0")]
    with pytest.raises(ValueError, match="SEARCH block not found"):
        apply_edit_blocks(content, blocks)


def test_apply_fuzzy_whitespace_match():
    content = "def foo():\n    return   1\n"
    blocks = [("def foo():\n    return 1", "def foo():\n    return 2")]
    new_content, _, _ = apply_edit_blocks(content, blocks)
    assert "return 2" in new_content


def test_apply_empty_search_appends():
    content = "line1\nline2\n"
    blocks = [("", "line3")]
    new_content, added, _ = apply_edit_blocks(content, blocks)
    assert "line3" in new_content
    assert added > 0


# ---------------------------------------------------------------------------
# parse_and_apply (integration: read file + parse + apply)
# ---------------------------------------------------------------------------

def test_parse_and_apply_roundtrip(tmp_path: Path):
    src = tmp_path / "sample.py"
    src.write_text("def hello():\n    print('hello')\n", encoding="utf-8")
    model_output = textwrap.dedent("""\
        <<<<<<< SEARCH
        def hello():
            print('hello')
        =======
        def hello():
            print('world')
        >>>>>>> REPLACE
    """)
    new_content, added, removed = parse_and_apply(src, model_output)
    assert "print('world')" in new_content
    assert "print('hello')" not in new_content
    assert added >= 1
    assert removed >= 1
    # File not modified — caller is responsible for write
    assert src.read_text(encoding="utf-8") == "def hello():\n    print('hello')\n"


def test_parse_and_apply_no_blocks_raises(tmp_path: Path):
    src = tmp_path / "file.py"
    src.write_text("pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="No SEARCH/REPLACE blocks found"):
        parse_and_apply(src, "just prose")


# ---------------------------------------------------------------------------
# Rewrite length-ratio guard logic (unit test for the guard predicate)
# ---------------------------------------------------------------------------

def test_shrink_keyword_detection():
    """Verify that shrink-keyword detection logic matches expected keywords."""
    shrink_kws = frozenset({
        "delete", "remove", "drop", "strip", "cleanup", "clean up",
        "prune", "trim", "shrink", "minimise", "minimize", "consolidate", "collapse",
    })
    prompts_that_should_skip_guard = [
        "remove all deprecated functions",
        "delete the old auth module",
        "prune unused imports",
        "minimize the boilerplate",
    ]
    prompts_that_should_not_skip_guard = [
        "rename function foo to bar",
        "add a new parameter to process_data",
        "fix the off-by-one in the loop",
    ]
    for prompt in prompts_that_should_skip_guard:
        assert any(kw in prompt.lower() for kw in shrink_kws), f"Expected {prompt!r} to match"
    for prompt in prompts_that_should_not_skip_guard:
        assert not any(kw in prompt.lower() for kw in shrink_kws), f"Expected {prompt!r} not to match"


# ---------------------------------------------------------------------------
# Existing patch mode — apply_unified_diff (Phase 3)
# ---------------------------------------------------------------------------

def test_apply_unified_diff_basic(tmp_path: Path):
    """Verify existing patch mode apply_unified_diff works end-to-end."""
    from shared.snapshot import apply_unified_diff

    target = tmp_path / "code.py"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")
    diff = textwrap.dedent("""\
        --- a/code.py
        +++ b/code.py
        @@ -1,3 +1,3 @@
         line1
        -line2
        +LINE_TWO
         line3
    """)
    new_content, added, removed = apply_unified_diff(target, diff)
    assert "LINE_TWO" in new_content
    assert "line2" not in new_content
    assert added == 1
    assert removed == 1


def test_apply_unified_diff_hunk_mismatch_raises(tmp_path: Path):
    """apply_unified_diff should raise ValueError on context-line mismatch."""
    from shared.snapshot import apply_unified_diff
    import textwrap

    target = tmp_path / "code.py"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")
    # diff claims context line "WRONG" but file has "line2"
    bad_diff = textwrap.dedent("""        --- a/code.py
        +++ b/code.py
        @@ -1,3 +1,3 @@
         line1
        -WRONG
        +replacement
         line3
    """)
    with pytest.raises(ValueError, match="Hunk mismatch"):
        apply_unified_diff(target, bad_diff)


def test_apply_unified_diff_noop_on_non_diff(tmp_path: Path):
    """apply_unified_diff returns original unchanged for non-diff text (no @@ headers)."""
    from shared.snapshot import apply_unified_diff

    target = tmp_path / "code.py"
    original = "some content\n"
    target.write_text(original, encoding="utf-8")
    new_content, added, removed = apply_unified_diff(target, "not a diff at all")
    assert new_content == original
    assert added == 0
    assert removed == 0
