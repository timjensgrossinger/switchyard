"""
Edit-block protocol for surgical file edits (Phase 2).

Providers return a sequence of SEARCH/REPLACE blocks:

    <<<<<<< SEARCH
    <exact lines to find>
    =======
    <replacement lines>
    >>>>>>> REPLACE

Multiple blocks are supported.  Leading/trailing blank lines inside a block are
stripped before matching; internal whitespace is normalised for the search pass
so that minor indentation drift does not cause false negatives.

Public API
----------
parse_edit_blocks(text)          -> list[tuple[str, str]]
apply_edit_blocks(content, blocks) -> str
parse_and_apply(target_path, text) -> tuple[str, int, int]
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_FENCE_OPEN  = re.compile(r"^<{7}\s*SEARCH\s*$", re.IGNORECASE)
_FENCE_SEP   = re.compile(r"^={7}\s*$")
_FENCE_CLOSE = re.compile(r"^>{7}\s*REPLACE\s*$", re.IGNORECASE)


def parse_edit_blocks(text: str) -> list[tuple[str, str]]:
    """Parse all SEARCH/REPLACE blocks from *text*.

    Returns a list of (search_text, replace_text) pairs in document order.
    Raises ValueError if the block structure is malformed.
    """
    blocks: list[tuple[str, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if _FENCE_OPEN.match(lines[i]):
            search_lines: list[str] = []
            i += 1
            while i < len(lines) and not _FENCE_SEP.match(lines[i]):
                search_lines.append(lines[i])
                i += 1
            if i >= len(lines):
                raise ValueError("Unclosed SEARCH block — missing '=======' separator")
            i += 1  # skip =======
            replace_lines: list[str] = []
            while i < len(lines) and not _FENCE_CLOSE.match(lines[i]):
                replace_lines.append(lines[i])
                i += 1
            if i >= len(lines):
                raise ValueError("Unclosed REPLACE block — missing '>>>>>>> REPLACE' closer")
            i += 1  # skip >>>>>>> REPLACE
            blocks.append(("\n".join(search_lines), "\n".join(replace_lines)))
        else:
            i += 1
    return blocks


_BOX_TO_ASCII = str.maketrans({
    '─': '-', '━': '-', '═': '=', '╌': '-', '╍': '-',
    '│': '|', '┃': '|', '║': '|', '╎': '|', '╏': '|',
    '┌': '+', '┐': '+', '└': '+', '┘': '+',
    '├': '+', '┤': '+', '┬': '+', '┴': '+', '┼': '+',
    '╔': '+', '╗': '+', '╚': '+', '╝': '+',
    '​': '', '‌': '', '‍': '', '﻿': '',
})

def _normalise(text: str) -> str:
    """Strip, unicode-normalize, and collapse internal whitespace for fuzzy matching."""
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_BOX_TO_ASCII)
    return re.sub(r"[ \t]+", " ", text.strip())


def apply_edit_blocks(
    content: str,
    blocks: list[tuple[str, str]],
) -> tuple[str, int, int]:
    """Apply *blocks* to *content* in order.

    Returns (new_content, lines_added, lines_removed).
    Raises ValueError if any SEARCH pattern cannot be matched.
    """
    lines_added = 0
    lines_removed = 0
    for search, replace in blocks:
        search_stripped = search.rstrip("\n")
        replace_stripped = replace.rstrip("\n")

        if search_stripped == "":
            # Empty search means append at end of file.
            old_lines = 0
            new_lines = replace_stripped.count("\n") + 1 if replace_stripped else 0
            content = content.rstrip("\n") + ("\n" + replace_stripped if replace_stripped else "")
            lines_added += new_lines
            continue

        # Exact match first.
        if search_stripped in content:
            old_line_count = search_stripped.count("\n") + 1
            new_line_count = replace_stripped.count("\n") + 1 if replace_stripped else 0
            content = content.replace(search_stripped, replace_stripped, 1)
            lines_removed += old_line_count
            lines_added += new_line_count
            continue

        # Fuzzy match: normalise whitespace on each line and try again.
        norm_search = _normalise(search_stripped)
        content_lines = content.splitlines(keepends=True)
        search_norm_lines = [_normalise(l) for l in search_stripped.splitlines()]

        matched_start = -1
        matched_end = -1
        for j in range(len(content_lines)):
            if j + len(search_norm_lines) > len(content_lines):
                break
            window = [_normalise(content_lines[j + k].rstrip("\n")) for k in range(len(search_norm_lines))]
            if window == search_norm_lines:
                matched_start = j
                matched_end = j + len(search_norm_lines)
                break

        if matched_start == -1:
            raise ValueError(
                f"SEARCH block not found in file.\n"
                f"Pattern (first 120 chars): {search_stripped[:120]!r}"
            )

        old_lines = matched_end - matched_start
        new_chunk = replace_stripped + "\n" if replace_stripped else ""
        new_chunk_lines = new_chunk.splitlines(keepends=True)
        content_lines[matched_start:matched_end] = new_chunk_lines
        lines_removed += old_lines
        lines_added += len(new_chunk_lines)
        content = "".join(content_lines)

    return content, lines_added, lines_removed


def parse_and_apply(target_path: Path, text: str) -> tuple[str, int, int]:
    """Parse edit blocks from *text* and apply them to *target_path*.

    Returns (new_content, lines_added, lines_removed).
    Does NOT write to disk — caller is responsible for writing.
    Raises ValueError on parse or apply failure.
    """
    blocks = parse_edit_blocks(text)
    if not blocks:
        raise ValueError("No SEARCH/REPLACE blocks found in model output")
    current = target_path.read_text(encoding="utf-8")
    return apply_edit_blocks(current, blocks)
