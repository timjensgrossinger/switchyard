from __future__ import annotations

"""
shared.style – Phase 5 items 17, 20, 21

Code style learning and output-format adaptation.

Learns per-project coding conventions by analysing the diffs between what an
agent produced and what the user actually kept. Observations are accumulated
with a vote-based strategy (never last-write-wins) and persisted in SQLite.
The resulting StyleProfile can be serialised into a concise prompt preamble
so that future agent outputs already match the project's conventions.

Public surface
--------------
StyleProfile        – dataclass, one per project
analyze_diff()      – standalone heuristic analyser (no LLM calls)
StyleLearner        – observer, profile loader, preamble generator
DecompositionPrefs  – tracks plan granularity preferences
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Literal

from .config import TGsConfig  # noqa: F401  – available for callers
from .db import Database

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

NamingConvention = Literal["snake_case", "camelCase", "mixed", "unknown"]
ReturnStyle      = Literal["early_return", "nested", "mixed", "unknown"]
TypeHintUsage    = Literal["always", "sometimes", "never", "unknown"]
CommentVerbosity = Literal["high", "medium", "low", "unknown"]
ImportStyle      = Literal["absolute", "relative", "mixed", "unknown"]
OutputFormat     = Literal["code_only", "code_and_explanation", "unknown"]
ReviewDepth      = Literal["thorough", "standard", "lean", "unknown"]
Granularity      = Literal["fine", "coarse", "default"]


# ---------------------------------------------------------------------------
# StyleProfile
# ---------------------------------------------------------------------------

@dataclass
class StyleProfile:
    """Per-project style profile derived from observed diffs.

    All fields default to ``"unknown"`` until enough observations accumulate.

    Attributes:
        naming_convention: Identifier casing convention observed in edited code.
        return_style:      Whether the codebase favours early returns or nesting.
        type_hint_usage:   How consistently type annotations are used.
        comment_verbosity: Ratio of comment lines to code lines.
        import_style:      Absolute vs. relative import preference.
        output_format:     Whether the user wants code-only or code+explanation.
        review_depth:      Inferred review thoroughness (promoted by follow-ups).
        sample_count:      Number of diff observations that contributed.
    """

    naming_convention: NamingConvention = "unknown"
    return_style:      ReturnStyle      = "unknown"
    type_hint_usage:   TypeHintUsage    = "unknown"
    comment_verbosity: CommentVerbosity = "unknown"
    import_style:      ImportStyle      = "unknown"
    output_format:     OutputFormat     = "unknown"
    review_depth:      ReviewDepth      = "unknown"
    sample_count:      int              = 0


# ---------------------------------------------------------------------------
# Compiled regex patterns (module-level for performance)
# ---------------------------------------------------------------------------

_RE_SNAKE          = re.compile(r'\b[a-z]+_[a-z][a-z0-9_]*\b')
_RE_CAMEL          = re.compile(r'\b[a-z]+[A-Z][a-zA-Z0-9]+\b')
_RE_FUNC_DEF       = re.compile(r'^\s*(?:async\s+)?def\s+\w+\s*\(', re.MULTILINE)
_RE_RETURN_ANNOT   = re.compile(r'^\s*(?:async\s+)?def\s+\w+\s*\([^)]*\)\s*->', re.MULTILINE)
_RE_COMMENT_LINE   = re.compile(r'^\s*#', re.MULTILINE)
_RE_NONBLANK_LINE  = re.compile(r'^\s*\S', re.MULTILINE)
_RE_REL_IMPORT     = re.compile(r'^\s*from\s+\.', re.MULTILINE)
_RE_ABS_IMPORT     = re.compile(r'^\s*(?:import\s+\w|from\s+[a-zA-Z_]\w+)', re.MULTILINE)
# Markdown / prose signals used to infer output_format.
# _RE_MARKDOWN matches common block-level markdown patterns at line start.
# _RE_PROSE matches plain-English explanatory sentences (50+ chars, starts capital).
_RE_MARKDOWN       = re.compile(
    r'(?m)^(?:#{1,6}\s|>\s|\*\*|```|\[.+\]\(.+\)|-\s{1,4}\w)',
)
_RE_PROSE          = re.compile(r'(?m)^[A-Z][a-zA-Z ,.\':;!?()\'-]{40,}$')


# ---------------------------------------------------------------------------
# Detection helpers (pure functions)
# ---------------------------------------------------------------------------

def _detect_naming(text: str) -> NamingConvention:
    snake = len(_RE_SNAKE.findall(text))
    camel = len(_RE_CAMEL.findall(text))
    total = snake + camel
    if total == 0:
        return "unknown"
    ratio = snake / total
    if ratio > 0.70:
        return "snake_case"
    if ratio < 0.30:
        return "camelCase"
    return "mixed"


def _detect_return_style(text: str) -> ReturnStyle:
    """Classify return style via indentation heuristics.

    *Early-return*: a ``return`` at ≤ 8-space indent that is preceded within
    4 lines by an ``if`` at the same or shallower indent.
    *Nested*: a ``return`` at ≥ 16-space indent.
    """
    lines = text.splitlines()
    early = 0
    deep  = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("return"):
            continue
        indent = len(line) - len(stripped)
        if indent >= 16:
            deep += 1
        elif indent <= 8:
            for j in range(max(0, i - 4), i):
                prev         = lines[j].lstrip()
                prev_indent  = len(lines[j]) - len(prev)
                if prev.startswith("if ") and prev_indent <= indent:
                    early += 1
                    break
    if early == 0 and deep == 0:
        return "unknown"
    if early > deep * 1.5:
        return "early_return"
    if deep > early * 1.5:
        return "nested"
    return "mixed"


def _detect_type_hints(text: str) -> TypeHintUsage:
    total      = len(_RE_FUNC_DEF.findall(text))
    if total == 0:
        return "unknown"
    annotated  = len(_RE_RETURN_ANNOT.findall(text))
    ratio      = annotated / total
    if ratio >= 0.80:
        return "always"
    if ratio >= 0.30:
        return "sometimes"
    return "never"


def _detect_comment_verbosity(text: str) -> CommentVerbosity:
    comments   = len(_RE_COMMENT_LINE.findall(text))
    all_lines  = len(_RE_NONBLANK_LINE.findall(text))
    if all_lines == 0:
        return "unknown"
    ratio = comments / all_lines
    if ratio > 0.15:
        return "high"
    if ratio >= 0.05:
        return "medium"
    return "low"


def _detect_import_style(text: str) -> ImportStyle:
    relative = len(_RE_REL_IMPORT.findall(text))
    absolute = len(_RE_ABS_IMPORT.findall(text))
    if relative == 0 and absolute == 0:
        return "unknown"
    if relative > 0 and absolute > 0:
        return "mixed"
    return "relative" if relative > 0 else "absolute"


def _detect_output_format(original: str, edited: str) -> OutputFormat:
    """Infer whether the user prefers code-only or code+explanation output.

    Counts markdown/prose signals in both versions. If the original had
    significant explanation content that the user stripped out, the preference
    is ``"code_only"``; if explanation was retained it is
    ``"code_and_explanation"``.
    """
    orig_signals = (len(_RE_MARKDOWN.findall(original))
                    + len(_RE_PROSE.findall(original)))
    edit_signals = (len(_RE_MARKDOWN.findall(edited))
                    + len(_RE_PROSE.findall(edited)))

    if orig_signals == 0 and edit_signals == 0:
        return "unknown"
    if orig_signals >= 2 and edit_signals == 0:
        return "code_only"
    if edit_signals >= 2:
        return "code_and_explanation"
    return "unknown"


# ---------------------------------------------------------------------------
# Public diff analyser
# ---------------------------------------------------------------------------

def analyze_diff(original: str, edited: str) -> dict[str, str]:
    """Analyse the diff between agent *original* output and user *edited* version.

    All detection is regex/heuristic-based — no LLM calls are made.

    The *edited* text is the source of truth for naming, return style, type
    hints, comment density, and import style. Both texts are required only for
    the output-format heuristic (detecting stripped explanations).

    Args:
        original: Raw text produced by the agent.
        edited:   The version the user actually kept after editing.

    Returns:
        A dict mapping style dimension names to observed string values.
        Dimensions where detection yields ``"unknown"`` are omitted so that
        sparse diffs do not dilute the accumulated vote profile.
    """
    observations: dict[str, str] = {}

    for key, value in [
        ("naming_convention",  _detect_naming(edited)),
        ("return_style",       _detect_return_style(edited)),
        ("type_hint_usage",    _detect_type_hints(edited)),
        ("comment_verbosity",  _detect_comment_verbosity(edited)),
        ("import_style",       _detect_import_style(edited)),
    ]:
        if value != "unknown":
            observations[key] = value

    fmt = _detect_output_format(original, edited)
    if fmt != "unknown":
        observations["output_format"] = fmt

    log.debug("analyze_diff → %s", observations)
    return observations


# ---------------------------------------------------------------------------
# Vote accumulation helpers
# ---------------------------------------------------------------------------

def _cast_vote(votes: dict[str, dict[str, int]], key: str, value: str) -> None:
    """Increment the vote counter for *value* under *key*."""
    bucket = votes.setdefault(key, {})
    bucket[value] = bucket.get(value, 0) + 1


def _tally(votes: dict[str, dict[str, int]], key: str,
           default: str = "unknown") -> str:
    """Return the plurality winner for *key*, or *default* if no votes exist."""
    bucket = votes.get(key, {})
    if not bucket:
        return default
    return max(bucket, key=lambda k: bucket[k])


def _profile_from_votes(votes: dict[str, dict[str, int]],
                        sample_count: int) -> StyleProfile:
    return StyleProfile(
        naming_convention=_tally(votes, "naming_convention"),  # type: ignore[arg-type]
        return_style=     _tally(votes, "return_style"),        # type: ignore[arg-type]
        type_hint_usage=  _tally(votes, "type_hint_usage"),     # type: ignore[arg-type]
        comment_verbosity=_tally(votes, "comment_verbosity"),   # type: ignore[arg-type]
        import_style=     _tally(votes, "import_style"),        # type: ignore[arg-type]
        output_format=    _tally(votes, "output_format"),       # type: ignore[arg-type]
        review_depth=     _tally(votes, "review_depth"),        # type: ignore[arg-type]
        sample_count=sample_count,
    )


# ---------------------------------------------------------------------------
# Auxiliary table DDL (created by StyleLearner / DecompositionPrefs.__init__)
# ---------------------------------------------------------------------------

_DDL_FOLLOWUP = """
CREATE TABLE IF NOT EXISTS followup_tracking (
    project_path  TEXT NOT NULL,
    followup_type TEXT NOT NULL,
    count         INTEGER DEFAULT 0,
    ts            REAL NOT NULL,
    PRIMARY KEY (project_path, followup_type)
);
"""

_DDL_DECOMP = """
CREATE TABLE IF NOT EXISTS decomp_preferences (
    project_path      TEXT PRIMARY KEY,
    planned_total     INTEGER DEFAULT 0,
    actual_total      INTEGER DEFAULT 0,
    interaction_count INTEGER DEFAULT 0,
    ts                REAL NOT NULL
);
"""


# ---------------------------------------------------------------------------
# StyleLearner
# ---------------------------------------------------------------------------

class StyleLearner:
    """Observes diffs, accumulates per-project StyleProfiles, emits preambles.

    Profiles are stored as JSON vote tallies in the ``style_profiles`` table.
    Each call to :meth:`observe` casts one vote per style dimension; the
    plurality winner is read back via :meth:`get_profile`. This ensures a
    single noisy diff cannot flip the profile (not last-write-wins).
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        with self._db.conn() as conn:
            conn.execute(_DDL_FOLLOWUP)
            conn.execute(_DDL_DECOMP)
        log.debug("StyleLearner ready")

    # ------------------------------------------------------------------
    # Internal persistence
    # ------------------------------------------------------------------

    def _load_raw(self, project_path: str) -> dict:
        """Return the stored raw JSON dict or a clean default."""
        with self._db.conn() as conn:
            row = conn.execute(
                "SELECT profile_json FROM style_profiles WHERE project_path = ?",
                (project_path,),
            ).fetchone()
        if row is None:
            return {"votes": {}, "sample_count": 0}
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError, ValueError):
            log.warning(
                "Corrupt style_profiles entry for %r — resetting", project_path
            )
            return {"votes": {}, "sample_count": 0}

    def _save_raw(self, project_path: str, raw: dict) -> None:
        with self._db.conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO style_profiles (project_path, profile_json, ts) "
                "VALUES (?, ?, ?)",
                (project_path, json.dumps(raw), time.time()),
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def observe(self, project_path: str, original: str, edited: str) -> None:
        """Record a style observation derived from the diff between *original* and *edited*.

        Calls :func:`analyze_diff`, then casts one vote per detected dimension
        into the project's accumulated vote store.

        Args:
            project_path: Absolute path to the project root (used as DB key).
            original:     Agent output before user edits.
            edited:       Text the user actually kept.
        """
        observations = analyze_diff(original, edited)
        if not observations:
            log.debug("observe: no signals detected for %r — diff too sparse", project_path)
            return

        raw          = self._load_raw(project_path)
        votes: dict[str, dict[str, int]] = raw.get("votes", {})
        sample_count: int = raw.get("sample_count", 0) + 1

        for key, value in observations.items():
            _cast_vote(votes, key, value)

        raw["votes"]        = votes
        raw["sample_count"] = sample_count
        self._save_raw(project_path, raw)

        log.info(
            "StyleLearner.observe: %r — sample #%d, dims=%s",
            project_path, sample_count, sorted(observations.keys()),
        )

    def get_profile(self, project_path: str) -> StyleProfile:
        """Return the accumulated StyleProfile for *project_path*.

        All fields are ``"unknown"`` when no observations have been recorded yet.
        """
        raw = self._load_raw(project_path)
        return _profile_from_votes(
            raw.get("votes", {}),
            raw.get("sample_count", 0),
        )

    def get_preamble(self, project_path: str) -> str:
        """Generate a concise prompt preamble from the project's style profile.

        Only non-``"unknown"`` preferences are included. Returns an empty string
        when the profile is entirely unknown (not enough observations yet).

        Returns:
            2–5 lines of plain text suitable for injection at the top of an
            agent system prompt.

        Example output::

            Code style: use snake_case naming, prefer early returns, always include type hints.
            Include thorough inline comments.
            Imports: use absolute import style.
            Output code only — no prose explanations.
            Review thoroughly — cover edge cases and error paths.
        """
        p      = self.get_profile(project_path)
        parts: list[str] = []

        # --- code style line ---
        style_notes: list[str] = []
        if p.naming_convention not in ("unknown", "mixed"):
            style_notes.append(f"use {p.naming_convention} naming")
        if p.return_style not in ("unknown", "mixed"):
            label = ("prefer early returns"
                     if p.return_style == "early_return"
                     else "use nested return style")
            style_notes.append(label)
        if p.type_hint_usage != "unknown":
            _hint = {
                "always":    "always include type hints",
                "sometimes": "include type hints where useful",
                "never":     "omit type hints",
            }.get(p.type_hint_usage, "")
            if _hint:
                style_notes.append(_hint)
        if style_notes:
            parts.append("Code style: " + ", ".join(style_notes) + ".")

        # --- comment verbosity ---
        if p.comment_verbosity != "unknown":
            _verb = {
                "high":   "Include thorough inline comments.",
                "medium": "Include comments on non-obvious logic.",
                "low":    "Minimal comments — let the code speak.",
            }.get(p.comment_verbosity, "")
            if _verb:
                parts.append(_verb)

        # --- import style ---
        if p.import_style not in ("unknown", "mixed"):
            parts.append(f"Imports: use {p.import_style} import style.")

        # --- output format ---
        if p.output_format != "unknown":
            _fmt = {
                "code_only":            "Output code only — no prose explanations.",
                "code_and_explanation": "Include explanations alongside code.",
            }.get(p.output_format, "")
            if _fmt:
                parts.append(_fmt)

        # --- review depth ---
        if p.review_depth != "unknown":
            _depth = {
                "thorough": "Review thoroughly — cover edge cases and error paths.",
                "standard": "Apply standard review depth.",
                "lean":     "Keep review lean — focus on critical issues only.",
            }.get(p.review_depth, "")
            if _depth:
                parts.append(_depth)

        preamble = "\n".join(parts)
        log.debug("get_preamble for %r: %r", project_path, preamble)
        return preamble

    def track_followup(self, project_path: str, followup_type: str) -> None:
        """Record a user follow-up pattern and update the profile when thresholds are met.

        Currently: when the ``"edge_cases"`` follow-up count exceeds **3** for a
        project, ``review_depth`` is promoted to ``"thorough"`` by casting an
        additional vote in the profile store.

        Args:
            project_path:  Absolute path to the project root.
            followup_type: Short label, e.g. ``"add_error_handling"``,
                           ``"edge_cases"``, ``"what_about_X"``.
        """
        with self._db.conn() as conn:
            conn.execute(
                """
                INSERT INTO followup_tracking (project_path, followup_type, count, ts)
                VALUES (?, ?, 1, ?)
                ON CONFLICT (project_path, followup_type)
                DO UPDATE SET count = count + 1, ts = excluded.ts
                """,
                (project_path, followup_type, time.time()),
            )

        with self._db.conn() as conn:
            row = conn.execute(
                "SELECT count FROM followup_tracking "
                "WHERE project_path = ? AND followup_type = 'edge_cases'",
                (project_path,),
            ).fetchone()

        if row and row[0] > 3:
            raw   = self._load_raw(project_path)
            if not raw.get("_review_depth_promoted"):
                votes = raw.get("votes", {})
                _cast_vote(votes, "review_depth", "thorough")
                raw["votes"] = votes
                raw["_review_depth_promoted"] = True
                self._save_raw(project_path, raw)
                log.info(
                    "track_followup: edge_cases=%d → review_depth promoted to 'thorough' for %r",
                    row[0], project_path,
                )


