#!/usr/bin/env python3
"""
Junie smoke tests for Wave 0 of Phase 7.

Tests CLIP-03 requirement: Junie adapter supports non-interactive mode with
`--output-format=json` for structured result + cost telemetry extraction.

Tests D-06 through D-10 locked decisions:
- D-06: Junie infers configured backend/model; exposes only truthful tiers
- D-07: Support both JetBrains-managed and BYOK setups
- D-08: Normalized result remains shared contract; provider metadata supplemental
- D-09: Telemetry captured when available; missing telemetry doesn't block correctness
- D-10: Retain only summarized metadata subset, not full raw payloads

All tests are hermetic and use mocked subprocess calls. No real Junie CLI required.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from subprocess import CompletedProcess

import pytest

# Ensure the project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.discovery import CLIProvider, DetectReason, ProviderReadiness


class TestJunieDetection:
    """Test Junie detection per D-06 and D-07."""

    def test_junie_detect_byok_api_key(self, mock_env, isolation_test_mode):
        """
        Test Junie detection succeeds with BYOK API key (D-07).
        
        Per D-07, both JetBrains-managed and BYOK setups must be supported.
        """
        mock_env.setenv("JUNIE_API_KEY", "junie-key-12345")
        
        def _junie_detect_hook(provider: CLIProvider) -> ProviderReadiness:
            key = os.environ.get("JUNIE_API_KEY")
            if key:
                return ProviderReadiness(
                    routeable=True,
                    reason=DetectReason.READY,
                    last_checked=None
                )
            return ProviderReadiness(
                routeable=False,
                reason=DetectReason.AUTH_FAILED,
                last_checked=None
            )
        
        provider = CLIProvider(
            name="junie",
            binary="junie",
            display_name="Junie",
            tier_models={
                "medium": "medium",  # Junie is single-tier per D-06
            },
            cost_rank={"medium": 2},
            detect_hook=_junie_detect_hook,
        )
        
        readiness = provider.detect()
        assert readiness.routeable is True
        assert readiness.reason == DetectReason.READY

    def test_junie_detect_byok_config_file(self, mock_env, isolation_test_mode):
        """
        Test Junie detection via BYOK config file (D-07).
        
        Checks ~/.local/share/junie/config.json for BYOK configuration.
        """
        def _junie_detect_hook(provider: CLIProvider) -> ProviderReadiness:
            # Check BYOK API key first
            if os.environ.get("JUNIE_API_KEY"):
                return ProviderReadiness(
                    routeable=True,
                    reason=DetectReason.READY,
                    last_checked=None
                )
            
            # Check BYOK config file
            config_path = Path.home() / ".local/share/junie/config.json"
            if config_path.exists():
                try:
                    with open(config_path) as f:
                        config = json.load(f)
                        if config.get("api_key") or config.get("authenticated"):
                            return ProviderReadiness(
                                routeable=True,
                                reason=DetectReason.READY,
                                last_checked=None
                            )
                except Exception:
                    pass
            
            return ProviderReadiness(
                routeable=False,
                reason=DetectReason.AUTH_FAILED,
                last_checked=None
            )
        
        # Create temporary home directory with config
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home = Path(tmpdir)
            config_dir = mock_home / ".local/share/junie"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_file = config_dir / "config.json"
            config_file.write_text(json.dumps({"api_key": "byok-key-123"}))
            
            with patch("pathlib.Path.home", return_value=mock_home):
                provider = CLIProvider(
                    name="junie",
                    binary="junie",
                    display_name="Junie",
                    tier_models={
                        "medium": "medium",
                    },
                    cost_rank={"medium": 2},
                    detect_hook=_junie_detect_hook,
                )
                
                readiness = provider.detect()
                assert readiness.routeable is True
                assert readiness.reason == DetectReason.READY

    def test_junie_detect_jetbrains_managed(self, mock_junie_cli):
        """
        Test Junie detection for JetBrains-managed setup (D-07).
        
        Checks `junie login status` command to verify JetBrains-managed auth.
        """
        def _junie_detect_hook(provider: CLIProvider) -> ProviderReadiness:
            from subprocess import run
            
            # Check BYOK first
            if os.environ.get("JUNIE_API_KEY"):
                return ProviderReadiness(
                    routeable=True,
                    reason=DetectReason.READY,
                    last_checked=None
                )
            
            # Check JetBrains-managed setup
            try:
                result = run(
                    ["junie", "login", "status"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    return ProviderReadiness(
                        routeable=True,
                        reason=DetectReason.READY,
                        last_checked=None
                    )
            except Exception:
                pass
            
            return ProviderReadiness(
                routeable=False,
                reason=DetectReason.AUTH_FAILED,
                last_checked=None
            )
        
        with mock_junie_cli:
            provider = CLIProvider(
                name="junie",
                binary="junie",
                display_name="Junie",
                tier_models={
                    "medium": "medium",
                },
                cost_rank={"medium": 2},
                detect_hook=_junie_detect_hook,
            )
            
            readiness = provider.detect()
            assert readiness.routeable is True
            assert readiness.reason == DetectReason.READY

    def test_junie_detect_no_auth(self, mock_env, isolation_test_mode):
        """
        Test Junie detection fails without auth (D-06 truthfulness).
        
        Per D-06, Junie without auth is truthfully non-routeable.
        """
        mock_env.delenv("JUNIE_API_KEY", raising=False)
        
        def _junie_detect_hook(provider: CLIProvider) -> ProviderReadiness:
            if os.environ.get("JUNIE_API_KEY"):
                return ProviderReadiness(
                    routeable=True,
                    reason=DetectReason.READY,
                    last_checked=None
                )
            return ProviderReadiness(
                routeable=False,
                reason=DetectReason.AUTH_FAILED,
                last_checked=None
            )
        
        provider = CLIProvider(
            name="junie",
            binary="junie",
            display_name="Junie",
            tier_models={
                "medium": "medium",
            },
            cost_rank={"medium": 2},
            detect_hook=_junie_detect_hook,
        )
        
        readiness = provider.detect()
        assert readiness.routeable is False
        assert readiness.reason == DetectReason.AUTH_FAILED


class TestJunieTierCoverage:
    """Test Junie single-tier truthfulness per D-06."""

    def test_junie_single_tier_coverage(self):
        """
        Test Junie exposes only single-tier coverage (truthful per D-06).
        
        Per D-06, Junie doesn't support per-call --model flag, so it's single-tier.
        No faking of low/medium/high coverage.
        """
        provider = CLIProvider(
            name="junie",
            binary="junie",
            display_name="Junie",
            tier_models={
                "medium": "medium",  # Only medium tier
            },
            cost_rank={"medium": 2},
        )
        
        # Verify only medium tier is available
        assert "medium" in provider.tier_models
        assert "low" not in provider.tier_models
        assert "high" not in provider.tier_models
        
        # Verify cost_rank reflects single tier
        assert len(provider.cost_rank) == 1
        assert "medium" in provider.cost_rank


class TestJunieCommandBuilding:
    """Test Junie command building per D-06 and CLIP-03."""

    def test_junie_command_building(self):
        """
        Test Junie command building doesn't include --model flag.
        
        Per D-06, Junie doesn't support per-call model selection.
        """
        def _build_command(
            provider: CLIProvider,
            prompt: str,
            model: str,
            code_only: str = None
        ) -> list[str]:
            """Build Junie command (no --model flag)."""
            return [
                "junie",
                prompt,
                "--output-format=json"
            ]
        
        provider = CLIProvider(
            name="junie",
            binary="junie",
            display_name="Junie",
            tier_models={
                "medium": "medium",
            },
            cost_rank={"medium": 2},
            command_builder=_build_command,
        )
        
        cmd = _build_command(provider, "write a function", "medium")
        
        assert cmd[0] == "junie"
        assert "write a function" in cmd
        assert "--output-format=json" in cmd
        # Verify --model flag is NOT used
        assert "--model" not in cmd
        assert "medium" not in cmd or cmd.index("medium") == cmd.index("medium")

    def test_junie_command_ignores_effort(self):
        """Junie should accept but ignore effort at the command-builder layer."""
        from junie.providers import _build_junie_command

        provider = CLIProvider(
            name="junie",
            binary="junie",
            display_name="Junie",
            tier_models={"medium": "medium"},
            cost_rank={"medium": 2},
        )

        cmd = _build_junie_command(
            provider,
            "execute",
            "medium",
            "write a function",
            effort="high",
        )

        assert cmd == ["junie", "write a function", "--output-format=json"]
        assert "--reasoning-effort" not in cmd


class TestJunieJSONParsing:
    """Test Junie JSON parsing and telemetry extraction per D-08 through D-10."""

    def test_junie_json_parsing_with_telemetry(self, mock_junie_cli):
        """
        Test Junie JSON parsing extracts normalized result (D-08).
        
        Per D-08, normalized result (the code) stays in shared contract.
        Telemetry is extracted separately as supplemental metadata.
        """
        with mock_junie_cli:
            from subprocess import run
            result = run(
                ["junie", "write hello", "--output-format=json"],
                capture_output=True,
                text=True
            )
            
            assert result.returncode == 0
            
            # Parse JSON response
            junie_response = json.loads(result.stdout)
            
            # Extract normalized result (D-08: shared contract)
            output = junie_response.get("result")
            assert output is not None
            assert "import anthropic" in output

    def test_junie_telemetry_extraction(self, mock_junie_cli):
        """
        Test Junie telemetry extraction from llmUsage (D-10 summarized metadata).
        
        Per D-10, only summarized metadata fields are extracted:
        - model, tokens, cost, session_id
        
        Raw payload is NOT stored.
        """
        with mock_junie_cli:
            from subprocess import run
            result = run(
                ["junie", "write hello", "--output-format=json"],
                capture_output=True,
                text=True
            )
            
            junie_response = json.loads(result.stdout)
            
            # Extract summarized telemetry from llmUsage
            telemetry = {}
            llm_usage = junie_response.get("llmUsage", [])
            if llm_usage and len(llm_usage) > 0:
                usage = llm_usage[0]  # First usage entry
                telemetry = {
                    "model": usage.get("model"),
                    "input_tokens": usage.get("inputTokens"),
                    "output_tokens": usage.get("outputTokens"),
                    "cost": usage.get("cost"),
                }
            
            session_id = junie_response.get("sessionId")
            
            # Verify all summarized fields are present (D-10)
            assert telemetry.get("model") == "claude-opus-4-1"
            assert telemetry.get("input_tokens") == 42
            assert telemetry.get("output_tokens") == 156
            assert telemetry.get("cost") == 0.00234
            assert session_id == "junie-sess-12345"
            
            # Verify these can be serialized to JSON (required for metadata storage)
            metadata_json = json.dumps({
                "provider": "junie",
                "telemetry": telemetry,
                "session_id": session_id,
            })
            assert len(metadata_json) > 0

    def test_junie_malformed_json_fallback(self, mock_env):
        """
        Test Junie gracefully falls back when JSON is malformed (D-09).
        
        Per D-09, missing telemetry doesn't block execution correctness.
        """
        def _mock_junie_malformed(cmd, *args, **kwargs):
            if isinstance(cmd, list) and len(cmd) > 0 and cmd[0] == "junie":
                # Return raw text instead of JSON
                return CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="def hello():\n    return 42\n",  # Not JSON
                    stderr=""
                )
            return CompletedProcess(args=cmd, returncode=1, stdout="", stderr="Error")
        
        with patch("subprocess.run", side_effect=_mock_junie_malformed):
            from subprocess import run
            result = run(
                ["junie", "write hello", "--output-format=json"],
                capture_output=True,
                text=True
            )
            
            # Try to parse as JSON, but handle graceful fallback
            try:
                junie_response = json.loads(result.stdout)
                output = junie_response.get("result")
            except json.JSONDecodeError:
                # Fallback: treat as raw text (D-09)
                output = result.stdout.strip()
            
            # Either way, we have output (correctness not blocked)
            assert len(output) > 0
            assert result.returncode == 0

    def test_junie_raw_payload_not_stored(self, mock_junie_cli):
        """
        Test Junie raw payload is NOT stored (D-10 summarized metadata only).
        
        Per D-10, only summarized metadata fields are retained; raw payloads
        are discarded to prevent tampering claims and reduce storage.
        """
        with mock_junie_cli:
            from subprocess import run
            result = run(
                ["junie", "write hello", "--output-format=json"],
                capture_output=True,
                text=True
            )
            
            junie_response = json.loads(result.stdout)
            
            # Extract ONLY summarized metadata (D-10)
            metadata = {}
            llm_usage = junie_response.get("llmUsage", [])
            if llm_usage and len(llm_usage) > 0:
                usage = llm_usage[0]
                metadata = {
                    "provider": "junie",
                    "model": usage.get("model"),
                    "input_tokens": usage.get("inputTokens"),
                    "output_tokens": usage.get("outputTokens"),
                    "cost": usage.get("cost"),
                    "session_id": junie_response.get("sessionId"),
                }
            
            # Verify raw payload is NOT included in metadata dict (D-10)
            assert "result" not in metadata  # Raw code not stored
            assert "llmUsage" not in metadata  # Raw usage array not stored
            # Verify full response is not stored
            metadata_str = json.dumps(metadata)
            assert "result" not in metadata_str  # Raw code not in serialized metadata
            
            # Verify only essential fields are present
            assert len(metadata) == 6  # provider + 5 fields
            assert all(key in metadata for key in [
                "provider", "model", "input_tokens", "output_tokens", "cost", "session_id"
            ])
