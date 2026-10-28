"""REQ-001 AC-003: exercise the real app's HTTP/WS identity boundary.

Run in the dedicated Linux test image configured by conftest.py. The tests
never create OS users: the external provisioning boundary is a rejecting
spy, not a mocked identity result. Existing task/terminal state and the OS
port scan are isolated; authentication, validation, routing and response
serialization remain the production paths. No lifespan loop is needed.
"""

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


TEST_KEY = "req001-test-key-not-a-secret"

# Every enabled resource route has a syntactically valid request. This
# avoids accepting a body/query validation error in place of identity denial.
RESOURCE_REQUESTS = [
    pytest.param("GET", "/files/cwd", {}, id="cwd-read"),
    pytest.param("POST", "/files/cwd", {"json": {"path": "."}}, id="cwd-write"),
    pytest.param("GET", "/skills", {}, id="skills-list"),
    pytest.param("GET", "/skills/read?name=example", {}, id="skills-read"),
    pytest.param("GET", "/files/list?path=.", {}, id="files-list"),
    pytest.param("POST", "/files/compare", {"json": {"original": "a.txt", "revised": "b.txt"}}, id="files-compare"),
    pytest.param("GET", "/files/read?path=a.txt", {}, id="files-read"),
    pytest.param("GET", "/files/display?path=a.txt", {}, id="files-display"),
    pytest.param("GET", "/files/view?path=a.txt", {}, id="files-view"),
    pytest.param("GET", "/files/serve/home/example/a.txt", {}, id="files-serve"),
    pytest.param("POST", "/files/write", {"json": {"path": "a.txt", "content": "example"}}, id="files-write"),
    pytest.param("POST", "/files/mkdir", {"json": {"path": "example"}}, id="files-mkdir"),
    pytest.param("DELETE", "/files/delete?path=example", {}, id="files-delete"),
    pytest.param("POST", "/files/move", {"json": {"source": "a.txt", "destination": "b.txt"}}, id="files-move"),
    pytest.param("POST", "/files/replace", {"json": {"path": "a.txt", "replacements": [{"target": "old", "replacement": "new"}]}}, id="files-replace"),
    pytest.param("GET", "/files/grep?query=example&path=.", {}, id="files-grep"),
    pytest.param("GET", "/files/matches?query=example&path=.", {}, id="files-matches"),
    pytest.param("GET", "/files/search?query=example&path=.", {}, id="files-search"),
    pytest.param("GET", "/files/glob?pattern=*.txt&path=.", {}, id="files-glob"),
    pytest.param("POST", "/files/upload?directory=.", {"files": {"file": ("a.txt", b"example", "text/plain")}}, id="files-upload"),
    pytest.param("POST", "/files/archive", {"json": {"paths": ["a.txt"]}}, id="files-archive"),
    pytest.param("GET", "/execute", {}, id="processes-list"),
    pytest.param("POST", "/execute", {"json": {"command": "true"}}, id="process-create"),
    pytest.param("GET", "/execute/unknown/status", {}, id="process-status"),
    pytest.param("POST", "/execute/unknown/input", {"json": {"input": "example"}}, id="process-input"),
    pytest.param("DELETE", "/execute/unknown", {}, id="process-delete"),
    pytest.param("GET", "/ports", {}, id="ports-list"),
    *[
        pytest.param(method, "/proxy/18080/", {}, id=f"port-proxy-{method.lower()}")
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
    ],
    pytest.param("POST", "/api/terminals", {}, id="terminal-create"),
    pytest.param("GET", "/api/terminals", {}, id="terminals-list"),
    pytest.param("GET", "/api/terminals/unknown", {}, id="terminal-get"),
    pytest.param("DELETE", "/api/terminals/unknown", {}, id="terminal-delete"),
    pytest.param("GET", "/api/terminals/user/output", {}, id="terminal-user-output"),
    pytest.param("POST", "/api/terminals/user/input", {"json": {"input": "example"}}, id="terminal-user-input"),
]


@pytest.fixture
def api(monkeypatch):
    from open_terminal import dependencies, main

    provisioning_calls = []

    def unexpected_provisioning(user_id):
        provisioning_calls.append(user_id)
        raise AssertionError("Identity rejection must precede OS account provisioning")

    monkeypatch.setattr(dependencies, "resolve_user", unexpected_provisioning)
    monkeypatch.setattr(main, "_processes", {})
    monkeypatch.setattr(main, "_terminal_sessions", {})
    monkeypatch.setattr(main, "detect_listening_ports", lambda: [])
    client = TestClient(main.app, raise_server_exceptions=False)
    yield SimpleNamespace(client=client, main=main, provisioning_calls=provisioning_calls)
    client.close()


