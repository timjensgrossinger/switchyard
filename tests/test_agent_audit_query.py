"""Focused tests for bounded agent audit event queries."""
from __future__ import annotations

from shared.db import Database


def test_list_agent_audit_events_is_newest_first_and_filterable(
    temp_db_fixture: Database,
) -> None:
    temp_db_fixture.agent_audit_log("agent-a", "created", {"step": 1})
    second_id = temp_db_fixture.agent_audit_log("agent-b", "approved", {"step": 2})
    third_id = temp_db_fixture.agent_audit_log("agent-a", "registered", {"step": 3})

    all_events = temp_db_fixture.list_agent_audit_events(limit=2)
    filtered = temp_db_fixture.list_agent_audit_events(agent_id="agent-a", limit=10)

    assert [event["id"] for event in all_events] == [third_id, second_id]
    assert [event["event_type"] for event in filtered] == ["registered", "created"]
    assert all(event["agent_id"] == "agent-a" for event in filtered)


def test_list_agent_audit_events_clamps_limit(temp_db_fixture: Database) -> None:
    for index in range(105):
        temp_db_fixture.agent_audit_log(
            f"agent-{index}",
            "generated",
            {"index": index},
        )

    events = temp_db_fixture.list_agent_audit_events(limit=500)

    assert len(events) == 100
