from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import codex.entry as entry
from codex.providers import (
    CODEX_TIER_MAP,
    CodexProvider,
    _build_codex_command,
    _clean_codex_output,
    _detect_codex,
)
from codex.providers_legacy import adapter_from_legacy
from shared.adapters import ProviderCapability
from shared.discovery import DetectReason
from shared.planner import Subtask


def _subtask(task_id: int = 7) -> Subtask:
    return Subtask(
        id=task_id,
        description="return exactly: codex-compatible",
        tier="low",
        model=CODEX_TIER_MAP["low"],
    )


def test_codex_command_uses_noninteractive_read_only_contract() -> None:
    provider = CodexProvider()

    command = _build_codex_command(
        provider,
        "execute_code_only",
        "test-model",
        "implement the change",
        effort="high",
    )
    output_file = Path(provider._pending_output_file)

    try:
        assert command[:4] == ["codex", "exec", "-m", "test-model"]
        assert command[command.index("-s") + 1] == "read-only"
        assert "--ephemeral" in command
        assert "--ignore-user-config" in command
        assert "--ignore-rules" in command
        assert "--skip-git-repo-check" in command
        assert command[command.index("-o") + 1] == str(output_file)
        assert command[command.index("-c") + 1] == 'model_reasoning_effort="high"'
        assert command[-1] == "implement the change"
        assert output_file.is_file()
    finally:
        output_file.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  completed\n", "completed"),
        ("\n\t", ""),
        ("plain text", "plain text"),
    ],
)
def test_codex_output_cleaning_is_plain_text(raw: str, expected: str) -> None:
    assert _clean_codex_output(raw) == expected


def test_codex_detection_prefers_api_key_without_spawning_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def unexpected_run(*_args, **_kwargs):
        pytest.fail("login probe must not run when OPENAI_API_KEY is present")

    monkeypatch.setattr("codex.providers.subprocess.run", unexpected_run)

    readiness = _detect_codex(CodexProvider())

    assert readiness.routeable is True
    assert readiness.reason == DetectReason.READY


@pytest.mark.parametrize(
    ("result_or_error", "expected_reason"),
    [
        (subprocess.CompletedProcess([], 0, "logged in", ""), DetectReason.READY),
        (subprocess.CompletedProcess([], 1, "", "not logged in"), DetectReason.AUTH_FAILED),
        (FileNotFoundError(), DetectReason.BINARY_MISSING),
        (subprocess.TimeoutExpired(["codex"], 5), DetectReason.AUTH_UNKNOWN),
    ],
)
def test_codex_detection_reports_login_probe_outcome(
    monkeypatch: pytest.MonkeyPatch,
    result_or_error: subprocess.CompletedProcess[str] | Exception,
    expected_reason: DetectReason,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def fake_run(*_args, **_kwargs):
        if isinstance(result_or_error, Exception):
            raise result_or_error
        return result_or_error

    monkeypatch.setattr("codex.providers.subprocess.run", fake_run)

    readiness = _detect_codex(CodexProvider())

    assert readiness.routeable is (expected_reason == DetectReason.READY)
    assert readiness.reason == expected_reason


def test_codex_execute_prefers_output_file_and_cleans_it_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexProvider()
    captured: dict[str, object] = {}

    monkeypatch.setattr("codex.providers.shutil.which", lambda _binary: "/usr/bin/codex")

    def fake_run(command: list[str], **kwargs):
        captured.update(kwargs)
        output_file = Path(command[command.index("-o") + 1])
        captured["output_file"] = output_file
        output_file.write_text(
            "  response from output file\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "stale stdout", "")

    monkeypatch.setattr("codex.providers.subprocess.run", fake_run)

    result = provider.execute(_subtask(), CODEX_TIER_MAP["low"], timeout=19)

    assert result == "response from output file"
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == 19
    assert not Path(captured["output_file"]).exists()
    assert provider._pending_output_file is None


def test_codex_execute_removes_output_file_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexProvider()
    output_paths: list[Path] = []

    monkeypatch.setattr("codex.providers.shutil.which", lambda _binary: "/usr/bin/codex")

    def fake_run(command: list[str], **_kwargs):
        output_paths.append(Path(command[command.index("-o") + 1]))
        return subprocess.CompletedProcess(command, 2, "", "authentication failed")

    monkeypatch.setattr("codex.providers.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match=r"agent #7 exited 2: authentication failed"):
        provider.execute(_subtask(), CODEX_TIER_MAP["low"])

    assert output_paths and not output_paths[0].exists()
    assert provider._pending_output_file is None


def test_codex_execute_translates_timeout_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexProvider()
    output_paths: list[Path] = []

    monkeypatch.setattr("codex.providers.shutil.which", lambda _binary: "/usr/bin/codex")

    def fake_run(command: list[str], **_kwargs):
        output_paths.append(Path(command[command.index("-o") + 1]))
        raise subprocess.TimeoutExpired(command, 3)

    monkeypatch.setattr("codex.providers.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match=r"agent #7 timed out after 3s"):
        provider.execute(_subtask(), CODEX_TIER_MAP["low"], timeout=3)

    assert output_paths and not output_paths[0].exists()
    assert provider._pending_output_file is None


def test_codex_adapter_preserves_provider_contract() -> None:
    provider = CodexProvider()
    adapter = adapter_from_legacy(provider)

    assert adapter.name == "codex"
    assert adapter.invoke("build_provider") is provider
    assert adapter.metadata["tier_models"] == CODEX_TIER_MAP
    assert adapter.metadata["auth_env_var"] == "OPENAI_API_KEY"
    assert ProviderCapability.EXECUTE in adapter.capabilities


def test_codex_route_command_emits_provider_model_and_cache_state(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    decision = SimpleNamespace(
        tier="high",
        score=0.91,
        reason="complex task",
        agents=3,
        override=False,
        intent_modifier=None,
    )
    provider = CodexProvider()
    db = SimpleNamespace(cache_get=lambda _task: ("cached", "test-model"))
    components = (
        SimpleNamespace(),
        db,
        SimpleNamespace(classify=lambda _task: decision),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    monkeypatch.setattr(entry, "_init", lambda: components)
    monkeypatch.setattr(entry, "_resolve_provider", lambda: provider)

    entry.cmd_route("verify compatibility")

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "tier": "high",
        "model": CODEX_TIER_MAP["high"],
        "score": 0.91,
        "reason": "complex task",
        "agents": 3,
        "cache_hit": True,
        "override": False,
        "intent_modifier": None,
    }
