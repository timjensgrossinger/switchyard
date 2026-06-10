#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import Database


def test_accepted_amendment_persists_revision_and_audit() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
        db = Database(Path(db_file.name))
        revision_id = db.insert_plan_revision(
            plan_id="13-03",
            revision_number=2,
            diff_blob={"updated_subtasks": ["13-03-02"]},
            proposer_id="coordinator-1",
            reason="tighten future validation",
        )
        audit_id = db.insert_coordinator_audit(revision_id, outcome="accepted")

        with db.conn() as conn:
            revision = conn.execute(
                """
                SELECT plan_id, revision_number, diff_blob, proposer_id, reason
                FROM plan_revisions
                WHERE id = ?
                """,
                (revision_id,),
            ).fetchone()
            audit = conn.execute(
                """
                SELECT plan_id, revision_id, proposer_id, diff_blob, reason, outcome, rejection_reason
                FROM coordinator_amendments
                WHERE id = ?
                """,
                (audit_id,),
            ).fetchone()

        assert revision is not None
        assert revision[0] == "13-03"
        assert revision[1] == 2
        assert json.loads(revision[2]) == {"updated_subtasks": ["13-03-02"]}
        assert revision[3] == "coordinator-1"
        assert revision[4] == "tighten future validation"

        assert audit is not None
        assert audit[0] == "13-03"
        assert audit[1] == revision_id
        assert audit[2] == "coordinator-1"
        assert json.loads(audit[3]) == {"updated_subtasks": ["13-03-02"]}
        assert audit[4] == "tighten future validation"
        assert audit[5] == "accepted"
        assert audit[6] is None

        db.close()


def test_rejected_amendment_persists_audit_error() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
        db = Database(Path(db_file.name))
        audit_id = db.insert_coordinator_audit_rejection(
            plan_id="13-03",
            proposer_id="coordinator-2",
            reason="attempted duplicate coordinator in wave 2",
            diff_blob={"wave": 2, "coordinator_ids": [4, 5]},
        )

        with db.conn() as conn:
            audit = conn.execute(
                """
                SELECT plan_id, revision_id, proposer_id, diff_blob, reason, outcome, rejection_reason
                FROM coordinator_amendments
                WHERE id = ?
                """,
                (audit_id,),
            ).fetchone()

        assert audit is not None
        assert audit[0] == "13-03"
        assert audit[1] is None
        assert audit[2] == "coordinator-2"
        assert json.loads(audit[3]) == {"wave": 2, "coordinator_ids": [4, 5]}
        assert audit[4] == "attempted duplicate coordinator in wave 2"
        assert audit[5] == "rejected"
        assert audit[6] == "attempted duplicate coordinator in wave 2"

        db.close()
