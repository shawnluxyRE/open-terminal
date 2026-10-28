"""Per-user OS account provisioning for multi-user mode.

When ``OPEN_TERMINAL_MULTI_USER=true``, each distinct ``X-User-Id`` is mapped
to a dedicated Linux user account.  Commands and file operations then run as
that OS user via ``sudo -u``. This separates workspaces inside a shared
container; it is not a security boundary between users.
"""

import hashlib
import logging
import os
import platform
import pwd
import re
import secrets
import shutil
import subprocess
from collections.abc import Collection

from open_terminal.utils.identity import WorkspaceAccount, WorkspaceProvisionError

log = logging.getLogger(__name__)

# In-memory cache: upstream user-id → (os_username, home_dir)
_user_cache: dict[str, tuple[str, str]] = {}

# New-workspace contracts; the legacy functions below remain unchanged in R2.
WORKSPACE_ID_MIN = 10001
WORKSPACE_ID_MAX = 19999
WORKSPACE_NAME_RANDOM_ATTEMPTS = 32


def derive_workspace_username(user_id: str, prefix: str = "") -> str:
    """Return the deterministic candidate OS name for an exact upstream ID.

    First validate with identity.validate_user_id. Take the first eight lowercase
    hex characters of SHA256 over the exact UTF-8 ID, then prepend prefix. Only
    if that combined name starts with a digit, prepend 'u' to the whole name.
    This preserves the old prefix-before-digit-check ordering. Hex letters are
    a..f, not all a..z. For example: 'a' -> 'ca978112', 'alice' -> 'u2bd806c9',
    and 'alice' with prefix='team_' -> 'team_2bd806c9'.

    Prefix must be empty or fully match ``[a-z_][a-z0-9_]{0,6}``; invalid prefixes
    raise ValueError. Without a prefix names have 8 or 9 characters. A nonempty
    valid prefix already supplies a legal leading character, so no 'u' is added;
    the total length is 9..15. The private group has exactly the same name.
    There are no filesystem or OS-account side effects.

    The truncated hash is only the first candidate, NOT ownership evidence.
    For a previously unbound ID, provisioning skips any candidate reserved by
    another ID or occupied by an OS account/group/home, then tries random
    candidates from generate_workspace_username. Once a name has been durably
    bound, reuse that recorded name; do not recompute or replace it on retry.
    Eight hex characters are 32 bits and can collide; length is not proof of
    uniqueness. IDs 'req001-collision-49585' and 'req001-collision-89826', for
    example, both produce 'u19042bfe' without a prefix. Keep full IDs in the
    registry and the full 64-character digest in the OS account marker.
    Do not use the old sanitize_username output for new resource ownership.
    """
    raise NotImplementedError("REQ-001: implement full-ID username derivation")


def generate_workspace_username(prefix: str = "") -> str:
    """Generate one short random candidate without inspecting or reserving it.

    Validate prefix exactly as derive_workspace_username BEFORE requesting any
    randomness; an invalid prefix raises ValueError. Obtain eight lowercase hex
    characters using secrets.token_hex(4), prepend prefix, and prepend 'u' to
    the combined name only when its first character is a digit. The name remains
    8/9 characters with no prefix, or 9..15 with a valid nonempty prefix. Examples
    of entropy output: 'abcdef01' -> 'abcdef01'; '1234abcd' -> 'u1234abcd'; the
    latter with prefix='team_' -> 'team_1234abcd'. Do not lengthen a name with a
    suffix. This helper accepts no upstream ID and makes no ownership decision.

    Randomness does not prove uniqueness. WorkspaceProvisioner checks each
    candidate against registry reservations and real accounts/groups/homes under
    its existing global lock. It tries at most WORKSPACE_NAME_RANDOM_ATTEMPTS
    random draws after the first deterministic candidate; repeated values count
    toward the limit. All candidates occupied raises username_allocation_failed.
    The final chosen name is durably recorded BEFORE any OS object is created.
    The helper itself neither retries nor reads/writes persistent or OS state.
    Entropy-source failures are wrapped by the resolver as workspace_unavailable.
    """
    raise NotImplementedError("REQ-001: implement short random username candidate")


def allocate_workspace_ids(
    occupied_uids: Collection[int], occupied_gids: Collection[int]
) -> tuple[int, int]:
    """Select the lowest available UID and GID independently, without mutation.

    Inputs are snapshots of all occupied NSS IDs plus other in-progress mapping
    reservations. Do not mutate these collections. Values outside the inclusive
    10001..19999 interval have no effect. Return (uid, gid), each inside that
    interval; uid need not equal gid. Duplicate occupied IDs have no extra effect.

    If no UID is available raise WorkspaceProvisionError('uid_pool_exhausted');
    otherwise if no GID is available raise ...('gid_pool_exhausted'). When both
    pools are exhausted UID wins for deterministic diagnostics. Never allocate
    outside the range. The caller holds the provisioning lock from snapshot to
    actual user/group creation and checks BOTH pools before any OS mutation.
    This helper alone cannot reserve IDs or serialize concurrent callers.
    """
    raise NotImplementedError("REQ-001: implement bounded independent ID allocation")


