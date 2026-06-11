#!/usr/bin/env python3
"""
Tests for the routing exceptions / bypass system.

Covers:
  - DB CRUD: routing_exception_add / _remove / _list
  - Config: RoutingExceptions dataclass loading from YAML
  - MCP: _is_routing_exception_exempt() for all 6 types
  - MCP handlers: handle_routing_exception_add/remove/list
  - Integration: _validate_routing_guard short-circuits on exempt match
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.db import Database
from shared.config import (
    DEFAULT_ROUTING_EXCEPTION_FILETYPES,
    DEFAULT_ROUTING_EXCEPTION_PATHS,
    TGsConfig,
    RoutingExceptions,
)
import mcp_server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmpdir: str) -> Database:
    return Database(db_path=Path(tmpdir) / "exceptions.db")


def _cfg_with_exceptions(**kwargs) -> TGsConfig:
    """Return a TGsConfig with static routing_exceptions populated from kwargs."""
    cfg = TGsConfig()
    cfg.routing_exceptions = RoutingExceptions(**kwargs)
    return cfg


# ---------------------------------------------------------------------------
# 1. DB CRUD
# ---------------------------------------------------------------------------

class TestRoutingExceptionCRUD:
    def test_add_and_list(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("skill", "auto-time", "test note")
            rows = db.routing_exception_list()
            assert len(rows) == 1
            r = rows[0]
            assert r["exception_type"] == "skill"
            assert r["pattern"] == "auto-time"
            assert r["note"] == "test note"
            assert r["created_at"] > 0

    def test_add_multiple_types(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("skill", "auto-time")
            db.routing_exception_add("filetype", ".md")
            db.routing_exception_add("caller", "github-copilot")
            rows = db.routing_exception_list()
            types = {r["exception_type"] for r in rows}
            assert types == {"skill", "filetype", "caller"}

    def test_list_ordered_by_type_then_pattern(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("skill", "zzz")
            db.routing_exception_add("caller", "aaa")
            db.routing_exception_add("skill", "aaa")
            rows = db.routing_exception_list()
            ordered = [(r["exception_type"], r["pattern"]) for r in rows]
            assert ordered == sorted(ordered)

    def test_remove_existing(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("skill", "auto-time")
            removed = db.routing_exception_remove("skill", "auto-time")
            assert removed is True
            assert db.routing_exception_list() == []

    def test_remove_nonexistent_returns_false(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            removed = db.routing_exception_remove("skill", "does-not-exist")
            assert removed is False

    def test_add_duplicate_is_upsert(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("skill", "auto-time", "original note")
            db.routing_exception_add("skill", "auto-time", "updated note")
            rows = db.routing_exception_list()
            assert len(rows) == 1
            assert rows[0]["note"] == "updated note"

    def test_add_invalid_type_raises(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            with pytest.raises(ValueError, match="(?i)invalid exception_type"):
                db.routing_exception_add("badtype", "foo")

    def test_add_empty_pattern_raises(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            with pytest.raises(ValueError):
                db.routing_exception_add("skill", "")

    def test_remove_only_matching_row(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("skill", "auto-time")
            db.routing_exception_add("skill", "other-skill")
            db.routing_exception_remove("skill", "auto-time")
            rows = db.routing_exception_list()
            assert len(rows) == 1
            assert rows[0]["pattern"] == "other-skill"

    def test_all_valid_types_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            for t in ("skill", "filetype", "project", "command", "caller", "path"):
                db.routing_exception_add(t, "test-value")
            rows = db.routing_exception_list()
            assert len(rows) == 6


# ---------------------------------------------------------------------------
# 2. Config loading
# ---------------------------------------------------------------------------

class TestRoutingExceptionsConfig:
    def test_default_has_builtin_doc_and_ai_instruction_exemptions(self):
        cfg = TGsConfig()
        r = cfg.routing_exceptions
        assert r.skills == []
        assert tuple(r.filetypes) == DEFAULT_ROUTING_EXCEPTION_FILETYPES
        assert r.projects == []
        assert r.commands == []
        assert r.callers == []
        assert tuple(r.paths) == DEFAULT_ROUTING_EXCEPTION_PATHS

    def test_from_yaml_loads_routing_exceptions(self):
        import yaml
        raw_yaml = """
