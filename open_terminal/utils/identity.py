"""REQ-001 identity contracts, shared by HTTP, WebSocket and Linux provisioning.

See ``docs/design/workspace-identity.md`` for integration and error translation.
This module must remain importable without Linux-only modules in single-user mode.
"""

import re
from dataclasses import dataclass


_USER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}")


class InvalidUserIdentity(ValueError):
    """Missing or malformed upstream ID; this does not authenticate an account."""


class WorkspaceProvisionError(RuntimeError):
    """Workspace preparation failure with a stable diagnostic ``code``.

    Keep messages free of credentials, other-user data and raw command stderr.
    Callers translate the code; failures never permit a service-account fallback.
    """

    def __init__(self, code: str, message: str = "Workspace preparation failed"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkspaceAccount:
    """Verified binding whose home is accessible to the current server process.

    ``user_id`` is exact and case-sensitive; ``username`` also names its private
    primary group. ``home`` is absolute; ``uid`` and ``gid`` are actual OS IDs.
    This immutable value is neither an auth token nor a persistent readiness cache.
    """

    user_id: str
    username: str
    home: str
    uid: int
    gid: int


def validate_user_id(value: str | None) -> str:
    """Return the ID unchanged, or raise InvalidUserIdentity for invalid input.

    Require a string matching ASCII ``[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}``.
    Do not trim or normalize; case remains significant. No I/O or authentication.
    """
    if not isinstance(value, str) or _USER_ID.fullmatch(value) is None:
        raise InvalidUserIdentity("Invalid user identity")
    return value
