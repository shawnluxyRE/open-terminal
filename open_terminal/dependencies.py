import hmac
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.requests import HTTPConnection

from open_terminal.env import API_KEY, MULTI_USER, PUBLIC_READ_ROOTS
from open_terminal.utils.fs import UserFS
from open_terminal.utils.identity import InvalidUserIdentity, WorkspaceProvisionError, validate_user_id

if MULTI_USER:
    from open_terminal.utils.user_isolation import resolve_user


bearer_scheme = HTTPBearer(auto_error=False)

_WORKSPACE_ERROR_STATUS = {
    "identity_conflict": 409,
    "uid_pool_exhausted": 503,
    "gid_pool_exhausted": 503,
    "username_allocation_failed": 503,
    "workspace_busy": 503,
    "workspace_unavailable": 503,
    "workspace_state_invalid": 500,
    "workspace_config_invalid": 500,
}


def require_user_identity(connection: HTTPConnection) -> str | None:
    """Validate one resource identity header after service-key authentication.

    Multi-user mode returns the exact X-User-Id; missing, repeated or invalid
    headers raise HTTP 400 with the fixed ``invalid_user_identity`` detail.
    Single-user mode returns None. This dependency never prepares an account.
    WS callers translate invalid identity to close code 4001; metadata routes
    keep their existing policy. See docs/design/workspace-identity.md.
    """
    if not MULTI_USER:
        return None
    # Read the raw ASGI fields so duplicate values cannot be merged or selected.
    values = [value for name, value in connection.scope.get("headers", []) if name.lower() == b"x-user-id"]
    try:
        if len(values) != 1:
            raise InvalidUserIdentity("Invalid user identity")
        return validate_user_id(values[0].decode("latin-1"))
    except InvalidUserIdentity:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_user_identity", "message": "Invalid user identity"},
        ) from None


def matches_api_key(token: object) -> bool:
    # compare_digest rejects non-ASCII strings; malformed network input is not an error.
    try:
        return isinstance(token, str) and hmac.compare_digest(token.encode("utf-8"), API_KEY.encode("utf-8"))
    except UnicodeEncodeError:
        return False


async def verify_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    if not API_KEY:
        return
    if not credentials or not matches_api_key(credentials.credentials):
        raise HTTPException(status_code=401, detail="Invalid API key")


def get_filesystem(request: Request) -> UserFS:
    """Build a :class:`UserFS` scoped to the requesting user.

    Multi-user mode requires ``X-User-Id`` to select the user's workspace.
    Otherwise returns a plain ``UserFS`` using stdlib.
    """
    user_id = require_user_identity(request)
    if user_id is None:
        return UserFS()
    try:
        username, home = resolve_user(user_id)
    except WorkspaceProvisionError as exc:
        # Never relay command stderr, paths, IDs, or an unrecognized code.
        code = exc.code if exc.code in _WORKSPACE_ERROR_STATUS else "workspace_unavailable"
        raise HTTPException(
            status_code=_WORKSPACE_ERROR_STATUS[code],
            detail={"code": code, "message": "Workspace preparation failed"},
        ) from None
    return UserFS(username=username, home=home, public_read_roots=PUBLIC_READ_ROOTS)
