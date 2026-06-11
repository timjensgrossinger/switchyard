#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_server
from shared.config import TGsConfig
from shared.db import Database
from shared.planner import CLIBackend, Planner
from shared.router import TaskRouter


class RecordingBackend(CLIBackend):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def call(self, prompt: str, model: str | None = None, timeout: int = 120) -> str | None:
        self.calls.append(prompt)
        return None


class StubRegistry:
    def select_provider(self, tier: str, *, caller: str | None = None, prefer_free: bool = True):
        from types import SimpleNamespace

        return SimpleNamespace(
            name="cursor",
            display_name="Cursor",
            resolve_model=lambda _tier: "cursor-model",
            cost_rank=0,
            billing_tier="free",
            is_free=True,
        )


def test_handle_plan_task_cursor_skips_planner_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    with TemporaryDirectory() as td:
        db_path = Path(td) / "planner-host-native.db"
        cfg = TGsConfig(db_path=db_path)
        db = Database(db_path=db_path)
        backend = RecordingBackend()
        planner = Planner(cfg, backend, db=db)
        router = TaskRouter(cfg)
        monkeypatch.setattr(
            mcp_server,
            "_ensure_init",
            lambda: (cfg, db, router, planner, None),
        )
        monkeypatch.setattr(mcp_server, "get_registry", lambda: StubRegistry())
        monkeypatch.setattr(mcp_server, "_resolve_caller", lambda: "cursor")

        result = mcp_server.handle_plan_task(
            {
                "task": (
                    "Build a calculator app: (1) models.py with Operation dataclass, "
                    "(2) ops.py with add/sub/mul/div, (3) main.py CLI entrypoint"
                )
            }
        )

        assert backend.calls == []
        assert result.get("planner_host_execution_mode") == "host_native"
        assert result.get("planner_mode") == "heuristic"
        assert len(result.get("subtasks", [])) == 3
