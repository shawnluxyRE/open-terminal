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
from dataclasses import dataclass

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
_cache_guard = threading.Lock()
_ready_cache: dict[tuple, "_ReadyEntry"] = {}
_descriptor_guard = threading.Lock()
_access_guard = threading.Lock()
_lock_descriptors: set[int] = set()
_process_pid = os.getpid()
_operation: ContextVar[tuple[str, str] | None] = ContextVar("workspace_operation", default=None)


@dataclass(frozen=True)
class _ReadyEntry:
    account: WorkspaceAccount
    record: dict
    registry_version: tuple
    observation: tuple


class _LockBudget:
    """One waiting budget across all user and registry lock acquisitions."""

    def __init__(self):
        self.remaining = _PREPARATION_TIMEOUT

    @contextmanager
    def waiting(self):
        if self.remaining <= 0:
            raise WorkspaceProvisionError("workspace_busy")
        start = time.monotonic()
        try:
            yield
        finally:
            self.remaining -= time.monotonic() - start


def _reset_after_fork() -> None:
    global _thread_locks, _thread_locks_guard, _cache_guard, _ready_cache
    global _descriptor_guard, _access_guard, _lock_descriptors, _process_pid
    # Close only this child's inherited references; never LOCK_UN a descriptor
    # shared with the parent, which would release the parent's real file lock.
    for descriptor in _lock_descriptors:
        try:
            os.close(descriptor)
        except OSError:
            pass
    _thread_locks = {}
    _thread_locks_guard = threading.Lock()
    _cache_guard = threading.Lock()
    _ready_cache = {}
    _descriptor_guard = threading.Lock()
    _access_guard = threading.Lock()
    _lock_descriptors = set()
    _process_pid = os.getpid()


os.register_at_fork(
    before=lambda: _descriptor_guard.acquire(),
    after_in_parent=lambda: _descriptor_guard.release(),
    after_in_child=_reset_after_fork,
)


def _file_version(path: str) -> tuple | None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


_NSS_FILES = ("/etc/passwd", "/etc/group", "/etc/nsswitch.conf")
_EXTRA_NSS_SOURCES = (
    "/etc/userdb", "/run/userdb", "/run/host/userdb", "/usr/lib/userdb",
    "/run/systemd/userdb", "/run/systemd/dynamic-uid", "/run/dbus/system_bus_socket",
)


def _nss_observation() -> tuple:
    return tuple(_file_version(path) for path in (*_NSS_FILES, *_EXTRA_NSS_SOURCES))


