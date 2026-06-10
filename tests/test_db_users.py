"""
Tests for multi-user DB layer: user CRUD, token HMAC storage, and scoped
remote_jobs queries.

Uses the conftest ``temp_db_fixture`` for hermetic DB isolation.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.db import Database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test_users.db")


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

class TestUserCRUD:
    def test_create_and_get_by_username(self, tmp_path):
        db = _make_db(tmp_path)
        uid = db.create_user("alice", "tok_alice")
        assert uid  # non-empty string
        user = db.get_user_by_username("alice")
        assert user is not None
        assert user["username"] == "alice"
        assert user["user_id"] == uid
        assert user["enabled"] is True or user["enabled"] == 1

    def test_create_and_get_by_id(self, tmp_path):
        db = _make_db(tmp_path)
        uid = db.create_user("bob", "tok_bob")
        user = db.get_user_by_id(uid)
        assert user is not None
        assert user["username"] == "bob"

    def test_token_stored_as_hmac(self, tmp_path):
        """Raw token must NOT appear in DB when a secret is provided."""
        raw_token = "super-secret-token"
        db = _make_db(tmp_path)
        db.create_user("carol", raw_token, secret="admin-secret")
        # token_hmac is intentionally excluded from public row dicts;
        # verify via direct SQL that the stored value differs from the raw token.
        with db.conn() as conn:
            row = conn.execute(
                "SELECT token_hmac FROM users WHERE username='carol'"
            ).fetchone()
        assert row is not None
        assert row[0] != raw_token

    def test_get_by_token_hmac(self, tmp_path):
        db = _make_db(tmp_path)
        import hmac as _hmac, hashlib
        admin_secret = "admin-secret"
        raw_token = "user-personal-token"
        token_hmac = _hmac.new(
            admin_secret.encode(), raw_token.encode(), hashlib.sha256
        ).hexdigest()
        # create_user with secret stores the hmac directly
        uid = db.create_user("dave", raw_token, secret=admin_secret)
        user = db.get_user_by_token_hmac(token_hmac)
        assert user is not None
        assert user["username"] == "dave"

    def test_list_users(self, tmp_path):
        db = _make_db(tmp_path)
        db.create_user("u1", "t1")
        db.create_user("u2", "t2")
        db.create_user("u3", "t3")
        users = db.list_users()
        assert len(users) >= 3
        names = {u["username"] for u in users}
        assert {"u1", "u2", "u3"}.issubset(names)

    def test_set_user_enabled_disable(self, tmp_path):
        db = _make_db(tmp_path)
        uid = db.create_user("eve", "tok_eve")
        db.set_user_enabled(uid, False)
        user = db.get_user_by_id(uid)
        assert not user["enabled"]

    def test_set_user_enabled_reenable(self, tmp_path):
        db = _make_db(tmp_path)
        uid = db.create_user("frank", "tok_frank")
        db.set_user_enabled(uid, False)
        db.set_user_enabled(uid, True)
        user = db.get_user_by_id(uid)
        assert user["enabled"]

    def test_delete_user(self, tmp_path):
        db = _make_db(tmp_path)
        uid = db.create_user("grace", "tok_grace")
        db.delete_user(uid)
        assert db.get_user_by_id(uid) is None
        assert db.get_user_by_username("grace") is None

    def test_duplicate_username_raises(self, tmp_path):
        db = _make_db(tmp_path)
        db.create_user("hank", "tok1")
        with pytest.raises(Exception):
            db.create_user("hank", "tok2")

    def test_unknown_user_returns_none(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.get_user_by_username("nobody") is None
        assert db.get_user_by_id("00000000-0000-0000-0000-000000000000") is None

    def test_providers_json_stored_and_retrieved(self, tmp_path):
        db = _make_db(tmp_path)
        creds = json.dumps({"claude": {"env": {"ANTHROPIC_API_KEY": "sk-test"}}})
        uid = db.create_user("iris", "tok_iris", providers_json=creds)
        user = db.get_user_by_id(uid)
        stored = json.loads(user.get("providers_json") or "{}")
        assert stored.get("claude", {}).get("env", {}).get("ANTHROPIC_API_KEY") == "sk-test"


# ---------------------------------------------------------------------------
# Scoped remote_jobs queries
# ---------------------------------------------------------------------------

class TestJobScoping:
    def test_user_can_get_own_job(self, tmp_path):
        db = _make_db(tmp_path)
        uid = db.create_user("jack", "tok_jack")
        job_id = str(uuid.uuid4())
        db.create_remote_job(job_id, "some task", user_id=uid)
        row = db.get_remote_job(job_id, user_id=uid)
        assert row is not None
        assert row["job_id"] == job_id

    def test_user_cannot_get_other_users_job(self, tmp_path):
        db = _make_db(tmp_path)
        uid_a = db.create_user("user_a", "tok_a")
        uid_b = db.create_user("user_b", "tok_b")
        job_id = str(uuid.uuid4())
        db.create_remote_job(job_id, "task for a", user_id=uid_a)
        # User B tries to fetch User A's job — should get None
        row = db.get_remote_job(job_id, user_id=uid_b)
        assert row is None

    def test_admin_can_get_any_job(self, tmp_path):
        db = _make_db(tmp_path)
        uid = db.create_user("kate", "tok_kate")
        job_id = str(uuid.uuid4())
        db.create_remote_job(job_id, "kate's task", user_id=uid)
        # Admin passes user_id=None → no ownership filter
        row = db.get_remote_job(job_id, user_id=None)
        assert row is not None

    def test_list_user_jobs(self, tmp_path):
        db = _make_db(tmp_path)
        uid_a = db.create_user("leo", "tok_leo")
        uid_b = db.create_user("mia", "tok_mia")
        j1 = str(uuid.uuid4())
        j2 = str(uuid.uuid4())
        j3 = str(uuid.uuid4())
        db.create_remote_job(j1, "leo task 1", user_id=uid_a)
        db.create_remote_job(j2, "leo task 2", user_id=uid_a)
        db.create_remote_job(j3, "mia task",   user_id=uid_b)
        leos = db.list_user_jobs(uid_a)
        assert len(leos) == 2
        ids = {r["job_id"] for r in leos}
        assert j1 in ids and j2 in ids and j3 not in ids

    def test_list_all_jobs_admin(self, tmp_path):
        db = _make_db(tmp_path)
        uid_a = db.create_user("nina", "tok_nina")
        uid_b = db.create_user("omar", "tok_omar")
        for _ in range(3):
            db.create_remote_job(str(uuid.uuid4()), "task", user_id=uid_a)
        for _ in range(2):
            db.create_remote_job(str(uuid.uuid4()), "task", user_id=uid_b)
        all_jobs = db.list_all_jobs()
        assert len(all_jobs) >= 5

    def test_admin_job_has_no_user_id(self, tmp_path):
        db = _make_db(tmp_path)
        job_id = str(uuid.uuid4())
        db.create_remote_job(job_id, "admin task")  # no user_id
        row = db.get_remote_job(job_id)
        assert row is not None
        assert row.get("user_id") is None

    def test_backward_compat_existing_jobs_null_user(self, tmp_path):
        """Jobs created without user_id must still be retrievable by admin."""
        db = _make_db(tmp_path)
        job_id = str(uuid.uuid4())
        db.create_remote_job(job_id, "legacy task")
        row = db.get_remote_job(job_id, user_id=None)
        assert row is not None
