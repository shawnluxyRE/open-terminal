"""Provision per-user Linux accounts and persistent homes in multi-user mode.

Ownership and recovery contracts: docs/design/workspace-identity.md.
Shared-container workspaces are not an adversarial security boundary.
"""

import hashlib
import fcntl
import grp
import json
import logging
import os
import platform
import pwd
import re
import secrets
import shutil
import subprocess
import stat
import tempfile
import threading
import time
from collections.abc import Collection
from contextlib import contextmanager
from contextvars import ContextVar

from open_terminal.utils.identity import (
    InvalidUserIdentity,
    WorkspaceAccount,
    WorkspaceProvisionError,
    validate_user_id,
)

log = logging.getLogger(__name__)

WORKSPACE_ID_MIN = 10001
WORKSPACE_ID_MAX = 19999
WORKSPACE_NAME_RANDOM_ATTEMPTS = 32
_PREPARATION_TIMEOUT = 30.0
_COMMAND_TIMEOUT = 30.0
_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()
_operation: ContextVar[tuple[str, str] | None] = ContextVar("workspace_operation", default=None)


def _step(name: str) -> None:
    context = _operation.get()
    if context is not None:
        _operation.set((context[0], name))


def _validate_prefix(prefix: str) -> None:
    if not isinstance(prefix, str) or (
        prefix and re.fullmatch(r"[a-z_][a-z0-9_]{0,6}", prefix) is None
    ):
        raise ValueError("Invalid workspace username prefix")


def _candidate_name(digest: str, prefix: str) -> str:
    name = prefix + digest
    return "u" + name if name[0].isdigit() else name


def derive_workspace_username(user_id: str, prefix: str = "") -> str:
    """Derive a first candidate from the exact ID's eight-character SHA256 prefix.

    Prepend prefix, then add 'u' only if the combined name starts with a digit.
    Prefix must be empty or match ``[a-z_][a-z0-9_]{0,6}`` (otherwise ValueError);
    invalid IDs raise InvalidUserIdentity. No I/O or uniqueness guarantee.
    Example: 'alice' -> 'u2bd806c9'; with 'team_' -> 'team_2bd806c9'.
    """
    validate_user_id(user_id)
    _validate_prefix(prefix)
    return _candidate_name(hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:8], prefix)


def generate_workspace_username(prefix: str = "") -> str:
    """Draw one candidate with secrets.token_hex(4), using the same naming rules.

    Validate prefix before drawing (ValueError if invalid). This helper neither
    checks occupancy nor reserves a name; the resolver handles retries.
    """
    _validate_prefix(prefix)
    return _candidate_name(secrets.token_hex(4), prefix)


def allocate_workspace_ids(
    occupied_uids: Collection[int], occupied_gids: Collection[int]
) -> tuple[int, int]:
    """Return independent lowest free (uid, gid) values in 10001..19999 inclusive.

    Inputs include occupied IDs and reservations; they are not changed. Exhaustion
    raises WorkspaceProvisionError with uid_pool_exhausted or gid_pool_exhausted;
    UID takes precedence when both are full. This helper does not reserve or lock.
    """
    used_uids, used_gids = set(occupied_uids), set(occupied_gids)
    uid = next((n for n in range(WORKSPACE_ID_MIN, WORKSPACE_ID_MAX + 1) if n not in used_uids), None)
    if uid is None:
        raise WorkspaceProvisionError("uid_pool_exhausted")
    gid = next((n for n in range(WORKSPACE_ID_MIN, WORKSPACE_ID_MAX + 1) if n not in used_gids), None)
    if gid is None:
        raise WorkspaceProvisionError("gid_pool_exhausted")
    return uid, gid