def assert_identity_denied(response, method="GET"):
    assert response.status_code == 400, response.text
    # HTTP HEAD intentionally omits the response body.
    if method != "HEAD":
        assert "application/json" in response.headers.get("content-type", ""), response.text
        detail = response.json()["detail"]
        assert isinstance(detail, dict), f"Expected structured identity error, got {detail!r}"
        assert detail["code"] == "invalid_user_identity"


@pytest.mark.parametrize("method,path,kwargs", RESOURCE_REQUESTS)
def test_every_resource_route_rejects_missing_identity(api, method, path, kwargs):
    response = api.client.request(
        method, path, headers={"Authorization": f"Bearer {TEST_KEY}"}, **kwargs
    )
    assert_identity_denied(response, method)
    assert api.provisioning_calls == []
    assert api.main._processes == {}
    assert api.main._terminal_sessions == {}


@pytest.mark.parametrize("method,path,kwargs", RESOURCE_REQUESTS)
def test_service_key_is_checked_before_invalid_identity(api, method, path, kwargs):
    response = api.client.request(
        method,
        path,
        headers={"Authorization": "Bearer incorrect-key", "X-User-Id": " "},
        **kwargs,
    )
    assert response.status_code == 401, response.text
    assert api.provisioning_calls == []
    assert api.main._processes == {}
    assert api.main._terminal_sessions == {}


@pytest.mark.parametrize("path", ["/files/cwd", "/execute", "/ports", "/api/terminals"])
@pytest.mark.parametrize("identity", ["", " ", " alice", "alice ", "alice/bob", "a" * 129])
def test_resource_families_reject_invalid_identity_without_provisioning(api, path, identity):
    response = api.client.get(
        path, headers={"Authorization": f"Bearer {TEST_KEY}", "X-User-Id": identity}
    )
    assert_identity_denied(response)
    assert api.provisioning_calls == []


@pytest.mark.parametrize("path", ["/files/cwd", "/execute", "/ports", "/api/terminals"])
@pytest.mark.parametrize("second_identity", ["alice", "bob"])
def test_duplicate_identity_headers_are_not_resolved_by_first_or_last_value(api, path, second_identity):
    response = api.client.get(
        path,
        headers=[
            ("Authorization", f"Bearer {TEST_KEY}"),
            ("X-User-Id", "alice"),
            ("x-user-id", second_identity),
        ],
    )
    assert_identity_denied(response)
    assert api.provisioning_calls == []


@pytest.mark.parametrize("path", ["/health", "/api/config", "/openapi.json", "/docs"])
def test_public_metadata_does_not_require_or_prepare_a_user(api, path):
    response = api.client.get(path)
    assert response.status_code == 200
    assert TEST_KEY not in response.text
    assert api.provisioning_calls == []


@pytest.mark.parametrize("path", ["/system", "/info"])
def test_service_metadata_accepts_key_without_identity(api, path):
    response = api.client.get(path, headers={"Authorization": f"Bearer {TEST_KEY}"})
    assert response.status_code == 200
    assert TEST_KEY not in response.text
    assert api.provisioning_calls == []


@pytest.mark.parametrize("path", ["/system", "/info", "/files/cwd", "/execute"])
def test_service_key_is_required(api, path):
    response = api.client.get(path, headers={"X-User-Id": "alice"})
    assert response.status_code == 401
    assert api.provisioning_calls == []


@pytest.mark.parametrize("path", ["/execute", "/api/terminals"])
def test_valid_identity_can_list_empty_runtime_state_without_creating_an_account(api, path):
    response = api.client.get(
        path, headers={"Authorization": f"Bearer {TEST_KEY}", "X-User-Id": "alice"}
    )
    assert response.status_code == 200
    assert response.json() == []
    assert api.provisioning_calls == []