# ---------------------------------------------------------------------------
# DecompositionPrefs
# ---------------------------------------------------------------------------

class DecompositionPrefs:
    """Tracks how users interact with generated plans to infer granularity preference.

    Each call to :meth:`record_plan_interaction` stores the planned vs. actual
    subtask counts. :meth:`get_preferred_granularity` returns ``"coarse"`` when
    the user consistently merges subtasks, ``"fine"`` when they split them, or
    ``"default"`` when there is insufficient data or no clear pattern.

    At least **3 interactions** are required before a preference is asserted.
    """

    _COARSE_RATIO    = 0.80  # actual/planned < this → user merges → coarse
    _FINE_RATIO      = 1.20  # actual/planned > this → user splits → fine
    _MIN_INTERACTIONS = 3

    def __init__(self, db: Database) -> None:
        self._db = db
        with self._db.conn() as conn:
            conn.execute(_DDL_DECOMP)
        log.debug("DecompositionPrefs ready")

    def record_plan_interaction(
        self, project_path: str, planned_count: int, actual_count: int
    ) -> None:
        """Record one plan interaction with its planned vs. actual subtask counts.

        Args:
            project_path:  Absolute path to the project root.
            planned_count: Subtasks the planner originally proposed (must be >= 0).
            actual_count:  Subtasks the user actually executed / accepted (must be >= 0).
        """
        planned_count = max(0, planned_count)
        actual_count = max(0, actual_count)
        with self._db.conn() as conn:
            conn.execute(
                """
                INSERT INTO decomp_preferences
                    (project_path, planned_total, actual_total, interaction_count, ts)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT (project_path) DO UPDATE SET
                    planned_total     = planned_total     + excluded.planned_total,
                    actual_total      = actual_total      + excluded.actual_total,
                    interaction_count = interaction_count + 1,
                    ts                = excluded.ts
                """,
                (project_path, planned_count, actual_count, time.time()),
            )
        log.debug(
            "DecompositionPrefs.record: %r planned=%d actual=%d",
            project_path, planned_count, actual_count,
        )

    def get_preferred_granularity(self, project_path: str) -> Granularity:
        """Return the inferred decomposition granularity for *project_path*.

        Returns:
            ``"fine"``    – user tends to split plans into more subtasks.
            ``"coarse"``  – user tends to merge plans into fewer subtasks.
            ``"default"`` – insufficient data or no clear preference.
        """
        with self._db.conn() as conn:
            row = conn.execute(
                "SELECT planned_total, actual_total, interaction_count "
                "FROM decomp_preferences WHERE project_path = ?",
                (project_path,),
            ).fetchone()

        if row is None:
            return "default"

        planned_total, actual_total, interaction_count = row

        if interaction_count < self._MIN_INTERACTIONS or planned_total == 0:
            return "default"

        ratio = actual_total / planned_total
        if ratio < self._COARSE_RATIO:
            return "coarse"
        if ratio > self._FINE_RATIO:
            return "fine"
        return "default"