routing_exceptions:
  skills:
    - auto-time
    - "tgsd-*"
  filetypes:
    - .md
    - .json
  projects:
    - /home/me/notes
  commands:
    - Write
  callers:
    - github-copilot
  paths:
    - /tmp/
"""
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(raw_yaml)
            tmp_path = Path(f.name)
        try:
            cfg = TGsConfig.from_yaml(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        r = cfg.routing_exceptions
        assert r.skills == ["auto-time", "tgsd-*"]
        assert r.filetypes == [".md", ".mdc", ".json"]
        assert r.projects == ["/home/me/notes"]
        assert r.commands == ["Write"]
        assert r.callers == ["github-copilot"]
        assert r.paths == [*DEFAULT_ROUTING_EXCEPTION_PATHS, "/tmp/"]

    def test_from_yaml_missing_key_keeps_defaults(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write("# no routing_exceptions key\n")
            tmp_path = Path(f.name)
        try:
            cfg = TGsConfig.from_yaml(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        r = cfg.routing_exceptions
        assert r.skills == []
        assert tuple(r.filetypes) == DEFAULT_ROUTING_EXCEPTION_FILETYPES
        assert tuple(r.paths) == DEFAULT_ROUTING_EXCEPTION_PATHS

    def test_from_yaml_partial_key(self):
        raw_yaml = """
routing_exceptions:
  skills:
    - "auto-time"
"""
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(raw_yaml)
            tmp_path = Path(f.name)
        try:
            cfg = TGsConfig.from_yaml(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        r = cfg.routing_exceptions
        assert r.skills == ["auto-time"]
        assert tuple(r.filetypes) == DEFAULT_ROUTING_EXCEPTION_FILETYPES
        assert tuple(r.paths) == DEFAULT_ROUTING_EXCEPTION_PATHS

    def test_from_yaml_empty_strings_filtered(self):
        raw_yaml = """
routing_exceptions:
  skills:
    - auto-time
    - ""
    - "   "
    - other
