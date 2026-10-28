"""REQ-001 identity contracts, shared by HTTP, WebSocket and Linux provisioning.

R2 interface skeleton: the validator intentionally has no business implementation.
See ``docs/design/workspace-identity.md`` for integration and error translation.
This module must remain importable without Linux-only modules in single-user mode.
"""

from dataclasses import dataclass


class InvalidUserIdentity(ValueError):
    """The upstream identity is missing or outside the supported wire grammar.

    Transport adapters expose a fixed message/code, never the rejected header.
    This error does not assert whether a syntactically valid ID exists upstream:
    Open WebUI authenticates accounts; Open Terminal trusts the administrator's
    authenticated service connection. Raising it must not create OS resources.
    """


class WorkspaceProvisionError(RuntimeError):
    """A workspace could not be safely made available.

    ``code`` is a stable diagnostic identifier from the design's error table.
    ``message`` is an operator-safe explanation; it must not contain API keys,
    other users' IDs, paths, file contents, or raw privileged-command stderr.
    HTTP adapters translate this exception; the provisioning layer has no HTTP
    dependency. A failure never authorizes fallback to the service account.
    """

    def __init__(self, code: str, message: str = "Workspace preparation failed"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkspaceAccount:
    """A completely prepared, verified binding; never a partially created user.

    Attributes:
        user_id: Exact, case-sensitive upstream ID accepted by validate_user_id.
        username: Linux account name and identically named private primary group.
        home: Absolute owned directory below the configured home root. Existing
            UserFS consumes this value without changing its public interface.
        uid: Actual passwd UID. Newly created accounts use 10001..19999.
        gid: Actual private primary group GID, independently allocated in the
            same range. It need not equal uid. An existing verified account may
            retain a previously assigned out-of-range, non-reserved uid/gid.

    Returning this object means account, group, directory permissions and the
    calling server process's group access are ready. The object is not an
    authentication token and must not be used as a persistent cache of readiness.
    """

    user_id: str
    username: str
    home: str
    uid: int
    gid: int


def validate_user_id(value: str | None) -> str:
    """Validate and return an upstream ID unchanged, without any side effects.

    Accepted grammar: ASCII ``[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}`` (full match).
    Length is 1..128 characters. Short IDs, names such as ``root``, UUIDs and
    mixed case are valid upstream identifiers, not requests to reuse OS names.
    Reject None, non-strings, empty values, whitespace anywhere, control bytes,
    non-ASCII characters, slashes and overlong values with InvalidUserIdentity.
    Do not trim, lowercase, normalize, truncate or silently substitute a value.
    For example ``A`` and ``a`` identify different accounts; ``' A'`` is invalid.

    Transport adapters additionally reject repeated X-User-Id header fields.
    Display name, login session and chat ID never affect this value. The function
    does not authenticate the caller, look up an upstream user or touch storage.
    """
    raise NotImplementedError("REQ-001: implement exact upstream identity validation")