class WorkspaceProvisioner:
    """Prepare and verify persistent full-ID bindings to Linux workspaces.

    State transitions and failure rules: docs/design/workspace-identity.md.
    """

    def __init__(
        self,
        *,
        state_dir: str = "/home/.open-terminal",
        home_root: str = "/home",
        username_prefix: str = "",
    ):
        """Store deployment paths and prefix without creating resources.

        state_dir holds the retained registry and lock; home_root contains user homes.
        resolve validates these settings before I/O; invalid configuration raises
        WorkspaceProvisionError with workspace_config_invalid.
        """
        self.state_dir = state_dir
        self.home_root = home_root
        self.username_prefix = username_prefix

    def resolve(self, user_id: str) -> WorkspaceAccount:
        """Return a ready workspace for the exact ID, creating or restoring it.

        Validate before I/O; serialize ownership checks and preparation under locks.
        For new users, try the hash name then up to 32 random candidates; persist the
        binding before OS creation. Reuse saved names on retry/rebuild; never rename
        to bypass a conflicting binding. New UID/GID values stay in 10001..19999;
        verified existing accounts retain their IDs, including out-of-range values.
        Return only after home ownership/permissions and server access are ready.
        InvalidUserIdentity or WorkspaceProvisionError leaves user data intact and
        never falls back to a shared account. State/error details are in the design.
        """
        validate_user_id(user_id)
        self._validate_configuration()
        # Correlation is deliberately unrelated to the upstream identity or its
        # directory. No account arguments, command stderr or credentials are logged.
        operation_id = hashlib.sha256(
            f"{os.getpid()}:{threading.get_ident()}:{time.monotonic_ns()}".encode()
        ).hexdigest()[:16]
        token = _operation.set((operation_id, "service_account"))
        try:
            server = pwd.getpwuid(os.getuid())
            with self._locked(server):
                registry = self._load_registry(server)
                users = registry["users"]
                record = users.get(user_id)
                if record is None:
                    name = self._choose_name(user_id, users)
                    uid, gid = self._allocate(users)
                    record = {
                        "username": name,
                        "prefix": self.username_prefix,
                        "home": os.path.join(self.home_root, name),
                        "uid": uid,
                        "gid": gid,
                        "state": "planned",
                    }
                    users[user_id] = record
                    self._save_registry(registry)
                return self._prepare(user_id, record, registry, server)
        except WorkspaceProvisionError as exc:
            _, step = _operation.get()
            log.warning("Workspace preparation rejected: operation=%s step=%s code=%s", operation_id, step, exc.code)
            raise
        except Exception as exc:
            # Privileged stderr and persistent user data must not enter a public
            # diagnostic (or an unrestricted traceback in the service log).
            _, step = _operation.get()
            log.warning("Workspace preparation failed: operation=%s step=%s error=%s", operation_id, step, type(exc).__name__)
            raise WorkspaceProvisionError("workspace_unavailable") from None
        finally:
            _operation.reset(token)

    def _validate_configuration(self) -> None:
        try:
            _validate_prefix(self.username_prefix)
            for path in (self.state_dir, self.home_root):
                if (
                    not isinstance(path, str) or not os.path.isabs(path)
                    or "\x00" in path or os.path.normpath(path) != path
                ):
                    raise ValueError
            if self.state_dir == self.home_root:
                raise ValueError
            common = os.path.commonpath((self.state_dir, self.home_root))
            if common == self.state_dir or (
                common == self.home_root and os.path.dirname(self.state_dir) != self.home_root
            ):
                # Under home_root, metadata has its own direct child (as in the
                # default /home/.open-terminal), never a user's home descendant.
                raise ValueError
        except (TypeError, ValueError):
            raise WorkspaceProvisionError("workspace_config_invalid") from None

    @staticmethod
    def _verify_metadata(info: os.stat_result, uid: int, *, directory: bool) -> None:
        expected_mode = 0o700 if directory else 0o600
        correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not correct_type or info.st_uid != uid or stat.S_IMODE(info.st_mode) != expected_mode:
            raise WorkspaceProvisionError("workspace_state_invalid")

    @contextmanager
    def _locked(self, server):
        # All instances in a process use the same lock, and all processes open
        # the same independent lock inode. Atomic registry replacement is safe.
        _step("thread_lock")
        key = os.path.normcase(os.path.abspath(self.state_dir))
        with _thread_locks_guard:
            lock = _thread_locks.setdefault(key, threading.Lock())
        deadline = time.monotonic() + _PREPARATION_TIMEOUT
        if not lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise WorkspaceProvisionError("workspace_busy")
        descriptor = None
        try:
            _step("metadata_directory")
            if not stat.S_ISDIR(os.lstat(self.home_root).st_mode):
                raise WorkspaceProvisionError("workspace_config_invalid")
            if not os.path.lexists(self.state_dir):
                # install makes an initially private directory, also when /home
                # itself is root-owned. Parallel initializers use identical args.
                _run_privileged([
                    "install", "-d", "-m", "0700", "-o", str(server.pw_uid),
                    "-g", str(server.pw_gid), self.state_dir,
                ])
            self._verify_metadata(os.lstat(self.state_dir), server.pw_uid, directory=True)
            _step("file_lock")
            lock_path = os.path.join(self.state_dir, "provision.lock")
            if os.path.lexists(lock_path):
                self._verify_metadata(os.lstat(lock_path), server.pw_uid, directory=False)
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
            try:
                descriptor = os.open(lock_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
                os.fchmod(descriptor, 0o600)
            except FileExistsError:
                descriptor = os.open(lock_path, flags)
            self._verify_metadata(os.fstat(descriptor), server.pw_uid, directory=False)
            while True:
                if time.monotonic() >= deadline:
                    raise WorkspaceProvisionError("workspace_busy")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise WorkspaceProvisionError("workspace_busy") from None
                    time.sleep(min(0.05, remaining))
            yield
        finally:
            try:
                if descriptor is not None:
                    os.close(descriptor)
            finally:
                lock.release()

    def _load_registry(self, server) -> dict:
        _step("registry_read")
        path = os.path.join(self.state_dir, "registry.json")
        if not os.path.lexists(path):
            return {"version": 1, "users": {}}
        self._verify_metadata(os.lstat(path), server.pw_uid, directory=False)

        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate registry key")
                result[key] = value
            return result

        try:
            with open(path, encoding="utf-8") as source:
                registry = json.load(source, object_pairs_hook=unique_keys)
            if (
                not isinstance(registry, dict) or set(registry) != {"version", "users"}
                or type(registry["version"]) is not int or registry["version"] != 1
                or not isinstance(registry["users"], dict)
            ):
                raise ValueError
            resources = {field: set() for field in ("username", "home", "uid", "gid")}
            for identity, record in registry["users"].items():
                validate_user_id(identity)
                if not isinstance(record, dict) or set(record) != {
                    "username", "prefix", "home", "uid", "gid", "state"
                }:
                    raise ValueError
                _validate_prefix(record["prefix"])
                prefix, name, home = record["prefix"], record["username"], record["home"]
                shape = re.escape(prefix) + r"[0-9a-f]{8}" if prefix else r"(?:[a-f][0-9a-f]{7}|u[0-9][0-9a-f]{7})"
                if not isinstance(name, str) or re.fullmatch(shape, name) is None:
                    raise ValueError
                if (
                    not isinstance(home, str) or not os.path.isabs(home)
                    or "\x00" in home or os.path.normpath(home) != home
                    or os.path.basename(home) != name
                    or record["state"] not in ("planned", "group_ready", "ready")
                    or any(type(record[field]) is not int or record[field] <= 0 for field in ("uid", "gid"))
                ):
                    raise ValueError
                if prefix != self.username_prefix or os.path.dirname(home) != self.home_root:
                    raise WorkspaceProvisionError("workspace_config_invalid")
                for field, seen in resources.items():
                    if record[field] in seen:
                        raise WorkspaceProvisionError("identity_conflict")
                    seen.add(record[field])
            # A prior replace may be visible even though its directory fsync
            # failed. Under the preparation lock, confirm that commit before
            # any planned/group_ready/ready record can drive OS work or success.
            # No in-memory flag can prove durability to a different process.
            self._sync_registry_directory()
            return registry
        except (ValueError, TypeError, UnicodeError, InvalidUserIdentity):
            raise WorkspaceProvisionError("workspace_state_invalid") from None

    def _save_registry(self, registry: dict) -> None:
        _step("registry_write")
        descriptor, temporary = tempfile.mkstemp(prefix=".registry-", dir=self.state_dir)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                os.fchmod(target.fileno(), 0o600)
                json.dump(registry, target, ensure_ascii=True, sort_keys=True)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, os.path.join(self.state_dir, "registry.json"))
            self._sync_registry_directory()
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _sync_registry_directory(self) -> None:
        """Confirm the visible registry name is durable before using its state."""
        _step("registry_directory_sync")
        directory = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _choose_name(self, user_id: str, users: dict) -> str:
        _step("name_allocation")
        reserved = {record["username"] for record in users.values()}
        accounts = {record.pw_name for record in pwd.getpwall()}
        groups = {record.gr_name for record in grp.getgrall()}
        name = derive_workspace_username(user_id, self.username_prefix)
        for attempt in range(WORKSPACE_NAME_RANDOM_ATTEMPTS + 1):
            if attempt:
                name = generate_workspace_username(self.username_prefix)
            if (
                name not in reserved and name not in accounts and name not in groups
                and not os.path.lexists(os.path.join(self.home_root, name))
            ):
                return name
        raise WorkspaceProvisionError("username_allocation_failed")

    @staticmethod
    def _account(name: str):
        try:
            return pwd.getpwnam(name)
        except KeyError:
            return None

    @staticmethod
    def _group(name: str):
        try:
            return grp.getgrnam(name)
        except KeyError:
            return None

    @staticmethod
    def _occupied(users: dict, own_id: str | None = None) -> tuple[set, set]:
        uids = {account.pw_uid for account in pwd.getpwall()}
        gids = {group.gr_gid for group in grp.getgrall()}
        for identity, record in users.items():
            if identity != own_id:
                uids.add(record["uid"])
                gids.add(record["gid"])
        return uids, gids

    def _allocate(self, users: dict, own_id: str | None = None) -> tuple[int, int]:
        _step("id_allocation")
        return allocate_workspace_ids(*self._occupied(users, own_id))

    @staticmethod
    def _check_home(record: dict) -> bool:
        if not os.path.lexists(record["home"]):
            return False
        if not stat.S_ISDIR(os.lstat(record["home"]).st_mode):
            raise WorkspaceProvisionError("identity_conflict")
        return True

    @staticmethod
    def _check_group(group, record: dict, server) -> None:
        if group is None or (group.gr_name, group.gr_gid) != (record["username"], record["gid"]):
            raise WorkspaceProvisionError("identity_conflict")
        if (
            group.gr_gid < 1000 or group.gr_gid == server.pw_gid
            or set(group.gr_mem) - {server.pw_name, record["username"]}
            or any(account.pw_gid == group.gr_gid and account.pw_name != record["username"] for account in pwd.getpwall())
            or any(other.gr_gid == group.gr_gid and other.gr_name != group.gr_name for other in grp.getgrall())
        ):
            raise WorkspaceProvisionError("identity_conflict")

    @staticmethod
    def _check_account(account, record: dict, identity: str) -> None:
        if (
            account.pw_name != record["username"]
            or account.pw_uid < 1000 or account.pw_uid in (os.getuid(), os.geteuid())
            or (account.pw_uid, account.pw_gid, account.pw_dir, account.pw_gecos)
            != (record["uid"], record["gid"], record["home"], "ot-" + hashlib.sha256(identity.encode("utf-8")).hexdigest())
            or any(other.pw_uid == account.pw_uid and other.pw_name != account.pw_name for other in pwd.getpwall())
        ):
            raise WorkspaceProvisionError("identity_conflict")

    def _prepare(self, identity: str, record: dict, registry: dict, server) -> WorkspaceAccount:
        _step("ownership_check")
        name, home = record["username"], record["home"]
        home_exists = self._check_home(record)
        account, group = self._account(name), self._group(name)
        recovered_home = False
        if account is not None:
            self._check_account(account, record, identity)
            self._check_group(group, record, server)
            if not home_exists:
                if record["state"] == "ready":
                    raise WorkspaceProvisionError("identity_conflict")
                # useradd may have committed passwd before home creation failed.
                # Only an incomplete, verified binding may finish that directory.
                _run_privileged([
                    "install", "-d", "-m", "0700", "-o", str(account.pw_uid),
                    "-g", str(account.pw_gid), home,
                ])
        elif record["state"] == "ready":
            if not home_exists:
                raise WorkspaceProvisionError("identity_conflict")
            if group is not None:
                self._check_group(group, record, server)
            uids, gids = self._occupied(registry["users"], identity)
            if group is not None and WORKSPACE_ID_MIN <= group.gr_gid <= WORKSPACE_ID_MAX:
                # Keep a proven, in-range orphan group. Remove it only from this
                # allocation snapshot so a full GID pool still permits recovery.
                gids.discard(group.gr_gid)
                uid, _ = allocate_workspace_ids(uids, gids)
                gid = group.gr_gid
            else:
                uid, gid = allocate_workspace_ids(uids, gids)
            if group is not None and not WORKSPACE_ID_MIN <= group.gr_gid <= WORKSPACE_ID_MAX:
                _run_privileged(["groupdel", name])
                group = None
            record.update(uid=uid, gid=gid, state="planned")
            self._save_registry(registry)
            recovered_home = True

        if account is None:
            uids, gids = self._occupied(registry["users"], identity)
            if group is None:
                if record["state"] == "group_ready":
                    raise WorkspaceProvisionError("identity_conflict")
                # No resource exists yet: a lost reservation may be reallocated.
                if (
                    record["uid"] in uids or record["gid"] in gids
                    or not WORKSPACE_ID_MIN <= record["uid"] <= WORKSPACE_ID_MAX
                    or not WORKSPACE_ID_MIN <= record["gid"] <= WORKSPACE_ID_MAX
                ):
                    uid, gid = allocate_workspace_ids(uids, gids)
                    record.update(uid=uid, gid=gid)
                    self._save_registry(registry)
                _run_privileged(["groupadd", "-g", str(record["gid"]), name])
                group = self._group(name)
            self._check_group(group, record, server)
            if record["state"] != "group_ready":
                record["state"] = "group_ready"
                self._save_registry(registry)
            if record["uid"] in uids or not WORKSPACE_ID_MIN <= record["uid"] <= WORKSPACE_ID_MAX:
                # Its private group already exists and cannot be renumbered.
                uid, _ = allocate_workspace_ids(uids, set())
                record["uid"] = uid
                self._save_registry(registry)
            _run_privileged([
                "useradd", "-m", "-u", str(record["uid"]), "-g", str(record["gid"]),
                "-d", home, "-s", "/bin/bash", "-c",
                "ot-" + hashlib.sha256(identity.encode("utf-8")).hexdigest(), name,
            ])
            account = self._account(name)
            if account is None:
                raise WorkspaceProvisionError("workspace_unavailable")
            self._check_account(account, record, identity)
            self._check_home(record)
            # A retained home needs ownership restored after passwd/group loss.
            # The durable full-ID binding, not filesystem ownership, authorizes it.
            recovered_home = home_exists

        if recovered_home or record["state"] != "ready":
            # -P/-h avoid following links within this proven owner's directory.
            # Do not recurse during ordinary reuse or alter user file modes.
            _run_privileged(["chown", "-h", "-R", "-P", f"{name}:{name}", home])
        else:
            info = os.stat(home)
            if (info.st_uid, info.st_gid) != (account.pw_uid, account.pw_gid):
                raise WorkspaceProvisionError("identity_conflict")
        _run_privileged(["chmod", "2770", home])
        self._prepare_access(account, server)
        if record["state"] != "ready":
            record["state"] = "ready"
            self._save_registry(registry)
        return WorkspaceAccount(identity, name, home, account.pw_uid, account.pw_gid)

    @staticmethod
    def _prepare_access(account, server) -> None:
        _step("service_access")
        if os.geteuid() != 0:
            group = grp.getgrnam(account.pw_name)
            if server.pw_name not in group.gr_mem:
                _run_privileged(["usermod", "-aG", account.pw_name, server.pw_name])
            groups = set(os.getgrouplist(server.pw_name, server.pw_gid)) | set(os.getgroups())
            try:
                os.setgroups(sorted(groups))
            except PermissionError:
                if not os.access(account.pw_dir, os.R_OK | os.W_OK | os.X_OK):
                    raise
        if not os.access(account.pw_dir, os.R_OK | os.W_OK | os.X_OK):
            raise WorkspaceProvisionError("workspace_unavailable")
        docker_socket = "/var/run/docker.sock"
        if os.path.exists(docker_socket):
            try:
                docker_group = grp.getgrgid(os.stat(docker_socket).st_gid)
                if account.pw_name not in docker_group.gr_mem and account.pw_gid != docker_group.gr_gid:
                    _run_privileged(["usermod", "-aG", docker_group.gr_name, account.pw_name])
            except (KeyError, OSError, subprocess.SubprocessError):
                log.warning("Optional Docker socket group membership could not be prepared")


def _run_privileged(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a command with appropriate privilege escalation.

    When the process is already running as root (UID 0), the command is
    executed directly.  Otherwise ``sudo`` is prepended.
    """
    verb = os.path.basename(cmd[0]) if cmd else "unknown"
    if verb not in {"install", "groupadd", "groupdel", "useradd", "usermod", "chown", "chmod"}:
        verb = "external"
    _step("command_" + verb)
    if os.getuid() == 0:
        return subprocess.run(cmd, check=True, capture_output=True, timeout=_COMMAND_TIMEOUT)
    return subprocess.run(["sudo", *cmd], check=True, capture_output=True, timeout=_COMMAND_TIMEOUT)


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

    Return the existing ``(username, home_dir)`` API after rechecking the durable
    binding and current process access. No memory cache bypasses those checks.
    """
    from open_terminal.env import USER_PREFIX

    account = WorkspaceProvisioner(username_prefix=USER_PREFIX).resolve(user_id)
    return account.username, account.home