@pytest.mark.parametrize(
    "path,code,status",
    [
        ("/files/cwd", "uid_pool_exhausted", 503),
        ("/files/cwd", "gid_pool_exhausted", 503),
        ("/files/cwd", "username_allocation_failed", 503),
        ("/files/cwd", "workspace_busy", 503),
        ("/files/cwd", "workspace_unavailable", 503),
        ("/files/cwd", "identity_conflict", 409),
        ("/files/cwd", "workspace_state_invalid", 500),
        ("/files/cwd", "workspace_config_invalid", 500),
        ("/ports", "uid_pool_exhausted", 503),
        ("/ports", "username_allocation_failed", 503),
        ("/ports", "identity_conflict", 409),
        ("/proxy/18080/", "gid_pool_exhausted", 503),
        ("/proxy/18080/", "workspace_state_invalid", 500),
    ],
)
def test_provisioning_errors_remain_diagnostic_without_leaking_details(api, monkeypatch, path, code, status):
    """AC-003/033: file and preview adapters translate instead of swallowing.

    Inject an error from the external provisioning boundary. Its deliberately
    sensitive message stands in for accidental command stderr; the HTTP adapter
    must still expose a safe, fixed message and only the allowlisted error code.
    This does not simulate the workspace allocation or ownership result.
    """
    from open_terminal import dependencies
    from open_terminal.utils.identity import WorkspaceProvisionError

    private_detail = "OTHER_USER_PRIVATE_ID /home/other-user/private.txt PRIVATE_FILE_CONTENT"

    def unavailable_workspace(user_id):
        api.provisioning_calls.append(user_id)
        raise WorkspaceProvisionError(code, private_detail + " " + TEST_KEY)

    monkeypatch.setattr(dependencies, "resolve_user", unavailable_workspace)
    response = api.client.get(
        path, headers={"Authorization": f"Bearer {TEST_KEY}", "X-User-Id": "alice"}
    )
    assert response.status_code == status, response.text
    assert "application/json" in response.headers.get("content-type", ""), response.text
    detail = response.json()["detail"]
    assert isinstance(detail, dict), f"Expected structured provisioning error, got {detail!r}"
    assert detail["code"] == code
    assert isinstance(detail["message"], str) and detail["message"]
    for sensitive_value in ("OTHER_USER_PRIVATE_ID", "/home/other-user", "PRIVATE_FILE_CONTENT", TEST_KEY):
        assert sensitive_value not in response.text
    assert api.provisioning_calls == ["alice"]
    assert api.main._processes == {}
    assert api.main._terminal_sessions == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "auth", "token": "incorrect-key"},
        {"type": "auth", "token": None},
        {"type": "resize", "cols": 80, "rows": 24},
        [],
    ],
)
def test_websocket_authentication_precedes_lookup_of_a_missing_terminal(api, payload):
    with pytest.raises(WebSocketDisconnect) as rejected:
        with api.client.websocket_connect("/api/terminals/unknown", headers={"X-User-Id": "alice"}) as ws:
            ws.send_json(payload)
            ws.receive_text()
    assert rejected.value.code == 4001
    assert api.provisioning_calls == []


@pytest.mark.parametrize("identity", [None, "", " ", "alice/bob", "a" * 129])
def test_websocket_identity_is_checked_after_key_before_resource_lookup(api, identity):
    headers = {} if identity is None else {"X-User-Id": identity}
    with pytest.raises(WebSocketDisconnect) as rejected:
        with api.client.websocket_connect("/api/terminals/unknown", headers=headers) as ws:
            ws.send_json({"type": "auth", "token": TEST_KEY})
            ws.receive_text()
    assert rejected.value.code == 4001
    assert rejected.value.reason == "Invalid user identity"
    assert api.provisioning_calls == []


def test_websocket_rejects_duplicate_identity_headers(api):
    headers = httpx.Headers([("X-User-Id", "alice"), ("x-user-id", "bob")])
    with pytest.raises(WebSocketDisconnect) as rejected:
        with api.client.websocket_connect("/api/terminals/unknown", headers=headers) as ws:
            ws.send_json({"type": "auth", "token": TEST_KEY})
            ws.receive_text()
    assert rejected.value.code == 4001
    assert rejected.value.reason == "Invalid user identity"
    assert api.provisioning_calls == []


def test_websocket_valid_identity_for_missing_resource_gets_not_found(api):
    with pytest.raises(WebSocketDisconnect) as rejected:
        with api.client.websocket_connect("/api/terminals/unknown", headers={"X-User-Id": "alice"}) as ws:
            ws.send_json({"type": "auth", "token": TEST_KEY})
            ws.receive_text()
    assert rejected.value.code == 4004
    assert api.provisioning_calls == []


def test_websocket_other_owner_cannot_connect_or_change_existing_terminal(api):
    # Only external process liveness is substituted. The production ownership
    # comparison must deny before accessing a PTY, starting I/O, or connecting.
    terminal = {
        "backend": "pty",
        "process": SimpleNamespace(poll=lambda: None),
        "user_id": "alice",
        "chat_id": "chat-1",
        "connected": False,
    }
    api.main._terminal_sessions["owned-terminal"] = terminal
    with pytest.raises(WebSocketDisconnect) as rejected:
        with api.client.websocket_connect(
            "/api/terminals/owned-terminal",
            headers={"X-User-Id": "bob", "X-Session-Id": "chat-1"},
        ) as ws:
            ws.send_json({"type": "auth", "token": TEST_KEY})
            ws.receive_text()
    assert rejected.value.code == 4004
    assert terminal["connected"] is False
    assert api.main._terminal_sessions == {"owned-terminal": terminal}
    assert api.provisioning_calls == []
