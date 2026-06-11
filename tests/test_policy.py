"""Tests for plan 12 — runtime policy enforcement."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from shared.policy import Policy, evaluate, Verdict, OPEN_POLICY, ALLOW, DENY


# ---------------------------------------------------------------------------
# Basic evaluate
# ---------------------------------------------------------------------------

def test_open_policy_allows_everything():
    v = evaluate(OPEN_POLICY, "file_write", "/home/user/code.py")
    assert v.allowed


def test_file_write_denied_by_path_deny():
    policy = Policy(name="test", path_deny=["**/secrets/**"])
    v = evaluate(policy, "file_write", "/home/user/secrets/key.txt")
    assert v.denied


def test_file_write_allowed_when_not_in_deny():
    policy = Policy(name="test", path_deny=["**/secrets/**"])
    v = evaluate(policy, "file_write", "/home/user/code.py")
    assert v.allowed


def test_file_write_denied_when_not_in_allow():
    policy = Policy(name="test", path_allow=["/home/user/project/**"])
    v = evaluate(policy, "file_write", "/tmp/evil.sh")
    assert v.denied


def test_file_write_allowed_when_in_allow():
    policy = Policy(name="test", path_allow=["/home/user/**"])
    v = evaluate(policy, "file_write", "/home/user/src/main.py")
    assert v.allowed


def test_path_deny_takes_priority_over_allow():
    policy = Policy(
        name="test",
        path_allow=["/home/user/**"],
        path_deny=["/home/user/secrets/**"],
    )
    v = evaluate(policy, "file_write", "/home/user/secrets/token")
    assert v.denied


# ---------------------------------------------------------------------------
# Command policy
# ---------------------------------------------------------------------------

def test_command_allow_list_blocks_unknown():
    policy = Policy(name="test", command_allow=["pytest", "python3", "pip"])
    v = evaluate(policy, "command", "rm -rf /")
    assert v.denied


def test_command_allow_list_passes_known():
    policy = Policy(name="test", command_allow=["pytest", "python3"])
    v = evaluate(policy, "command", "pytest tests/")
    assert v.allowed


def test_command_deny_blocks_matching():
    policy = Policy(name="test", command_deny=["rm", "dd", "mkfs"])
    v = evaluate(policy, "command", "rm -rf /tmp")
    assert v.denied


def test_command_open_allows_any():
    policy = Policy(name="open")
    v = evaluate(policy, "command", "arbitrary-binary --flag")
    assert v.allowed


# ---------------------------------------------------------------------------
# HTTP egress
# ---------------------------------------------------------------------------

def test_http_egress_blocked_when_not_in_allow():
    policy = Policy(name="test", net_allow_hosts=["api.anthropic.com", "*.github.com"])
    v = evaluate(policy, "http_egress", "evil.example.com")
    assert v.denied


def test_http_egress_allowed_when_in_allow():
    policy = Policy(name="test", net_allow_hosts=["api.anthropic.com"])
    v = evaluate(policy, "http_egress", "api.anthropic.com")
    assert v.allowed


def test_http_egress_open_when_no_list():
    policy = Policy(name="test")
    v = evaluate(policy, "http_egress", "anydomain.com")
    assert v.allowed


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------

def test_mcp_tool_allow_list():
    policy = Policy(name="test", mcp_tools_allow=["execute_subtask", "route_task"])
    v_allow = evaluate(policy, "mcp_tool", "execute_subtask")
    v_deny  = evaluate(policy, "mcp_tool", "memory_set")
    assert v_allow.allowed
    assert v_deny.denied


def test_mcp_tool_deny_list():
    policy = Policy(name="test", mcp_tools_deny=["memory_set", "memory_delete"])
    v = evaluate(policy, "mcp_tool", "memory_set")
    assert v.denied


def test_mcp_tool_open_allows_all():
    v = evaluate(OPEN_POLICY, "mcp_tool", "any_tool")
    assert v.allowed


# ---------------------------------------------------------------------------
# Secret access
# ---------------------------------------------------------------------------

def test_secrets_denied_when_no_allow_list():
    policy = Policy(name="restricted")
    v = evaluate(policy, "secret", "OPENAI_API_KEY")
    assert v.denied


def test_secrets_allowed_when_in_list():
    policy = Policy(name="test", secrets_allow=["GITHUB_TOKEN", "OPENAI_*"])
    v = evaluate(policy, "secret", "OPENAI_API_KEY")
    assert v.allowed


def test_secrets_denied_when_not_in_list():
    policy = Policy(name="test", secrets_allow=["GITHUB_TOKEN"])
    v = evaluate(policy, "secret", "AWS_SECRET_ACCESS_KEY")
    assert v.denied


# ---------------------------------------------------------------------------
# Verdict properties
# ---------------------------------------------------------------------------

def test_verdict_allowed_property():
    v = Verdict(ALLOW, "ok")
    assert v.allowed and not v.denied


def test_verdict_denied_property():
    v = Verdict(DENY, "blocked")
    assert v.denied and not v.allowed


def test_unknown_op_type_is_denied():
    v = evaluate(OPEN_POLICY, "invalid_op_type", "target")  # type: ignore[arg-type]
    assert v.denied


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

def test_policy_from_yaml_loads_correctly(tmp_path):
    yaml_content = """\
name: test-policy
path_deny:
  - "**/secrets/**"
mcp_tools_allow:
  - execute_subtask
  - route_task
command_deny:
  - rm
"""
    yaml_path = tmp_path / "test.yaml"
    yaml_path.write_text(yaml_content)

    from shared.policy import policy_from_yaml
    policy = policy_from_yaml(str(yaml_path))
    assert policy.name == "test-policy"
    assert "**/secrets/**" in policy.path_deny
    assert "execute_subtask" in policy.mcp_tools_allow
    assert "rm" in policy.command_deny


# ---------------------------------------------------------------------------
# WorkerSession policy attachment
# ---------------------------------------------------------------------------

def test_worker_session_policy_blocks_send(tmp_path):
    from shared.db import Database
    from shared.orchestrator import WorkerSession
    from shared.policy import Policy

    db = Database(tmp_path / "test.db")
    policy = Policy(
        name="strict",
        mcp_tools_deny=["session_send"],
    )
    session = WorkerSession("s-policy", "claude-code", "model", proc=None, db=db, policy=policy)
    with pytest.raises(PermissionError, match="Policy denied"):
        session.send("hello")