def _local_nss_is_observable(observation: tuple) -> bool:
    # The standard container uses files systemd without userdb providers or
    # dynamic-user facilities. Their appearance invalidates every local proof.
    if any(version is not None for version in observation[len(_NSS_FILES):]):
        return False
    if any(version is None or not stat.S_ISREG(version[2]) for version in observation[:len(_NSS_FILES)]):
        return False
    with open("/etc/nsswitch.conf", encoding="utf-8") as source:
        entries = {}
        for line in source:
            key, separator, values = line.partition("#")[0].partition(":")
            if separator and key.strip() in ("passwd", "group"):
                entries[key.strip()] = values.split()
    return all(entries.get(key) in (["files"], ["files", "systemd"]) for key in ("passwd", "group"))


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

    Cache, lock and recovery contracts: docs/design/workspace-identity.md.
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

        Validate before I/O. The fast path reuses only durable, unchanged
        bindings after lightweight ownership/access checks. Slow preparation uses
        a per-user lock; global locks cover short registry transactions only.
        Each commit merges the user's transition into the latest valid registry.
        Persist reservations before OS work; publish cache entries only after
        durable ready and usable access. Reuse names on retry, never on conflict.
        InvalidUserIdentity or WorkspaceProvisionError preserves user data with
        no shared-account fallback. Detailed limits and invalidation: design doc.
        """
        validate_user_id(user_id)
        self._validate_configuration()
        # Correlation is deliberately unrelated to the upstream identity or its
        # directory. No account arguments, command stderr or credentials are logged.
        operation_id = hashlib.sha256(
            f"{os.getpid()}:{threading.get_ident()}:{time.monotonic_ns()}".encode()
        ).hexdigest()[:16]
        token = _operation.set((operation_id, "service_account"))
        cache_key = self._cache_key(user_id)
        try:
            server = pwd.getpwuid(os.getuid())
            hit = self._cached_account(cache_key, server)
            if hit is not None:
                return hit
            budget = _LockBudget()
            self._ensure_metadata(server)
            lock_name = hashlib.sha256(user_id.encode("utf-8")).hexdigest() + ".lock"
            with self._locked(server, budget, os.path.join("user-locks", lock_name)):
                snapshot = self._nss_snapshot()
                with self._locked(server, budget):
                    registry, _ = self._load_registry(server)
                    record = registry["users"].get(user_id)
                    if record is None:
                        name = self._choose_name(user_id, registry["users"], snapshot)
                        uid, gid = self._allocate(registry["users"], snapshot)
                        record = {
                            "username": name, "prefix": self.username_prefix,
                            "home": os.path.join(self.home_root, name),
                            "uid": uid, "gid": gid, "state": "planned",
                        }
                        registry["users"][user_id] = record
                        self._save_registry(registry)
                    record = dict(record)
                account, record = self._prepare(user_id, record, server, budget)
                entry = self._confirm_ready(account, record, server, budget)
            # Cache mutexes never enclose I/O or nest inside either disk lock.
            if entry is not None:
                with _cache_guard:
                    _ready_cache[cache_key] = entry
            return account
        except WorkspaceProvisionError as exc:
            with _cache_guard:
                _ready_cache.pop(cache_key, None)
            _, step = _operation.get()
            log.warning("Workspace preparation rejected: operation=%s step=%s code=%s", operation_id, step, exc.code)
            raise
        except Exception as exc:
            with _cache_guard:
                _ready_cache.pop(cache_key, None)
            # Privileged stderr and persistent user data must not enter a public
            # diagnostic (or an unrestricted traceback in the service log).
            _, step = _operation.get()
            log.warning("Workspace preparation failed: operation=%s step=%s error=%s", operation_id, step, type(exc).__name__)
            raise WorkspaceProvisionError("workspace_unavailable") from None
        finally:
            _operation.reset(token)

    def _cache_key(self, identity: str) -> tuple:
        if _process_pid != os.getpid():
            _reset_after_fork()
        return (
            os.path.realpath(self.state_dir), self.home_root, self.username_prefix,
            identity, os.getuid(), os.geteuid(), os.getgid(), os.getegid(), os.getpid(),
        )

    @staticmethod
    def _node(info) -> tuple:
        # User writes change directory timestamps, not the ownership of the home.
        return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid

    @classmethod
    def _observe_docker_access(cls, account: WorkspaceAccount) -> tuple:
        """Observe optional membership without making it a workspace requirement."""
        path = "/var/run/docker.sock"
        try:
            target = os.stat(path)
        except FileNotFoundError:
            return None, True
        except OSError:
            return None, False
        try:
            # Observe the followed target as well as the link: a stable symlink
            # can point to a replaced socket or one whose owning GID changes.
            node = cls._node(os.lstat(path)), cls._node(target)
            group = grp.getgrgid(target.st_gid)
            complete = account.gid == group.gr_gid or account.username in group.gr_mem
            return (node, group.gr_name, group.gr_gid, complete), complete
        except (OSError, KeyError):
            return None, False

    def _observe_ready(self, account: WorkspaceAccount, server) -> tuple:
        home_root = os.lstat(self.home_root)
        if not stat.S_ISDIR(home_root.st_mode):
            raise WorkspaceProvisionError("workspace_config_invalid")
        metadata = []
        user_lock = hashlib.sha256(account.user_id.encode("utf-8")).hexdigest() + ".lock"
        for relative, directory in (
            ("", True), ("user-locks", True), ("provision.lock", False),
            (os.path.join("user-locks", user_lock), False), ("registry.json", False),
        ):
            info = os.lstat(os.path.join(self.state_dir, relative))
            self._verify_metadata(info, server.pw_uid, directory=directory)
            metadata.append(self._node(info))
        version = _file_version(os.path.join(self.state_dir, "registry.json"))
        sources = _nss_observation()
        user, group = pwd.getpwnam(account.username), grp.getgrnam(account.username)
        home = os.lstat(account.home)
        if (
            not stat.S_ISDIR(home.st_mode) or stat.S_IMODE(home.st_mode) != 0o2770
            or (home.st_uid, home.st_gid) != (account.uid, account.gid)
            or not os.access(account.home, os.R_OK | os.W_OK | os.X_OK)
        ):
            raise WorkspaceProvisionError("workspace_unavailable")
        return (
            version, tuple(metadata), sources, self._node(home), tuple(user),
            tuple(group[:3]) + (tuple(group.gr_mem),), tuple(server), self._node(home_root),
            self._observe_docker_access(account),
        )

    def _cached_account(self, key: tuple, server) -> WorkspaceAccount | None:
        with _cache_guard:
            entry = _ready_cache.get(key)
        if entry is None:
            return None
        try:
            before = self._observe_ready(entry.account, server)
            if before == entry.observation and before[0] == entry.registry_version:
                after = self._observe_ready(entry.account, server)
                if after == before:
                    return entry.account
        except (OSError, KeyError, WorkspaceProvisionError):
            pass
        with _cache_guard:
            if _ready_cache.get(key) is entry:
                _ready_cache.pop(key, None)
        return None

    def _confirm_ready(self, account, record, server, budget) -> _ReadyEntry | None:
        # The version is captured by a confirmed transaction BEFORE the complete
        # ownership check. Never attach a later stat to an earlier verification.
        for _ in range(32):
            with self._locked(server, budget):
                registry, version = self._load_registry(server)
                if registry["users"].get(account.user_id) != record or record["state"] != "ready":
                    raise WorkspaceProvisionError("identity_conflict")
            before = self._observe_ready(account, server)
            if before[0] != version:
                continue
            self._check_account(pwd.getpwnam(account.username), record, account.user_id)
            self._check_group(grp.getgrnam(account.username), record, server)
            after = self._observe_ready(account, server)
            if before[:-1] != after[:-1]:
                continue
            observable = _local_nss_is_observable(after[2])
            final = self._observe_ready(account, server)
            if final[:-1] != after[:-1]:
                continue
            # Optional failure or a changing socket still permits the verified
            # workspace. Withhold caching so the next access retries maintenance;
            # do not turn optional observation churn into a failed core resolve.
            optional_ready = before[-1] == after[-1] == final[-1] and final[-1][1]
            return _ReadyEntry(account, dict(record), version, final) if observable and optional_ready else None
        raise WorkspaceProvisionError("workspace_unavailable")

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

    def _ensure_metadata(self, server) -> None:
        _step("metadata_directory")
        if not stat.S_ISDIR(os.lstat(self.home_root).st_mode):
            raise WorkspaceProvisionError("workspace_config_invalid")
        for directory in (self.state_dir, os.path.join(self.state_dir, "user-locks")):
            if not os.path.lexists(directory):
                _run_privileged([
                    "install", "-d", "-m", "0700", "-o", str(server.pw_uid),
                    "-g", str(server.pw_gid), directory,
                ])
            self._verify_metadata(os.lstat(directory), server.pw_uid, directory=True)

    @contextmanager
    def _locked(self, server, budget, relative="provision.lock"):
        _step("thread_lock")
        lock_path = os.path.join(self.state_dir, relative)
        key = os.path.realpath(lock_path)
        with _thread_locks_guard:
            lock = _thread_locks.setdefault(key, threading.Lock())
        with budget.waiting():
            if not lock.acquire(timeout=budget.remaining):
                raise WorkspaceProvisionError("workspace_busy")
        descriptor = None
        try:
            _step("file_lock")
            if os.path.lexists(lock_path):
                self._verify_metadata(os.lstat(lock_path), server.pw_uid, directory=False)
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
            with _descriptor_guard:
                try:
                    descriptor = os.open(lock_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
                    os.fchmod(descriptor, 0o600)
                except FileExistsError:
                    descriptor = os.open(lock_path, flags)
                _lock_descriptors.add(descriptor)
            self._verify_metadata(os.fstat(descriptor), server.pw_uid, directory=False)
            with budget.waiting():
                deadline = time.monotonic() + budget.remaining
                while True:
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
                    with _descriptor_guard:
                        _lock_descriptors.discard(descriptor)
                        os.close(descriptor)
            finally:
                lock.release()

    def _load_registry(self, server) -> tuple[dict, tuple | None]:
        _step("registry_read")
        path = os.path.join(self.state_dir, "registry.json")
        if not os.path.lexists(path):
            return {"version": 1, "users": {}}, None
        self._verify_metadata(os.lstat(path), server.pw_uid, directory=False)
        version = _file_version(path)

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
            if _file_version(path) != version:
                raise WorkspaceProvisionError("workspace_unavailable")
            return registry, version
        except (ValueError, TypeError, UnicodeError, InvalidUserIdentity):
            raise WorkspaceProvisionError("workspace_state_invalid") from None

    def _save_registry(self, registry: dict) -> tuple:
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
            version = _file_version(os.path.join(self.state_dir, "registry.json"))
            self._sync_registry_directory()
            if _file_version(os.path.join(self.state_dir, "registry.json")) != version:
                raise WorkspaceProvisionError("workspace_unavailable")
            return version
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

    def _choose_name(self, user_id: str, users: dict, snapshot) -> str:
        _step("name_allocation")
        reserved = {record["username"] for record in users.values()}
        accounts = {record.pw_name for record in snapshot[0]}
        groups = {record.gr_name for record in snapshot[1]}
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
    def _nss_snapshot() -> tuple:
        return list(pwd.getpwall()), list(grp.getgrall())

    @staticmethod
    def _occupied(users: dict, snapshot, own_id: str | None = None) -> tuple[set, set]:
        uids = {account.pw_uid for account in snapshot[0]}
        gids = {group.gr_gid for group in snapshot[1]}
        for identity, record in users.items():
            if identity != own_id:
                uids.add(record["uid"])
                gids.add(record["gid"])
        return uids, gids

    def _allocate(self, users: dict, snapshot, own_id: str | None = None) -> tuple[int, int]:
        _step("id_allocation")
        return allocate_workspace_ids(*self._occupied(users, snapshot, own_id))

    def _commit(self, identity, expected, update, server, budget) -> dict:
        """Merge one expected transition; callbacks only inspect supplied data."""
        with self._locked(server, budget):
            latest, _ = self._load_registry(server)
            if latest["users"].get(identity) != expected:
                raise WorkspaceProvisionError("identity_conflict")
            replacement = update(latest["users"]) if callable(update) else update
            latest["users"][identity] = replacement
            self._save_registry(latest)
            return dict(replacement)

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

    def _prepare(self, identity: str, record: dict, server, budget) -> tuple[WorkspaceAccount, dict]:
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
            snapshot = self._nss_snapshot()

            def recovery_record(users):
                uids, gids = self._occupied(users, snapshot, identity)
                if group is not None and WORKSPACE_ID_MIN <= group.gr_gid <= WORKSPACE_ID_MAX:
                    gids.discard(group.gr_gid)
                    uid, _ = allocate_workspace_ids(uids, gids)
                    gid = group.gr_gid
                else:
                    uid, gid = allocate_workspace_ids(uids, gids)
                return {**record, "uid": uid, "gid": gid, "state": "planned"}

            if group is not None and not WORKSPACE_ID_MIN <= group.gr_gid <= WORKSPACE_ID_MAX:
                # Check both pools before touching a proven orphan. Its removal
                # stays outside the global transaction; a crash retains the old
                # ready binding and can repeat recovery with the group absent.
                with self._locked(server, budget):
                    latest, _ = self._load_registry(server)
                    if latest["users"].get(identity) != record:
                        raise WorkspaceProvisionError("identity_conflict")
                    recovery_record(latest["users"])
                _run_privileged(["groupdel", name])
                group = None
                snapshot = self._nss_snapshot()
            record = self._commit(identity, record, recovery_record, server, budget)
            recovered_home = True

        if account is None:
            snapshot = self._nss_snapshot()
            uids, gids = self._occupied({}, snapshot)
            if group is None:
                if record["state"] == "group_ready":
                    raise WorkspaceProvisionError("identity_conflict")
                # No resource exists yet: a lost reservation may be reallocated.
                if (
                    record["uid"] in uids or record["gid"] in gids
                    or not WORKSPACE_ID_MIN <= record["uid"] <= WORKSPACE_ID_MAX
                    or not WORKSPACE_ID_MIN <= record["gid"] <= WORKSPACE_ID_MAX
                ):
                    def reallocate(users):
                        uid, gid = self._allocate(users, snapshot, identity)
                        return {**record, "uid": uid, "gid": gid}

                    record = self._commit(identity, record, reallocate, server, budget)
                _run_privileged(["groupadd", "-g", str(record["gid"]), name])
                group = self._group(name)
            self._check_group(group, record, server)
            if record["state"] != "group_ready":
                record = self._commit(identity, record, {**record, "state": "group_ready"}, server, budget)
            if record["uid"] in uids or not WORKSPACE_ID_MIN <= record["uid"] <= WORKSPACE_ID_MAX:
                # Its private group already exists and cannot be renumbered.
                def reallocate_uid(users):
                    occupied_uids, _ = self._occupied(users, snapshot, identity)
                    uid, _ = allocate_workspace_ids(occupied_uids, set())
                    return {**record, "uid": uid}

                record = self._commit(identity, record, reallocate_uid, server, budget)
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
        if stat.S_IMODE(os.stat(home).st_mode) != 0o2770:
            _run_privileged(["chmod", "2770", home])
        self._prepare_access(account, server, budget)
        if record["state"] != "ready":
            record = self._commit(identity, record, {**record, "state": "ready"}, server, budget)
        return WorkspaceAccount(identity, name, home, account.pw_uid, account.pw_gid), record

    @staticmethod
    def _prepare_access(account, server, budget) -> None:
        _step("service_access")
        if os.geteuid() != 0:
            group = grp.getgrnam(account.pw_name)
            if server.pw_name not in group.gr_mem:
                _run_privileged(["usermod", "-aG", account.pw_name, server.pw_name])
            with budget.waiting():
                if not _access_guard.acquire(timeout=budget.remaining):
                    raise WorkspaceProvisionError("workspace_busy")
            try:
                # setgroups changes all service threads. Read the union inside
                # this short lock so concurrent preparations cannot overwrite a
                # newer process group list with an earlier user's stale snapshot.
                if account.pw_gid not in os.getgroups():
                    groups = set(os.getgrouplist(server.pw_name, server.pw_gid)) | set(os.getgroups())
                    try:
                        os.setgroups(sorted(groups))
                    except PermissionError:
                        if not os.access(account.pw_dir, os.R_OK | os.W_OK | os.X_OK):
                            raise
                if not os.access(account.pw_dir, os.R_OK | os.W_OK | os.X_OK):
                    raise WorkspaceProvisionError("workspace_unavailable")
            finally:
                _access_guard.release()
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