class WorkspaceProvisioner:
    """Prepare exact-ID workspaces against Linux NSS and a persistent registry.

    R2 interface skeleton. R4 integrates resolve_user with this class, preserving
    its existing (username, home) return type and existing UserFS/runner APIs.
    Existing legacy implementations are deliberately retained until then.
    """

    def __init__(
        self,
        *,
        state_dir: str = "/home/.open-terminal",
        home_root: str = "/home",
        username_prefix: str = "",
    ):
        """Store immutable deployment choices; do not create accounts here.

        state_dir contains schema-v1 registry.json and an independent stable
        provision.lock on the retained /home volume. It must be service-owned,
        mode 0700, with 0600 registry/lock files; never placed in a workspace.
        home_root is the absolute parent of generated homes. Explicit alternate
        roots support isolated Linux tests; production uses the defaults above.
        username_prefix follows derive_workspace_username's grammar and comes
        from the existing USER_PREFIX setting. Invalid configuration fails before
        mutation with workspace_config_invalid. Each registry record stores its
        original prefix explicitly; compare that field, not a recomputed hash
        name, because a recorded random fallback is valid. Registry bindings never
        silently move when prefix or root configuration changes.
        """
        self.state_dir = state_dir
        self.home_root = home_root
        self.username_prefix = username_prefix

    def resolve(self, user_id: str) -> WorkspaceAccount:
        """Idempotently return a usable binding for an exact valid upstream ID.

        Validate identity before touching persistent state. Serialize the entire
        read/verify/reserve/create/repair/publish sequence using a process-wide
        threading lock AND flock on provision.lock; all instances share that lock.
        Lock acquisition has a total 30-second deadline (workspace_busy). The
        registry is atomic JSON with fsync/replace and an unchanged lock inode.

        New bindings: inspect NSS and all registry reservations under the lock.
        Try the deterministic candidate first, then at most 32 random candidates
        if the name, private group or home is occupied/reserved. Never adopt or
        modify an occupied candidate, including an unrecorded directory/symlink.
        If all attempts collide, raise username_allocation_failed without a new
        binding or OS objects. This does not claim the entire name space is full.
        Preflight both ID pools, durably record the chosen name, prefix, home and
        numbers in planned ownership, then explicitly groupadd -g / useradd -u -g
        with an explicit home and shell. Account GECOS is 'ot-' + the full SHA256
        of the exact ID, even when its chosen username is random.
        Never use -o/non-unique, change root/server/other users' IDs, or allocate
        an out-of-range ID. Root, UID<1000, and the service real/effective UIDs
        are never acceptable workspace accounts, even if a record names them.

        Reuse: look up the exact ID BEFORE deriving or drawing a candidate. Use
        its durably chosen name for planned/group_ready/ready, even after partial
        failure, process restart or account recreation. Compare the entire ID,
        recorded prefix/name/path, UID/GID, group and account marker; do not
        require the name to equal derive_workspace_username(user_id). Conflicting
        existing bindings fail identity_conflict, never get a new random name.
        A present verified out-of-range account retains its IDs; an absent account
        is recreated using the current interval and existing ownership record.
        Inconsistent/corrupt/unknown-schema state fails closed; a symlink or
        non-directory at an already bound home is identity_conflict. For an
        unbound user, an unrecorded home entry (including a dangling symlink)
        instead makes that candidate occupied and must be skipped. Do not infer
        ownership from a name or file UID.

        Retry: resume only objects belonging to a durable planned binding; see
        the design's state transition/recovery table. Do not delete homes or run
        blanket rollback/userdel on failure. Only the proven owner's directory
        can be re-chowned after account recreation. Preserve file bytes/modes
        except required owner and home-directory permission repair.

        Before ready: private group has the account's name, home is 2770, server
        has required group membership and current-process supplementary groups
        are refreshed. Do not derive the server user from a client-controlled
        environment variable. Retain existing optional Docker group behavior.
        Required permission setup failure raises workspace_unavailable; do not
        cache or return a partially prepared account as ready. Repeated calls
        recheck readiness, including on a different process/instance.

        Raises InvalidUserIdentity or WorkspaceProvisionError with stable code;
        sanitized diagnostics contain no other-user data/credentials. OS errors
        are wrapped; no fallback to service/default/shared account. Calls may
        create OS accounts/groups, journal records and owned home permissions;
        they never execute user commands or create a terminal/session. Expected
        cases and response mapping are in docs/design/workspace-identity.md.
        """
        raise NotImplementedError("REQ-001: implement ownership-aware workspace preparation")


