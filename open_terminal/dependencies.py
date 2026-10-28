import hmac
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.requests import HTTPConnection

from open_terminal.env import API_KEY, MULTI_USER
from open_terminal.utils.fs import UserFS

if MULTI_USER:
    from open_terminal.utils.user_isolation import resolve_user


bearer_scheme = HTTPBearer(auto_error=False)


def require_user_identity(connection: HTTPConnection) -> str | None:
    """REQ-001 resource identity dependency (R2 skeleton; not yet wired).

    Preconditions: HTTP callers run verify_api_key before this dependency; WS
    callers validate the existing first-message service token before invoking
    the shared identity parsing logic. This function never provisions accounts.

    In multi-user mode require exactly one X-User-Id header (case insensitive
    name), pass its original value to utils.identity.validate_user_id and return
    that exact ID. Missing, duplicate or invalid values raise HTTPException(400)
    with detail={"code": "invalid_user_identity", "message": "Invalid user identity"}.
    No raw ID or credential is included. In explicit single-user mode return
    None without requiring a header, importing Linux modules or creating state.

    R4 must attach this only to user-resource HTTP routes, including list/control
    routes which do not call get_filesystem. Metadata routes retain their existing
    API-key policy and do not require identity. get_filesystem must also call it
    because callers outside the route dependency chain use that function directly.
    Do not make a general service-key check implicitly provision an account.

    WS maps the same invalid identity to close code 4001 after authenticating;
    valid identities use the existing user/chat ownership check (4004 on mismatch).
    Keep WS transport error handling outside this HTTPException-based adapter.
    """
    raise NotImplementedError("REQ-001: implement resource identity dependency")


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
    if not MULTI_USER:
        return UserFS()
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=400, detail="X-User-Id is required in multi-user mode")
    username, home = resolve_user(user_id)
    return UserFS(username=username, home=home)
