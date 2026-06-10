"""
Runtime policy enforcement (plan 12).

Policy dataclass + evaluate(policy, op) -> Verdict.
Fail-closed: unrecognized op type → deny.
"""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

OpType = Literal["file_write", "file_read", "command", "http_egress", "mcp_tool", "secret"]

ALLOW = "allow"
DENY  = "deny"


@dataclass(frozen=True)
class Policy:
    """Per-session / per-swarm / per-project access policy."""
    name: str = "default"
    # Glob patterns for allowed/denied file paths.
    path_allow: list[str] = field(default_factory=list)     # empty = allow all
    path_deny:  list[str] = field(default_factory=list)     # empty = deny none

    # Subprocess command allowlist/denylist (first token of command).
    command_allow: list[str] = field(default_factory=list)  # empty = allow all
    command_deny:  list[str] = field(default_factory=list)  # empty = deny none

    # Hostnames/CIDR prefixes allowed for HTTP egress.
    net_allow_hosts: list[str] = field(default_factory=list)  # empty = allow all

    # MCP tool names allowed in session/swarm scope.
    mcp_tools_allow: list[str] = field(default_factory=list)  # empty = allow all
    mcp_tools_deny:  list[str] = field(default_factory=list)  # empty = deny none

    # Secret keys accessible (by secret name prefix).
    secrets_allow: list[str] = field(default_factory=list)  # empty = deny all secrets


@dataclass(frozen=True)
class Verdict:
    decision: str    # "allow" | "deny"
    reason: str
    policy_name: str = "default"

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW

    @property
    def denied(self) -> bool:
        return self.decision == DENY


def _matches_any(value: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(value, pat):
            return True
    return False


def evaluate(policy: Policy, op_type: OpType, target: str) -> Verdict:
    """Evaluate policy for an operation.

    Args:
        policy:   Policy to apply.
        op_type:  Type of operation.
        target:   Subject of operation — file path, command name, hostname,
                  MCP tool name, or secret key.

    Returns:
        Verdict with allow/deny and reason.
    """
    name = policy.name

    if op_type in ("file_write", "file_read"):
        # Normalize path without resolving symlinks (portable across OS)
        import os as _os
        try:
            normalized = _os.path.normpath(target)
        except Exception:
            normalized = target

        if policy.path_deny and _matches_any(normalized, policy.path_deny):
            return Verdict(DENY, f"path denied by policy: {target!r}", name)
        if policy.path_allow and not _matches_any(normalized, policy.path_allow):
            return Verdict(DENY, f"path not in allow-list: {target!r}", name)
        return Verdict(ALLOW, "path allowed", name)

    if op_type == "command":
        cmd_token = target.split()[0] if target.strip() else ""
        if policy.command_deny and _matches_any(cmd_token, policy.command_deny):
            return Verdict(DENY, f"command denied by policy: {cmd_token!r}", name)
        if policy.command_allow and not _matches_any(cmd_token, policy.command_allow):
            return Verdict(DENY, f"command not in allow-list: {cmd_token!r}", name)
        return Verdict(ALLOW, "command allowed", name)

    if op_type == "http_egress":
        if policy.net_allow_hosts and not _matches_any(target, policy.net_allow_hosts):
            return Verdict(DENY, f"host not in net_allow_hosts: {target!r}", name)
        return Verdict(ALLOW, "host allowed", name)

    if op_type == "mcp_tool":
        if policy.mcp_tools_deny and _matches_any(target, policy.mcp_tools_deny):
            return Verdict(DENY, f"MCP tool denied: {target!r}", name)
        if policy.mcp_tools_allow and not _matches_any(target, policy.mcp_tools_allow):
            return Verdict(DENY, f"MCP tool not in allow-list: {target!r}", name)
        return Verdict(ALLOW, "MCP tool allowed", name)

    if op_type == "secret":
        if not policy.secrets_allow:
            return Verdict(DENY, "no secrets allowed by policy", name)
        if not _matches_any(target, policy.secrets_allow):
            return Verdict(DENY, f"secret not in allow-list: {target!r}", name)
        return Verdict(ALLOW, "secret allowed", name)

    return Verdict(DENY, f"unknown op_type: {op_type!r}", name)


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

def policy_from_yaml(path: str) -> Policy:
    """Load a Policy from a YAML file."""
    import yaml
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return Policy(
        name=str(data.get("name") or path),
        path_allow=list(data.get("path_allow") or []),
        path_deny=list(data.get("path_deny") or []),
        command_allow=list(data.get("command_allow") or []),
        command_deny=list(data.get("command_deny") or []),
        net_allow_hosts=list(data.get("net_allow_hosts") or []),
        mcp_tools_allow=list(data.get("mcp_tools_allow") or []),
        mcp_tools_deny=list(data.get("mcp_tools_deny") or []),
        secrets_allow=list(data.get("secrets_allow") or []),
    )


# Default open policy (allow everything).
OPEN_POLICY = Policy(name="open")
