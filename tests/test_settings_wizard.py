#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shared.settings_wizard as settings_wizard


def test_provider_label_tolerates_malformed_models() -> None:
    label = settings_wizard._provider_label(
        {
            "name": "broken-provider",
            "billing": "unknown",
            "models": "not-a-mapping",
        }
    )

    assert "broken-provider" in label


def test_write_config_works_without_pyyaml(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(settings_wizard, "yaml", None)

    settings_wizard._write_config(
        config_path,
        disabled=["gemini-cli"],
        caller_allowlists={},
        preferred_routing={},
        routing_policy={"mode": "advisory"},
    )

    body = config_path.read_text(encoding="utf-8")
    assert "routing_policy:" in body
    assert "mode: \"advisory\"" in body
