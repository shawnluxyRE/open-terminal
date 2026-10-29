"""Additional REQ-001 transport regressions, without provisioning OS accounts."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

from open_terminal.utils.identity import WorkspaceProvisionError


TEST_KEY = "req001-test-key-not-a-secret"


@pytest.fixture
def transport(monkeypatch):
    from open_terminal import dependencies, main

    def forbidden_provisioning(_user_id):
        pytest.fail("This request must not prepare an OS account")

    monkeypatch.setattr(dependencies, "resolve_user", forbidden_provisioning)
    monkeypatch.setattr(main, "_processes", {})
    monkeypatch.setattr(main, "_terminal_sessions", {})
    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        yield SimpleNamespace(client=client, dependencies=dependencies, main=main)
    finally:
        client.close()


@pytest.mark.parametrize("path", ["/files/cwd", "/ports", "/proxy/18080/"])
def test_unknown_provisioning_code_is_redacted_before_port_scanning(transport, monkeypatch, path):
    calls = []
    private_detail = "private-id /home/private-space " + TEST_KEY

    def unavailable(user_id):
        calls.append(user_id)
        raise WorkspaceProvisionError(private_detail, private_detail)

    def forbidden_scan():
        pytest.fail("Port scanning must wait until identity and preparation succeed")

    monkeypatch.setattr(transport.dependencies, "resolve_user", unavailable)
    monkeypatch.setattr(transport.main, "detect_listening_ports", forbidden_scan)
    response = transport.client.get(
        path, headers={"Authorization": f"Bearer {TEST_KEY}", "X-User-Id": "alice"}
    )
    assert response.status_code == 503
    assert response.json() == {
        "detail": {"code": "workspace_unavailable", "message": "Workspace preparation failed"}
    }
    assert calls == ["alice"]
    assert private_detail not in response.text


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"x-user-id", b" alice")],
        [(b"x-user-id", b"alice"), (b"X-USER-ID", b"alice")],
        [(b"x-user-id", b"alice"), (b"X-USER-ID", b"bob")],
    ],
)
def test_direct_filesystem_call_enforces_identity_without_route_dependencies(transport, headers):
    with pytest.raises(HTTPException) as rejected:
        transport.dependencies.get_filesystem(Request({"type": "http", "headers": headers}))
    assert rejected.value.status_code == 400
    assert rejected.value.detail == {
        "code": "invalid_user_identity", "message": "Invalid user identity"
    }


@pytest.mark.parametrize(
    "token,identity,expected_code",
    [("wrong-key", "alice", 4001), (TEST_KEY, "bob", 4004), (TEST_KEY, " alice", 4001)],
)
def test_rejected_websocket_does_not_inspect_or_cleanup_dead_terminal(
    transport, monkeypatch, token, identity, expected_code
):
    liveness_checks = []

    def dead_process():
        liveness_checks.append(True)
        return 1

    async def forbidden_cleanup(_session_id):
        pytest.fail("Rejected WebSocket must not clean up another terminal")

    terminal = {
        "backend": "pty", "process": SimpleNamespace(poll=dead_process),
        "user_id": "alice", "chat_id": "chat-1", "connected": False,
    }
    transport.main._terminal_sessions["dead-terminal"] = terminal
    monkeypatch.setattr(transport.main, "_cleanup_session", forbidden_cleanup)
    with pytest.raises(WebSocketDisconnect) as rejected:
        with transport.client.websocket_connect(
            "/api/terminals/dead-terminal",
            headers={"X-User-Id": identity, "X-Session-Id": "chat-1"},
        ) as ws:
            ws.send_json({"type": "auth", "token": token})
            ws.receive_text()
    assert rejected.value.code == expected_code
    assert liveness_checks == []
    assert transport.main._terminal_sessions == {"dead-terminal": terminal}
    assert terminal["connected"] is False


@pytest.mark.parametrize(
    "chat_header,chat_payload,expected_code",
    [("chat-1", "other-chat", 4009), (None, "chat-1", 4009),
     ("other-chat", "chat-1", 4004), ("", "chat-1", 4004)],
)
def test_websocket_chat_header_precedence_and_first_frame_fallback(
    transport, chat_header, chat_payload, expected_code
):
    # A connected live terminal lets the real owner check complete without
    # substituting identity or needing PTY I/O in this transport-only test.
    terminal = {
        "backend": "pty", "process": SimpleNamespace(poll=lambda: None),
        "user_id": "alice", "chat_id": "chat-1", "connected": True,
    }
    transport.main._terminal_sessions["connected-terminal"] = terminal
    headers = {"X-User-Id": "alice"}
    if chat_header is not None:
        headers["X-Session-Id"] = chat_header
    with pytest.raises(WebSocketDisconnect) as rejected:
        with transport.client.websocket_connect("/api/terminals/connected-terminal", headers=headers) as ws:
            ws.send_json({"type": "auth", "token": TEST_KEY, "chat_id": chat_payload})
            ws.receive_text()
    assert rejected.value.code == expected_code
    assert terminal["connected"] is True


class ObservedRuntime(dict):
    """Record reads of existing runtime state without replacing route logic."""

    def __init__(self, entries, observations):
        super().__init__(entries)
        self.observations = observations

    def get(self, *args):
        self.observations.append("get")
        return super().get(*args)

    def values(self):
        self.observations.append("values")
        return super().values()

    def items(self):
        self.observations.append("items")
        return super().items()

    def __contains__(self, key):
        self.observations.append("contains")
        return super().__contains__(key)


@pytest.fixture
def existing_runtime(monkeypatch):
    from open_terminal import dependencies, main

    observations = []
    process = object()
    terminal = {
        "backend": "pty",
        "process": SimpleNamespace(poll=lambda: None),
        "user_id": "Alice",
        "chat_id": "chat-1",
        "connected": True,
    }
    processes = ObservedRuntime({"owned-process": process}, observations)
    terminals = ObservedRuntime({"owned-terminal": terminal}, observations)
    monkeypatch.setattr(main, "_processes", processes)
    monkeypatch.setattr(main, "_terminal_sessions", terminals)

    def forbidden_preparation(_identity):
        observations.append("prepare-account")
        raise AssertionError("This request must not prepare an account")

    def forbidden_process_cleanup():
        observations.append("cleanup-processes")
        raise AssertionError("Rejected HTTP requests must not clean up processes")

    async def forbidden_terminal_cleanup(_session_id):
        observations.append("cleanup-terminal")
        raise AssertionError("This request must not clean up a terminal")

    monkeypatch.setattr(dependencies, "resolve_user", forbidden_preparation)
    monkeypatch.setattr(main, "_cleanup_expired", forbidden_process_cleanup)
    monkeypatch.setattr(main, "_cleanup_session", forbidden_terminal_cleanup)
    # Do not enter the lifespan: background maintenance is not an incoming
    # request and may legitimately inspect runtime state.
    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        yield SimpleNamespace(
            client=client, observations=observations, processes=processes,
            terminals=terminals, process=process, terminal=terminal,
        )
    finally:
        client.close()


@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("GET", "/execute", {}),
        ("POST", "/execute", {"json": {"command": "true"}}),
        ("GET", "/execute/owned-process/status", {}),
        ("POST", "/execute/owned-process/input", {"json": {"input": "example"}}),
        ("DELETE", "/execute/owned-process", {}),
        ("GET", "/api/terminals", {}),
        ("POST", "/api/terminals", {}),
        ("GET", "/api/terminals/owned-terminal", {}),
        ("DELETE", "/api/terminals/owned-terminal", {}),
        ("GET", "/api/terminals/user/output", {}),
        ("POST", "/api/terminals/user/input", {"json": {"input": "example"}}),
    ],
)
@pytest.mark.parametrize(
    "key,identity_headers,status",
    [
        ("wrong-key", [("X-User-Id", "Alice")], 401),
        (TEST_KEY, [], 400),
        (TEST_KEY, [("X-User-Id", "Alice"), ("x-user-id", "Alice")], 400),
    ],
)
def test_rejected_http_requests_do_not_touch_existing_runtime(
    existing_runtime, method, path, kwargs, key, identity_headers, status
):
    """Design: key -> exact identity -> runtime lookup/cleanup/mutation."""
    runtime = existing_runtime
    response = runtime.client.request(
        method, path,
        headers=[("Authorization", f"Bearer {key}"), ("X-Session-Id", "chat-1"), *identity_headers],
        **kwargs,
    )
    assert response.status_code == status, response.text
    if status == 400:
        assert response.json() == {
            "detail": {"code": "invalid_user_identity", "message": "Invalid user identity"}
        }
    assert runtime.observations == []
    assert runtime.processes == {"owned-process": runtime.process}
    assert runtime.terminals == {"owned-terminal": runtime.terminal}
    assert runtime.terminal["connected"] is True


@pytest.mark.parametrize(
    "header_identity,payload_identity,expected_code",
    [
        (None, "Alice", 4001),
        ("Bob", "Alice", 4004),
        ("alice", "Alice", 4004),
        ("Alice", "Bob", 4009),
    ],
)
def test_websocket_first_frame_identity_cannot_override_header(
    existing_runtime, header_identity, payload_identity, expected_code
):
    """Only the exact header selects a user; chat_id alone has a body fallback."""
    runtime = existing_runtime
    headers = {"X-Session-Id": "chat-1"}
    if header_identity is not None:
        headers["X-User-Id"] = header_identity
    with pytest.raises(WebSocketDisconnect) as rejected:
        with runtime.client.websocket_connect("/api/terminals/owned-terminal", headers=headers) as ws:
            ws.send_json({"type": "auth", "token": TEST_KEY, "user_id": payload_identity})
            ws.receive_text()
    assert rejected.value.code == expected_code
    assert runtime.terminals == {"owned-terminal": runtime.terminal}
    assert runtime.terminal["connected"] is True
    assert "prepare-account" not in runtime.observations
    assert "cleanup-terminal" not in runtime.observations
    if header_identity is None:
        assert runtime.observations == []
