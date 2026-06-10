"""
Integration tests for multi-user remote server auth, job isolation, and
per-user execution context.

These tests spin up a real ``_ThreadingHTTPServer`` on a random port,
make HTTP requests with Bearer tokens, and assert correct auth / scoping
behaviour. Provider execution is mocked to avoid live CLI calls.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import secrets
import socket
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
import urllib.request
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.db import Database
from shared.remote_server import build_server, _hmac_token


# ---------------------------------------------------------------------------
# Test server fixture
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _TestServer:
    """Wraps a live test server and provides HTTP helpers."""

    def __init__(self, admin_token: str, db: Database) -> None:
        self.admin_token = admin_token
        self.db = db
        self.port = _free_port()
        self.server = build_server(
            "127.0.0.1", self.port, admin_token, no_tls=True, db=db
        )
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        # Brief wait for the server to be ready
        for _ in range(20):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=1)
                break
            except Exception:
                time.sleep(0.05)

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str = "",
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def stop(self) -> None:
        self.server.shutdown()


class _ExecuteProvider:
    def __init__(self, result_text: str = "print('ok')\n") -> None:
        self.name = "Mock Provider"
        self.tier_models = {"low": "mock-low"}
        self.result_text = result_text
        self.execute_calls = 0

    def execute(self, prompt: str, model: str, env_overrides: dict[str, str] | None = None) -> str:
        self.execute_calls += 1
        return self.result_text


class _ExecuteRegistry:
    def __init__(self, provider: _ExecuteProvider) -> None:
        self._provider = provider

    def cheapest_for_tier(self, _tier: str) -> _ExecuteProvider:
        return self._provider


@pytest.fixture()
def server_env(tmp_path):
    """Yield (server, admin_token, db)."""
    db = Database(tmp_path / "srv_test.db")
    admin_token = secrets.token_urlsafe(32)
    srv = _TestServer(admin_token, db)
    yield srv, admin_token, db
    srv.stop()


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

class TestAuth:
    def test_health_unauthenticated(self, server_env):
        srv, _, _ = server_env
        status, body = srv.request("GET", "/health")
        assert status == 200
        assert body["status"] == "ok"

    def test_admin_token_grants_access(self, server_env):
        srv, admin_token, _ = server_env
        status, body = srv.request("POST", "/v1/route", token=admin_token,
                                   body={"task": "do something"})
        # 200 or 500 (missing deps) — both indicate auth passed (not 401)
        assert status != 401

    def test_no_token_returns_401(self, server_env):
        srv, _, _ = server_env
        status, body = srv.request("POST", "/v1/route", body={"task": "test"})
        assert status == 401

    def test_wrong_token_returns_401(self, server_env):
        srv, _, _ = server_env
        status, body = srv.request("POST", "/v1/route", token="totally-wrong",
                                   body={"task": "test"})
        assert status == 401

    def test_user_token_grants_access(self, server_env):
        srv, admin_token, db = server_env
        # Register a user
        raw_token = secrets.token_urlsafe(32)
        uid = db.create_user("testuser", raw_token)
        # The server HMACs the provided token with the admin secret to look
        # up the user.  We must store the same hmac in the DB.
        db.update_user_token_hmac(uid, raw_token, secret=admin_token)
        status, body = srv.request("GET", "/v1/jobs", token=raw_token)
        assert status in (200, 503)  # not 401

    def test_disabled_user_returns_401(self, server_env):
        srv, admin_token, db = server_env
        raw_token = secrets.token_urlsafe(32)
        uid = db.create_user("disabled_user", raw_token)
        db.update_user_token_hmac(uid, raw_token, secret=admin_token)
        db.set_user_enabled(uid, False)
        status, _ = srv.request("GET", "/v1/jobs", token=raw_token)
        assert status == 401


# ---------------------------------------------------------------------------
# Job isolation tests
# ---------------------------------------------------------------------------

class TestJobIsolation:
    def _register_user(self, db: Database, admin_token: str, username: str) -> tuple[str, str]:
        """Register a user and return (user_id, raw_token)."""
        raw_token = secrets.token_urlsafe(32)
        uid = db.create_user(username, raw_token)
        db.update_user_token_hmac(uid, raw_token, secret=admin_token)
        return uid, raw_token

    def test_user_sees_only_own_jobs(self, server_env):
        srv, admin_token, db = server_env
        uid_a, tok_a = self._register_user(db, admin_token, "user_alpha")
        uid_b, tok_b = self._register_user(db, admin_token, "user_beta")

        j1 = str(uuid.uuid4())
        j2 = str(uuid.uuid4())
        db.create_remote_job(j1, "alpha's task", user_id=uid_a)
        db.create_remote_job(j2, "beta's task",  user_id=uid_b)

        status_a, body_a = srv.request("GET", "/v1/jobs", token=tok_a)
        assert status_a == 200
        ids_a = {j["job_id"] for j in body_a["jobs"]}
        assert j1 in ids_a
        assert j2 not in ids_a

    def test_admin_sees_all_jobs(self, server_env):
        srv, admin_token, db = server_env
        uid_a, _ = self._register_user(db, admin_token, "gamma")
        uid_b, _ = self._register_user(db, admin_token, "delta")
        j1, j2, j3 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        db.create_remote_job(j1, "t", user_id=uid_a)
        db.create_remote_job(j2, "t", user_id=uid_b)
        db.create_remote_job(j3, "admin job")

        status, body = srv.request("GET", "/v1/jobs", token=admin_token)
        assert status == 200
        ids = {j["job_id"] for j in body["jobs"]}
        assert j1 in ids and j2 in ids and j3 in ids

    def test_user_cannot_fetch_other_users_job_by_id(self, server_env):
        srv, admin_token, db = server_env
        uid_a, tok_a = self._register_user(db, admin_token, "epsilon")
        uid_b, tok_b = self._register_user(db, admin_token, "zeta")
        job_id = str(uuid.uuid4())
        db.create_remote_job(job_id, "epsilon's job", user_id=uid_a)

        status, body = srv.request("GET", f"/v1/job/{job_id}", token=tok_b)
        assert status == 404

    def test_admin_can_fetch_any_job_by_id(self, server_env):
        srv, admin_token, db = server_env
        uid, tok = self._register_user(db, admin_token, "eta")
        job_id = str(uuid.uuid4())
        db.create_remote_job(job_id, "eta's job", user_id=uid)

        status, body = srv.request("GET", f"/v1/job/{job_id}", token=admin_token)
        assert status == 200
        assert body["job_id"] == job_id


class TestExecutePathSafety:
    def test_execute_rejects_target_file_outside_cwd(self, server_env, tmp_path):
        srv, admin_token, _ = server_env
        provider = _ExecuteProvider()
        server_root = tmp_path / "server-root"
        server_root.mkdir()
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside.py"

        with patch("shared.discovery.ProviderRegistry", return_value=_ExecuteRegistry(provider)), patch(
            "shared.remote_server.os.getcwd",
            return_value=str(server_root),
        ):
            status, body = srv.request(
                "POST",
                "/v1/execute",
                token=admin_token,
                body={
                    "prompt": "write a file",
                    "cwd": str(workspace),
                    "target_file": str(outside),
                },
            )

        assert status == 400
        assert body["error"] == "PathTraversalRejected"
        assert provider.execute_calls == 0
        assert outside.exists() is False

    def test_execute_rejects_target_file_dotdot_traversal(self, server_env, tmp_path):
        srv, admin_token, _ = server_env
        provider = _ExecuteProvider()
        server_root = tmp_path / "server-root"
        server_root.mkdir()
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        with patch("shared.discovery.ProviderRegistry", return_value=_ExecuteRegistry(provider)), patch(
            "shared.remote_server.os.getcwd",
            return_value=str(server_root),
        ):
            status, body = srv.request(
                "POST",
                "/v1/execute",
                token=admin_token,
                body={
                    "prompt": "write a file",
                    "cwd": str(workspace),
                    "target_file": "../outside.py",
                },
            )

        assert status == 400
        assert body["error"] == "PathTraversalRejected"
        assert provider.execute_calls == 0

    def test_execute_accepts_target_file_inside_cwd(self, server_env, tmp_path):
        srv, admin_token, _ = server_env
        provider = _ExecuteProvider("print('safe')\n")
        server_root = tmp_path / "server-root"
        server_root.mkdir()
        workspace = server_root / "workspace"
        workspace.mkdir()
        target = workspace / "generated.py"

        with patch("shared.discovery.ProviderRegistry", return_value=_ExecuteRegistry(provider)), patch(
            "shared.remote_server.os.getcwd",
            return_value=str(server_root),
        ):
            status, body = srv.request(
                "POST",
                "/v1/execute",
                token=admin_token,
                body={
                    "prompt": "write a file",
                    "cwd": str(workspace),
                    "target_file": str(target),
                },
            )

        assert status == 200
        assert body["file_written"] == str(target.resolve())
        assert target.read_text(encoding="utf-8") == "print('safe')\n"
        assert provider.execute_calls == 1

    def test_execute_rejects_target_file_symlink(self, server_env, tmp_path):
        srv, admin_token, _ = server_env
        provider = _ExecuteProvider("print('safe')\n")
        server_root = tmp_path / "server-root"
        server_root.mkdir()
        workspace = server_root / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("outside\n", encoding="utf-8")
        target = workspace / "generated.py"
        target.symlink_to(outside)

        with patch("shared.discovery.ProviderRegistry", return_value=_ExecuteRegistry(provider)), patch(
            "shared.remote_server.os.getcwd",
            return_value=str(server_root),
        ):
            status, body = srv.request(
                "POST",
                "/v1/execute",
                token=admin_token,
                body={
                    "prompt": "write a file",
                    "cwd": str(workspace),
                    "target_file": str(target),
                },
            )

        assert status == 400
        assert body["error"] == "PathTraversalRejected"
        assert provider.execute_calls == 0
        assert outside.read_text(encoding="utf-8") == "outside\n"

    def test_execute_rejects_workspace_outside_server_root(self, server_env, tmp_path):
        srv, admin_token, _ = server_env
        provider = _ExecuteProvider()
        server_root = tmp_path / "server-root"
        server_root.mkdir()
        outside = tmp_path / "outside.py"

        with patch("shared.discovery.ProviderRegistry", return_value=_ExecuteRegistry(provider)), patch(
            "shared.remote_server.os.getcwd",
            return_value=str(server_root),
        ):
            status, body = srv.request(
                "POST",
                "/v1/execute",
                token=admin_token,
                body={
                    "prompt": "write a file",
                    "cwd": "/",
                    "target_file": str(outside),
                },
            )

        assert status == 400
        assert body["error"] == "PathTraversalRejected"
        assert provider.execute_calls == 0


# ---------------------------------------------------------------------------
# _hmac_token unit test
# ---------------------------------------------------------------------------

class TestHmacToken:
    def test_deterministic(self):
        h1 = _hmac_token("mytoken", "mysecret")
        h2 = _hmac_token("mytoken", "mysecret")
        assert h1 == h2

    def test_different_token_different_hash(self):
        h1 = _hmac_token("tok1", "secret")
        h2 = _hmac_token("tok2", "secret")
        assert h1 != h2

    def test_different_secret_different_hash(self):
        h1 = _hmac_token("token", "sec1")
        h2 = _hmac_token("token", "sec2")
        assert h1 != h2

    def test_matches_manual_hmac(self):
        import hashlib
        expected = _hmac.new(
            b"admin-secret", b"user-token", hashlib.sha256
        ).hexdigest()
        assert _hmac_token("user-token", "admin-secret") == expected