"""
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(raw_yaml)
            tmp_path = Path(f.name)
        try:
            cfg = TGsConfig.from_yaml(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        # empty / whitespace-only entries are filtered
        assert "" not in cfg.routing_exceptions.skills
        assert "auto-time" in cfg.routing_exceptions.skills


# ---------------------------------------------------------------------------
# 3. _is_routing_exception_exempt() — all 6 types
# ---------------------------------------------------------------------------

class TestIsRoutingExceptionExempt:
    """Test _is_routing_exception_exempt() against DB-backed rules."""

    def _call(self, db, cfg, **kwargs):
        defaults = dict(skill=None, filetype=None, cwd=None, tool_name=None, caller=None, target_file=None)
        defaults.update(kwargs)
        return mcp_server._is_routing_exception_exempt(db, cfg, **defaults)

    # ---- skill ----

    def test_skill_exact_match(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("skill", "auto-time")
            cfg = TGsConfig()
            exempt, reason = self._call(db, cfg, skill="auto-time")
            assert exempt is True
            assert reason == "routing_exception_skill"

    def test_skill_glob_match(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("skill", "tgsd-*")
            cfg = TGsConfig()
            exempt, _ = self._call(db, cfg, skill="tgsd-plan-phase")
            assert exempt is True

    def test_skill_case_insensitive(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("skill", "Auto-Time")
            cfg = TGsConfig()
            exempt, _ = self._call(db, cfg, skill="auto-time")
            assert exempt is True

    def test_skill_no_match(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("skill", "auto-time")
            cfg = TGsConfig()
            exempt, _ = self._call(db, cfg, skill="other-skill")
            assert exempt is False

    def test_skill_none_does_not_match(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("skill", "auto-time")
            cfg = TGsConfig()
            exempt, _ = self._call(db, cfg, skill=None)
            assert exempt is False

    # ---- filetype ----

    def test_filetype_exact_match(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("filetype", ".md")
            cfg = TGsConfig()
            exempt, reason = self._call(db, cfg, filetype=".md")
            assert exempt is True
            assert reason == "routing_exception_filetype"

    def test_filetype_case_insensitive(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("filetype", ".MD")
            cfg = TGsConfig()
            exempt, _ = self._call(db, cfg, filetype=".md")
            assert exempt is True

    def test_filetype_no_match(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("filetype", ".md")
            cfg = TGsConfig()
            exempt, _ = self._call(db, cfg, filetype=".py")
            assert exempt is False

    # ---- command ----

    def test_command_match(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("command", "Write")
            cfg = TGsConfig()
            exempt, reason = self._call(db, cfg, tool_name="Write")
            assert exempt is True
            assert reason == "routing_exception_command"

    def test_command_glob(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("command", "Edit*")
            cfg = TGsConfig()
            exempt, _ = self._call(db, cfg, tool_name="EditFile")
            assert exempt is True

    # ---- caller ----

    def test_caller_match(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("caller", "github-copilot")
            cfg = TGsConfig()
            exempt, reason = self._call(db, cfg, caller="github-copilot")
            assert exempt is True
            assert reason == "routing_exception_caller"

    def test_caller_no_match(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("caller", "claude-code")
            cfg = TGsConfig()
            exempt, _ = self._call(db, cfg, caller="github-copilot")
            assert exempt is False

    # ---- project ----

    def test_project_prefix_match(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("project", "/home/me/notes")
            cfg = TGsConfig()
            exempt, reason = self._call(db, cfg, cwd="/home/me/notes/subdir")
            assert exempt is True
            assert reason == "routing_exception_project"

    def test_project_exact_match(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("project", "/home/me/notes")
            cfg = TGsConfig()
            exempt, _ = self._call(db, cfg, cwd="/home/me/notes")
            assert exempt is True

    def test_project_no_partial_name_collision(self):
        """'/home/me/notes-extra' should NOT match '/home/me/notes'."""
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("project", "/home/me/notes")
            cfg = TGsConfig()
            exempt, _ = self._call(db, cfg, cwd="/home/me/notes-extra/sub")
            assert exempt is False

    def test_project_no_match(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("project", "/home/me/notes")
            cfg = TGsConfig()
            exempt, _ = self._call(db, cfg, cwd="/home/other/project")
            assert exempt is False

    # ---- path ----

    def test_path_prefix_match(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            # Resolve to handle macOS /var -> /private/var symlink
            real_td = str(Path(td).resolve())
            db.routing_exception_add("path", real_td)
            cfg = TGsConfig()
            exempt, reason = self._call(db, cfg, target_file=str(Path(td) / "foo.py"))
            assert exempt is True
            assert reason == "routing_exception_path"

    def test_path_no_partial_collision(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("path", "/tmp/safe")
            cfg = TGsConfig()
            exempt, _ = self._call(db, cfg, target_file="/tmp/safe-extra/file.py")
            assert exempt is False

    def test_relative_path_pattern_is_scoped_to_cwd_root(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            cfg = _cfg_with_exceptions(paths=["docs/generated/*"])
            root = str(Path(td).resolve())

            intended, reason = self._call(
                db,
                cfg,
                cwd=root,
                target_file=str(Path(root) / "docs" / "generated" / "notes.txt"),
            )
            lookalike, _ = self._call(
                db,
                cfg,
                cwd=root,
                target_file=str(Path(root) / "src" / "docs" / "generated" / "evil.py"),
            )

            assert intended is True
            assert reason == "routing_exception_path"
            assert lookalike is False

    def test_relative_path_pattern_does_not_use_process_cwd_when_empty(self, monkeypatch: pytest.MonkeyPatch):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            cfg = _cfg_with_exceptions(paths=["docs/generated/*"])
            root = Path(td).resolve()
            monkeypatch.chdir(root)

            exempt, reason = self._call(
                db,
                cfg,
                cwd="",
                target_file=str(root / "docs" / "generated" / "notes.txt"),
            )

            assert exempt is False
            assert reason == ""

    def test_relative_path_pattern_does_not_use_process_cwd_when_missing(self, monkeypatch: pytest.MonkeyPatch):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            cfg = _cfg_with_exceptions(paths=["docs/generated/*"])
            root = Path(td).resolve()
            monkeypatch.chdir(root)

            exempt, reason = self._call(
                db,
                cfg,
                target_file=str(root / "docs" / "generated" / "notes.txt"),
            )

            assert exempt is False
            assert reason == ""

    def test_relative_wildcard_pattern_does_not_match_absolute_target_without_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            cfg = _cfg_with_exceptions(paths=["*.md"])
            root = Path(td).resolve()

            exempt, reason = self._call(
                db,
                cfg,
                target_file=str(root / "README.md"),
            )

            assert exempt is False
            assert reason == ""

    def test_relative_path_pattern_normalizes_dot_segments(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            cfg = _cfg_with_exceptions(paths=["./docs/generated/*"])
            root = Path(td).resolve()

            exempt, reason = self._call(
                db,
                cfg,
                cwd=str(root),
                target_file=str(root / "docs" / "generated" / "notes.txt"),
            )

            assert exempt is True
            assert reason == "routing_exception_path"

    def test_bare_filename_path_pattern_is_scoped_to_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            cfg = TGsConfig()
            root = Path(td).resolve()
            outside = root / "outside"
            outside.mkdir()

            exempt_inside, reason_inside = self._call(
                db,
                cfg,
                cwd=str(root),
                target_file=str(root / "CLAUDE.md"),
            )
            exempt_outside, _ = self._call(
                db,
                cfg,
                cwd=str(root),
                target_file=str(outside / "CLAUDE.md"),
            )

            assert exempt_inside is True
            assert reason_inside == "routing_exception_path"
            assert exempt_outside is False

    def test_root_path_pattern_is_not_trimmed_away(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            cfg = _cfg_with_exceptions(paths=["/"])

            exempt, reason = self._call(
                db,
                cfg,
                target_file="/tmp/example.txt",
            )

            assert exempt is True
            assert reason == "routing_exception_path"

    def test_mapping_config_static_exceptions_are_honored(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            cfg = {"routing_exceptions": {"filetypes": [".rst"]}}
            exempt, reason = self._call(db, cfg, filetype=".rst")
            assert exempt is True
            assert reason == "routing_exception_filetype"

    # ---- static config takes effect too ----

    def test_config_yaml_skill_exempt(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            cfg = _cfg_with_exceptions(skills=["auto-time"])
            exempt, reason = self._call(db, cfg, skill="auto-time")
            assert exempt is True
            assert reason == "routing_exception_skill"

    def test_config_yaml_filetype_exempt(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            cfg = _cfg_with_exceptions(filetypes=[".json"])
            exempt, reason = self._call(db, cfg, filetype=".json")
            assert exempt is True
            assert reason == "routing_exception_filetype"

    def test_config_and_db_both_checked(self):
        """DB entry matches even when config has no matching rule."""
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            db.routing_exception_add("command", "Write")
            cfg = _cfg_with_exceptions(skills=["auto-time"])  # no command entries in config
            exempt, reason = self._call(db, cfg, tool_name="Write")
            assert exempt is True
            assert reason == "routing_exception_command"

    def test_no_exception_rules_returns_false(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(td)
            cfg = TGsConfig()
            exempt, _ = self._call(db, cfg, skill="auto-time", filetype=".py", tool_name="Write")
            assert exempt is False


# ---------------------------------------------------------------------------
# 4. MCP handler: routing_exception_add
# ---------------------------------------------------------------------------

class TestHandleRoutingExceptionAdd:
    def _add(self, exc_type, pattern, note=None):
        return mcp_server.handle_routing_exception_add(
            {"exception_type": exc_type, "pattern": pattern, "note": note}
        )

    def test_add_returns_exception_dict(self):
        with tempfile.TemporaryDirectory() as td:
            db = Database(db_path=Path(td) / "mcp.db")
            with patch.object(mcp_server, "_ensure_init", return_value=(TGsConfig(db_path=Path(td) / "mcp.db"), db, None, None, None)):
                result = self._add("skill", "auto-time", "bypass auto-time skill")
        assert "exception" in result
        exc = result["exception"]
        assert exc["exception_type"] == "skill"
        assert exc["pattern"] == "auto-time"
        assert exc["note"] == "bypass auto-time skill"

    def test_add_invalid_type_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            db = Database(db_path=Path(td) / "mcp.db")
            with patch.object(mcp_server, "_ensure_init", return_value=(TGsConfig(db_path=Path(td) / "mcp.db"), db, None, None, None)):
                result = mcp_server.handle_routing_exception_add(
                    {"exception_type": "badtype", "pattern": "foo"}
                )
        assert "error" in result

    def test_add_missing_pattern_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            db = Database(db_path=Path(td) / "mcp.db")
            with patch.object(mcp_server, "_ensure_init", return_value=(TGsConfig(db_path=Path(td) / "mcp.db"), db, None, None, None)):
                result = mcp_server.handle_routing_exception_add(
                    {"exception_type": "skill"}
                )
        assert "error" in result

    def test_add_missing_type_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            db = Database(db_path=Path(td) / "mcp.db")
            with patch.object(mcp_server, "_ensure_init", return_value=(TGsConfig(db_path=Path(td) / "mcp.db"), db, None, None, None)):
                result = mcp_server.handle_routing_exception_add(
                    {"pattern": "auto-time"}
                )
        assert "error" in result


# ---------------------------------------------------------------------------
# 5. MCP handler: routing_exception_remove
# ---------------------------------------------------------------------------

class TestHandleRoutingExceptionRemove:
    def test_remove_existing(self):
        with tempfile.TemporaryDirectory() as td:
            db = Database(db_path=Path(td) / "mcp.db")
            db.routing_exception_add("skill", "auto-time")
            with patch.object(mcp_server, "_ensure_init", return_value=(TGsConfig(db_path=Path(td) / "mcp.db"), db, None, None, None)):
                result = mcp_server.handle_routing_exception_remove(
                    {"exception_type": "skill", "pattern": "auto-time"}
                )
        assert result.get("removed") is True

    def test_remove_nonexistent_not_error(self):
        with tempfile.TemporaryDirectory() as td:
            db = Database(db_path=Path(td) / "mcp.db")
            with patch.object(mcp_server, "_ensure_init", return_value=(TGsConfig(db_path=Path(td) / "mcp.db"), db, None, None, None)):
                result = mcp_server.handle_routing_exception_remove(
                    {"exception_type": "skill", "pattern": "nonexistent"}
                )
        assert "error" not in result
        assert result.get("removed") is False

    def test_remove_missing_type_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            db = Database(db_path=Path(td) / "mcp.db")
            with patch.object(mcp_server, "_ensure_init", return_value=(TGsConfig(db_path=Path(td) / "mcp.db"), db, None, None, None)):
                result = mcp_server.handle_routing_exception_remove({"pattern": "auto-time"})
        assert "error" in result


# ---------------------------------------------------------------------------
# 6. MCP handler: routing_exception_list
# ---------------------------------------------------------------------------

class TestHandleRoutingExceptionList:
    def test_list_empty(self):
        with tempfile.TemporaryDirectory() as td:
            db = Database(db_path=Path(td) / "mcp.db")
            with patch.object(mcp_server, "_ensure_init", return_value=(TGsConfig(db_path=Path(td) / "mcp.db"), db, None, None, None)):
                result = mcp_server.handle_routing_exception_list({})
        assert result.get("exceptions") == []
        assert result.get("count") == 0

    def test_list_returns_all_rows(self):
        with tempfile.TemporaryDirectory() as td:
            db = Database(db_path=Path(td) / "mcp.db")
            db.routing_exception_add("skill", "auto-time")
            db.routing_exception_add("filetype", ".md")
            with patch.object(mcp_server, "_ensure_init", return_value=(TGsConfig(db_path=Path(td) / "mcp.db"), db, None, None, None)):
                result = mcp_server.handle_routing_exception_list({})
        assert result.get("count") == 2
        types = {r["exception_type"] for r in result["exceptions"]}
        assert types == {"skill", "filetype"}


# ---------------------------------------------------------------------------
# 7. validate_routing_guard — short-circuits on exception match
# ---------------------------------------------------------------------------

class TestValidateRoutingGuardExempt:
    """validate_routing_guard should return valid=True without checking the guard table."""

    def _setup(self, td: str):
        db_path = Path(td) / "guard.db"
        cfg = TGsConfig(db_path=db_path)
        db = Database(db_path=db_path)
        return cfg, db

    def test_skill_bypass_skips_guard_check(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, db = self._setup(td)
            db.routing_exception_add("skill", "auto-time")
            with patch.object(mcp_server, "_ensure_init", return_value=(cfg, db, None, None, None)):
                result = mcp_server.handle_validate_routing_guard({
                    "target_file": "/some/file.py",
                    "cwd": "/project",
                    "caller": "claude-code",
                    "tool_name": "Write",
                    "skill": "auto-time",
                })
        assert result.get("valid") is True
        assert result.get("mode") == "exempt"
        assert "routing_exception_skill" in result.get("reason", "")

    def test_filetype_bypass(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, db = self._setup(td)
            db.routing_exception_add("filetype", ".md")
            with patch.object(mcp_server, "_ensure_init", return_value=(cfg, db, None, None, None)):
                result = mcp_server.handle_validate_routing_guard({
                    "target_file": "/docs/readme.md",
                    "cwd": "/project",
                    "caller": "claude-code",
                    "tool_name": "Write",
                })
        assert result.get("valid") is True
        assert "routing_exception_filetype" in result.get("reason", "")

    def test_caller_bypass(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, db = self._setup(td)
            db.routing_exception_add("caller", "github-copilot")
            with patch.object(mcp_server, "_ensure_init", return_value=(cfg, db, None, None, None)):
                result = mcp_server.handle_validate_routing_guard({
                    "target_file": "/some/file.py",
                    "cwd": "/project",
                    "caller": "github-copilot",
                    "tool_name": "Edit",
                })
        assert result.get("valid") is True
        assert "routing_exception_caller" in result.get("reason", "")

    def test_project_bypass(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, db = self._setup(td)
            # Resolve to handle macOS /var -> /private/var symlink
            real_td = str(Path(td).resolve())
            db.routing_exception_add("project", real_td)
            sub = str(Path(td) / "subdir")
            with patch.object(mcp_server, "_ensure_init", return_value=(cfg, db, None, None, None)):
                result = mcp_server.handle_validate_routing_guard({
                    "target_file": str(Path(td) / "file.py"),
                    "cwd": sub,
                    "caller": "claude-code",
                    "tool_name": "Write",
                })
        assert result.get("valid") is True
        assert "routing_exception_project" in result.get("reason", "")

    def test_missing_cwd_does_not_trigger_relative_path_bypass(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, db = self._setup(td)
            db.routing_exception_add("path", "*.txt")
            target = str(Path(td).resolve() / "README.txt")
            with patch.object(mcp_server, "_ensure_init", return_value=(cfg, db, None, None, None)):
                result = mcp_server.handle_validate_routing_guard({
                    "target_file": target,
                    "caller": "claude-code",
                    "tool_name": "Write",
                })
        assert result.get("valid") is False
        assert "routing_exception_path" not in result.get("reason", "")

    def test_no_exception_falls_through_to_guard_check(self):
        """Without matching exception, normal guard validation runs (returns valid=False here)."""
        with tempfile.TemporaryDirectory() as td:
            cfg, db = self._setup(td)
            with patch.object(mcp_server, "_ensure_init", return_value=(cfg, db, None, None, None)):
                result = mcp_server.handle_validate_routing_guard({
                    "target_file": "/some/file.py",
                    "cwd": "/project",
                    "caller": "claude-code",
                    "tool_name": "Write",
                })
        # No guard set, so it should be denied (not exempted)
        assert result.get("valid") is False

    def test_static_config_skill_bypass(self):
        """Config-only (no DB) exception is respected."""
        with tempfile.TemporaryDirectory() as td:
            cfg, db = self._setup(td)
            cfg.routing_exceptions = RoutingExceptions(skills=["static-skill"])
            with patch.object(mcp_server, "_ensure_init", return_value=(cfg, db, None, None, None)):
                result = mcp_server.handle_validate_routing_guard({
                    "target_file": "/some/file.py",
                    "cwd": "/project",
                    "caller": "claude-code",
                    "tool_name": "Write",
                    "skill": "static-skill",
                })
        assert result.get("valid") is True
        assert result.get("mode") == "exempt"
