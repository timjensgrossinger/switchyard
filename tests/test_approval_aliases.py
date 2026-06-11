#!/usr/bin/env python3
from __future__ import annotations

import mcp_server


def test_agent_queue_tools_are_discoverable() -> None:
    tool_names = {tool["name"] for tool in mcp_server.TOOLS}

    assert "agent_queue_list" in tool_names
    assert "agent_queue_approve" in tool_names
    assert "agent_queue_reject" in tool_names
    assert "agent_queue_merge" in tool_names
    assert "approval_queue_list" in tool_names
    assert "approval_queue_approve" in tool_names
    assert "approval_queue_reject" in tool_names
    assert "approval_queue_merge" in tool_names


def test_agent_queue_handlers_share_compatibility_mappings() -> None:
    assert mcp_server.HANDLERS["agent_queue_list"] is mcp_server.HANDLERS["approval_queue_list"]
    assert mcp_server.HANDLERS["agent_queue_approve"] is mcp_server.HANDLERS["approval_queue_approve"]
    assert mcp_server.HANDLERS["agent_queue_reject"] is mcp_server.HANDLERS["approval_queue_reject"]
    assert mcp_server.HANDLERS["agent_queue_merge"] is mcp_server.HANDLERS["approval_queue_merge"]