def _run_privileged(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a command with appropriate privilege escalation.

    When the process is already running as root (UID 0), the command is
    executed directly.  Otherwise ``sudo`` is prepended.
    """
    if os.getuid() == 0:
        return subprocess.run(cmd, check=True, capture_output=True)
    return subprocess.run(["sudo", *cmd], check=True, capture_output=True)


def check_environment() -> None:
    """Validate that the host supports multi-user mode.

    Raises ``RuntimeError`` at startup when the platform is not Linux or
    the required privilege escalation tools are not available.
    """
    if platform.system() != "Linux":
        raise RuntimeError(
            "OPEN_TERMINAL_MULTI_USER requires Linux "
            f"(current platform: {platform.system()})"
        )
    if shutil.which("useradd") is None:
        raise RuntimeError(
            "OPEN_TERMINAL_MULTI_USER requires useradd to be installed"
        )
    if os.getuid() != 0 and shutil.which("sudo") is None:
        raise RuntimeError(
            "OPEN_TERMINAL_MULTI_USER requires either running as root "
            "or sudo to be installed. Use the standard image, run with "
            "user: '0:0', or use Terminals for container-per-user isolation."
        )


def sanitize_username(user_id: str) -> str:
    """Convert an arbitrary user ID into a valid Linux username.

    Uses the first 8 lowercase alphanumeric characters of the user ID,
    optionally prefixed by ``OPEN_TERMINAL_USER_PREFIX``.  Prepends ``u``
    only when the result starts with a digit (Linux usernames must begin
    with a letter or underscore).  Falls back to a short hash when the ID
    contains fewer than 4 usable characters.
    """
    from open_terminal.env import USER_PREFIX

    cleaned = re.sub(r"[^a-z0-9]", "", user_id.lower())
    if len(cleaned) >= 4:
        name = cleaned[:8]
    else:
        # Fallback: hash-based name for very short / non-alphanumeric IDs
        name = hashlib.sha256(user_id.encode()).hexdigest()[:8]
    name = f"{USER_PREFIX}{name}"
    # Linux usernames must start with a letter or underscore
    if name[0].isdigit():
        name = f"u{name}"
    return name


def ensure_os_user(username: str) -> str:
    """Create the OS user if it doesn't exist (idempotent).

    Sets ``chmod 2770`` on the home directory and adds the server process
    user to the new user's primary group.  This allows native Python I/O
    for reads while other provisioned users still get ``Permission denied``.
    Returns the home directory path.
    """
    try:
        pw = pwd.getpwnam(username)
        if pw.pw_uid < 1000 or pw.pw_uid in (os.getuid(), os.geteuid()):
            raise RuntimeError(f"Refusing to use reserved account {username!r}")
        return pw.pw_dir
    except KeyError:
        pass  # User doesn't exist yet — create below

    log.info("Provisioning OS user: %s", username)
    _run_privileged(["useradd", "-m", "-s", "/bin/bash", username])
    home_dir = f"/home/{username}"
    # Fix ownership (home dir may pre-exist from a previous run with a
    # different UID assignment) and set permissions.
    _run_privileged(["chown", "-R", f"{username}:{username}", home_dir])
    _run_privileged(["chmod", "2770", home_dir])
    # Add the server process user to the new user's group so Python can
    # read files natively without subprocess.
    server_user = os.getenv("USER", "user")
    _run_privileged(["usermod", "-aG", username, server_user])
    # If the Docker socket is mounted, add the new user to its group
    # so docker commands work without sudo (mirrors entrypoint.sh).
    _DOCKER_SOCK = "/var/run/docker.sock"
    if os.path.exists(_DOCKER_SOCK):
        import grp as _grp
        sock_gid = os.stat(_DOCKER_SOCK).st_gid
        try:
            sock_group = _grp.getgrgid(sock_gid).gr_name
            _run_privileged(["usermod", "-aG", sock_group, username])
            log.info("Added %s to Docker socket group '%s'", username, sock_group)
        except (KeyError, subprocess.CalledProcessError) as exc:
            log.warning("Could not add %s to Docker socket group: %s", username, exc)
    # Refresh the running process's supplementary group list so the new
    # group takes effect immediately (normally requires re-login).
    import ctypes
    import ctypes.util
    import grp

    pw = pwd.getpwnam(server_user)
    group_ids = sorted({
        g.gr_gid for g in grp.getgrall() if server_user in g.gr_mem
    } | {pw.pw_gid})
    try:
        os.setgroups(group_ids)
    except PermissionError:
        # Container user may lack CAP_SETGID.  The group will take effect
        # after the next process restart; log a warning and continue.
        log.warning(
            "Could not refresh supplementary groups (missing CAP_SETGID). "
            "Restart the server for group changes to take effect."
        )
    return home_dir


def resolve_user(user_id: str) -> tuple[str, str]:
    """Map an upstream user ID to an OS user, provisioning if needed.

    Returns ``(username, home_dir)``.  Results are cached in-memory so
    repeated requests for the same user skip the syscall / subprocess.
    """
    cached = _user_cache.get(user_id)
    if cached is not None:
        return cached

    username = sanitize_username(user_id)
    home_dir = ensure_os_user(username)
    _user_cache[user_id] = (username, home_dir)
    return username, home_dir
