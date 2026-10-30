"""Isolated real-file HTTP fixtures for the REQ-002 contract suite.

Run only in disposable Linux Docker. No deployment credentials or volumes.
OS account provisioning is external to REQ-002 and replaced at that boundary;
UserFS, HTTP dependencies, path checks and filesystem I/O remain real.
"""

import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace

import pytest


# Match REQ-001's synthetic process configuration so both suites can be run in
# one interpreter without collection order changing the imported app settings.
os.environ.update({
    "OPEN_TERMINAL_API_KEY": "req001-test-key-not-a-secret",
    "OPEN_TERMINAL_MULTI_USER": "true",
    "OPEN_TERMINAL_USER_PREFIX": "",
    "OPEN_TERMINAL_ENABLE_NOTEBOOKS": "false",
    "OPEN_TERMINAL_ENABLE_TERMINAL": "true",
    "OPEN_TERMINAL_ENABLE_SYSTEM_PROMPT": "true",
    "OPEN_TERMINAL_INFO": "req001-public-info",
    "OPEN_TERMINAL_LOG_DIR": "/tmp/req001-logs",
    "OPEN_TERMINAL_PUBLIC_READ_ROOTS": "[]",
})
os.environ.pop("OPEN_TERMINAL_API_KEY_FILE", None)


@pytest.fixture
def api(monkeypatch, tmp_path):
    if os.environ.get("REQ002_DISPOSABLE_CONTAINER") != "1" or os.geteuid() != 0:
        pytest.fail("REQ-002 HTTP tests require the disposable root Docker test image")

    from fastapi.testclient import TestClient
    from open_terminal import dependencies, env, main

    homes = {name: Path(tempfile.mkdtemp(prefix=f"req002-{name}-", dir="/home"))
             for name in ("alice", "bob")}
    public = Path(tempfile.mkdtemp(prefix="req002-public-", dir="/opt"))
    external = tmp_path / "private-service"
    external.mkdir()
    public_roots = (str(public),)
    monkeypatch.setenv("OPEN_TERMINAL_PUBLIC_READ_ROOTS", json.dumps(public_roots))
    # Configuration is an external input. R4's documented binding is env ->
    # dependencies -> UserFS; patch imported copies for the already-loaded app.
    for module in (env, dependencies):
        monkeypatch.setattr(module, "PUBLIC_READ_ROOTS", public_roots, raising=False)
    monkeypatch.setattr(dependencies, "resolve_user", lambda user: ("root", str(homes[user])))
    monkeypatch.setattr(main, "_session_cwds", {})
    client = TestClient(main.app, raise_server_exceptions=False)

    def request(method, url, *, user="alice", session="chat-a", **kwargs):
        headers = {
            "Authorization": "Bearer req001-test-key-not-a-secret",
            "X-User-Id": user,
            "X-Session-Id": session,
        }
        headers.update(kwargs.pop("headers", {}))
        return client.request(method, url, headers=headers, **kwargs)

    try:
        yield SimpleNamespace(client=client, main=main, homes=homes, public=public,
                              external=external, request=request)
    finally:
        client.close()
        for home in homes.values():
            if home.is_dir():
                shutil.rmtree(home)
        shutil.rmtree(public)
