"""REQ-001: direct contracts for the identity-only transport adapter.

This calls the real declared dependency, including its real validator. There
is no dependency override returning an invented identity. The only forbidden
boundary is OS account preparation, which this adapter must never invoke.
"""

import builtins

import pytest
from fastapi import HTTPException
from starlette.requests import HTTPConnection


def connection_with(headers, scope_type="http"):
    """Build ASGI-normalized headers, preserving repeated field entries."""
    return HTTPConnection({"type": scope_type, "headers": headers})


@pytest.fixture
def adapter(monkeypatch):
    from open_terminal import dependencies

    monkeypatch.setattr(dependencies, "MULTI_USER", True)

    def forbidden_provisioning(_user_id):
        pytest.fail("An identity-only dependency must not prepare an OS account")

    monkeypatch.setattr(dependencies, "resolve_user", forbidden_provisioning)
    return dependencies


@pytest.mark.parametrize("identity", ["a", "root", "Alice", "alice", "a.b:c@d-_", "a" * 128])
def test_identity_dependency_preserves_the_exact_header(adapter, identity):
    connection = connection_with([(b"x-user-id", identity.encode("ascii"))])
    assert adapter.require_user_identity(connection) == identity


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"x-user-id", b"")],
        [(b"x-user-id", b" alice")],
        [(b"x-user-id", b"alice/bob")],
        [(b"x-user-id", b"a" * 129)],
        [(b"x-user-id", b"alice"), (b"x-user-id", b"alice")],
        [(b"x-user-id", b"alice"), (b"x-user-id", b"bob")],
    ],
)
def test_identity_dependency_rejects_missing_invalid_or_repeated_headers(adapter, headers):
    with pytest.raises(HTTPException) as rejected:
        adapter.require_user_identity(connection_with(headers))
    assert rejected.value.status_code == 400
    assert rejected.value.detail == {
        "code": "invalid_user_identity",
        "message": "Invalid user identity",
    }


def test_identity_dependency_accepts_a_websocket_connection_after_service_auth(adapter):
    connection = connection_with([(b"x-user-id", b"Alice")], scope_type="websocket")
    assert adapter.require_user_identity(connection) == "Alice"


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"x-user-id", b"alice/bob")],
        [(b"x-user-id", b"alice"), (b"x-user-id", b"bob")],
    ],
)
def test_explicit_single_user_mode_needs_no_identity_or_linux_provisioning(adapter, monkeypatch, headers):
    monkeypatch.setattr(adapter, "MULTI_USER", False)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in {"pwd", "grp", "open_terminal.utils.user_isolation"}:
            pytest.fail("Single-user identity handling must not import Linux account modules")
        return original_import(name, *args, **kwargs)

    # Dependencies are already loaded by this test process. The guard verifies
    # that this call adds no Linux provisioning imports; it is not a claim that
    # the full app has been exercised on Windows or other operating systems.
    with monkeypatch.context() as guarded:
        guarded.setattr(builtins, "__import__", guarded_import)
        identity = adapter.require_user_identity(connection_with(headers))
    assert identity is None
