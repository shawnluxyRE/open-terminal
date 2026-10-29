"""Fault and acceptance evidence against the documented journal and real Linux resources.

ENOSPC and command timeouts are external-dependency simulations, not claims of
a physically full disk. Process-interruption cases really terminate a worker.
Every mutating case uses the disposable Docker guard from the R2 fixture.
"""

import errno
import fcntl
import grp
import json
import multiprocessing
import os
from pathlib import Path
import pwd
import stat
import subprocess
import time
import uuid

import pytest

from open_terminal.utils import user_isolation
from open_terminal.utils.identity import WorkspaceProvisionError
from open_terminal.utils.user_isolation import WorkspaceProvisioner, derive_workspace_username
from test_workspace_provisioning import assert_ready_account, run_account_tool, workspace
from test_workspace_recovery import binding, free_gid, free_uid, write_registry


def state_record(options, identity):
    return json.loads((Path(options["state_dir"]) / "registry.json").read_text())["users"][identity]


def test_entropy_failure_preserves_unowned_home_and_creates_no_binding(workspace, monkeypatch):
    provisioner, options, identity = workspace
    preferred = Path(options["home_root"]) / derive_workspace_username(identity, options["username_prefix"])
    preferred.mkdir()
    marker = preferred / "unowned.txt"
    marker.write_text("keep unowned data")
    registry = write_registry(options, {})
    before = list(pwd.getpwall()), list(grp.getgrall())

    def unavailable(_size):
        raise OSError("injected entropy source failure")

    monkeypatch.setattr(user_isolation.secrets, "token_hex", unavailable)
    with pytest.raises(WorkspaceProvisionError) as failure:
        provisioner.resolve(identity)
    assert failure.value.code == "workspace_unavailable"
    assert json.loads(registry.read_text())["users"] == {}
    assert (list(pwd.getpwall()), list(grp.getgrall())) == before
    assert marker.read_text() == "keep unowned data"


@pytest.mark.parametrize("change", ["prefix", "home_root"])
def test_configuration_drift_does_not_rebind_existing_identity(workspace, change):
    provisioner, options, identity = workspace
    original = provisioner.resolve(identity)
    sentinel = Path(original.home) / "keep.txt"
    sentinel.write_text("same binding")
    registry = Path(options["state_dir"]) / "registry.json"
    before = registry.read_bytes(), list(pwd.getpwall()), list(grp.getgrall())
    changed = dict(options)
    if change == "prefix":
        changed["username_prefix"] = "other_"
    else:
        changed["home_root"] = str(Path(options["home_root"]).parent / "other-homes")
        Path(changed["home_root"]).mkdir()
    with pytest.raises(WorkspaceProvisionError) as failure:
        WorkspaceProvisioner(**changed).resolve(identity)
    assert failure.value.code == "workspace_config_invalid"
    assert (registry.read_bytes(), list(pwd.getpwall()), list(grp.getgrall())) == before
    assert sentinel.read_text() == "same binding"


@pytest.mark.parametrize("duplicate", ["json-key", "username", "uid", "gid"])
def test_duplicate_journal_keys_or_resources_fail_before_system_mutation(workspace, duplicate):
    provisioner, options, identity = workspace
    first = binding(options, identity, state="planned")
    second = binding(options, identity + "-other", state="planned", uid=first["uid"] + 1, gid=first["gid"] + 1)
    if duplicate == "username":
        second["username"], second["home"] = first["username"], first["home"]
    elif duplicate in {"uid", "gid"}:
        second[duplicate] = first[duplicate]
    registry = write_registry(options, {identity: first, identity + "-other": second})
    if duplicate == "json-key":
        registry.write_text('{"version":1,"version":1,"users":{}}')
    before = registry.read_bytes(), list(pwd.getpwall()), list(grp.getgrall())
    with pytest.raises(WorkspaceProvisionError) as failure:
        provisioner.resolve(identity)
    assert failure.value.code == ("workspace_state_invalid" if duplicate == "json-key" else "identity_conflict")
    assert (registry.read_bytes(), list(pwd.getpwall()), list(grp.getgrall())) == before


@pytest.mark.parametrize("mismatch", ["marker", "uid", "gid"])
def test_bound_account_mismatch_is_not_repaired_or_renamed(workspace, mismatch):
    provisioner, options, identity = workspace
    account = provisioner.resolve(identity)
    sentinel = Path(account.home) / "keep.txt"
    sentinel.write_text("keep bound data")
    if mismatch == "marker":
        run_account_tool("usermod", "-c", "not-the-bound-identity", account.username)
    else:
        record = state_record(options, identity)
        record[mismatch] = free_uid() if mismatch == "uid" else free_gid()
        write_registry(options, {identity: record})
    registry = Path(options["state_dir"]) / "registry.json"
    before = registry.read_bytes(), list(pwd.getpwall()), list(grp.getgrall())
    with pytest.raises(WorkspaceProvisionError) as failure:
        provisioner.resolve(identity)
    assert failure.value.code == "identity_conflict"
    assert (registry.read_bytes(), list(pwd.getpwall()), list(grp.getgrall())) == before
    assert sentinel.read_text() == "keep bound data"


@pytest.mark.parametrize("state", ["planned", "group_ready"])
def test_reserved_numbers_occupied_before_retry_are_reselected_without_adoption(workspace, state):
    provisioner, options, identity = workspace
    record = binding(options, identity, state=state)
    write_registry(options, {identity: record})
    foreign = "fault_" + uuid.uuid4().hex[:12]
    if state == "planned":
        run_account_tool("groupadd", "-g", str(record["gid"]), foreign)
        foreign_gid = record["gid"]
    else:
        run_account_tool("groupadd", "-g", str(record["gid"]), record["username"])
        foreign_gid = 0
    run_account_tool("useradd", "-M", "-N", "-u", str(record["uid"]), "-g", str(foreign_gid), foreign)
    foreign_before = pwd.getpwnam(foreign)
    account = provisioner.resolve(identity)
    assert_ready_account(account, identity, options["home_root"])
    assert account.username == record["username"]
    assert account.uid != record["uid"]
    assert (account.gid != record["gid"]) if state == "planned" else (account.gid == record["gid"])
    assert pwd.getpwnam(foreign) == foreign_before


def test_privileged_command_timeout_is_diagnostic_and_retry_keeps_the_binding(workspace, monkeypatch):
    provisioner, options, identity = workspace
    real_run = user_isolation._run_privileged
    injected = False

    def timeout_once(command):
        nonlocal injected
        if Path(command[0]).name == "groupadd" and not injected:
            injected = True
            raise subprocess.TimeoutExpired(command, 30)
        return real_run(command)

    monkeypatch.setattr(user_isolation, "_run_privileged", timeout_once)
    with pytest.raises(WorkspaceProvisionError) as failure:
        provisioner.resolve(identity)
    assert injected and failure.value.code == "workspace_unavailable"
    pending = state_record(options, identity)
    assert pending["state"] == "planned"
    recovered = WorkspaceProvisioner(**options).resolve(identity)
    assert_ready_account(recovered, identity, options["home_root"])
    assert recovered.username == pending["username"]


def test_privileged_subprocess_has_a_finite_timeout(workspace, monkeypatch):
    # Verify the real external runner supplies a deadline without waiting 30s.
    calls = []

    def external_run(command, **kwargs):
        calls.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

    monkeypatch.setattr(user_isolation.subprocess, "run", external_run)
    with pytest.raises(subprocess.TimeoutExpired):
        user_isolation._run_privileged(["true"])
    assert len(calls) == 1 and isinstance(calls[0], (int, float)) and 0 < calls[0] < float("inf")


@pytest.mark.parametrize("boundary", ["replace-planned", "fsync-planned", "replace-ready"])
def test_journal_io_failure_never_returns_a_partial_workspace(workspace, monkeypatch, boundary):
    provisioner, options, identity = workspace
    registry = Path(options["state_dir"]) / "registry.json"
    before = list(pwd.getpwall()), list(grp.getgrall())
    real_replace, real_fsync = os.replace, os.fsync
    injected = False

    def fail_replace(source, destination, *args, **kwargs):
        nonlocal injected
        if Path(destination) == registry and not injected:
            document = json.loads(Path(source).read_text())
            record = document["users"].get(identity)
            target = "ready" if boundary == "replace-ready" else "planned"
            if record and record["state"] == target:
                injected = True
                raise OSError(errno.ENOSPC, "injected full metadata disk")
        return real_replace(source, destination, *args, **kwargs)

    def fail_fsync(fd):
        nonlocal injected
        if not injected:
            injected = True
            raise OSError(errno.ENOSPC, "injected fsync failure")
        return real_fsync(fd)

    with monkeypatch.context() as fault:
        if boundary == "fsync-planned":
            fault.setattr(os, "fsync", fail_fsync)
        else:
            fault.setattr(os, "replace", fail_replace)
        with pytest.raises(WorkspaceProvisionError) as failure:
            provisioner.resolve(identity)
        assert injected and failure.value.code == "workspace_unavailable"
    if boundary == "replace-ready":
        pending = state_record(options, identity)
        assert pending["state"] != "ready"
        marker = Path(pending["home"]) / "keep-after-io-failure.txt"
        marker.write_text("existing user data")
    else:
        assert (list(pwd.getpwall()), list(grp.getgrall())) == before
        marker = None
    recovered = WorkspaceProvisioner(**options).resolve(identity)
    assert_ready_account(recovered, identity, options["home_root"])
    if marker is not None:
        assert marker.read_text() == "existing user data"


def _exit_after_system_creation(options, identity, command_name):
    real_run = user_isolation._run_privileged

    def interrupt(command):
        result = real_run(command)
        if Path(command[0]).name == command_name:
            os._exit(87)
        return result

    user_isolation._run_privileged = interrupt
    WorkspaceProvisioner(**options).resolve(identity)
    os._exit(88)


@pytest.mark.parametrize("command_name", ["groupadd", "useradd"])
def test_worker_exit_after_os_creation_recovers_original_journal_binding(workspace, command_name):
    _, options, identity = workspace
    child = multiprocessing.get_context("fork").Process(
        target=_exit_after_system_creation, args=(options, identity, command_name),
    )
    child.start()
    child.join(20)
    if child.is_alive():
        child.kill()
        child.join()
        pytest.fail("Interrupted provisioning worker did not finish")
    assert child.exitcode == 87
    pending = state_record(options, identity)
    assert pending["state"] != "ready"
    marker = None
    if command_name == "useradd":
        marker = Path(pending["home"]) / "after-interruption.txt"
        marker.write_text("preserve after actual worker exit")
    recovered = WorkspaceProvisioner(**options).resolve(identity)
    assert_ready_account(recovered, identity, options["home_root"])
    assert (recovered.username, recovered.uid, recovered.gid) == (pending["username"], pending["uid"], pending["gid"])
    if marker is not None:
        assert marker.read_text() == "preserve after actual worker exit"


def test_lock_contention_times_out_without_mutation_then_recovers(workspace):
    provisioner, options, identity = workspace
    registry = write_registry(options, {})
    lock_path = Path(options["state_dir"]) / "provision.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    before = registry.read_bytes(), list(pwd.getpwall()), list(grp.getgrall())
    inode = os.fstat(fd).st_ino
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        started = time.monotonic()
        with pytest.raises(WorkspaceProvisionError) as failure:
            provisioner.resolve(identity)
        elapsed = time.monotonic() - started
        assert failure.value.code == "workspace_busy"
        assert 29 <= elapsed < 40, elapsed
        assert (registry.read_bytes(), list(pwd.getpwall()), list(grp.getgrall())) == before
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    recovered = provisioner.resolve(identity)
    assert_ready_account(recovered, identity, options["home_root"])
    assert lock_path.stat().st_ino == inode


def test_recovery_chown_does_not_follow_workspace_symlink_targets(workspace):
    provisioner, options, identity = workspace
    account = provisioner.resolve(identity)
    external = Path(options["home_root"]).parent / "outside.txt"
    external.write_text("not workspace data")
    before = external.stat()
    (Path(account.home) / "outside-link").symlink_to(external)
    run_account_tool("userdel", account.username)
    recovered = WorkspaceProvisioner(**options).resolve(identity)
    assert_ready_account(recovered, identity, options["home_root"])
    after = external.stat()
    assert (after.st_uid, after.st_gid, after.st_mode) == (before.st_uid, before.st_gid, before.st_mode)
    assert external.read_text() == "not workspace data"


def test_upper_bound_numbers_are_used_by_real_accounts_and_homes(workspace, monkeypatch):
    """Simulate lower NSS occupancy, then actually create UID/GID 19999."""
    provisioner, options, identity = workspace
    real_users, real_groups = pwd.getpwall, grp.getgrall
    assert not any(item.pw_uid == 19999 for item in real_users())
    assert not any(item.gr_gid == 19999 for item in real_groups())

    def occupied_users():
        current = list(real_users())
        present = {item.pw_uid for item in current}
        return current + [
            pwd.struct_passwd((f"boundary_uid_{number}", "x", number, 0, "", "/nonexistent", "/usr/sbin/nologin"))
            for number in range(10001, 19999) if number not in present
        ]

    def occupied_groups():
        current = list(real_groups())
        present = {item.gr_gid for item in current}
        return current + [
            grp.struct_group((f"boundary_gid_{number}", "x", number, []))
            for number in range(10001, 19999) if number not in present
        ]

    monkeypatch.setattr(pwd, "getpwall", occupied_users)
    monkeypatch.setattr(grp, "getgrall", occupied_groups)
    account = provisioner.resolve(identity)
    assert_ready_account(account, identity, options["home_root"])
    assert (account.uid, account.gid) == (19999, 19999)


def test_useradd_partial_failure_with_missing_home_can_resume_its_account(workspace, monkeypatch):
    """Model useradd updating NSS before a home-creation failure, using real NSS."""
    provisioner, options, identity = workspace
    real_run = user_isolation._run_privileged
    injected = False

    def fail_home_creation_once(command):
        nonlocal injected
        if Path(command[0]).name == "useradd" and not injected:
            injected = True
            # The external command really creates the requested passwd entry,
            # but -M models its filesystem step failing after that NSS update.
            partial_command = ["-M" if item == "-m" else item for item in command]
            real_run(partial_command)
            raise subprocess.CalledProcessError(12, command, stderr=b"injected home creation failure")
        return real_run(command)

    monkeypatch.setattr(user_isolation, "_run_privileged", fail_home_creation_once)
    with pytest.raises(WorkspaceProvisionError) as failure:
        provisioner.resolve(identity)
    assert injected and failure.value.code == "workspace_unavailable"
    pending = state_record(options, identity)
    assert pending["state"] != "ready"
    partial = pwd.getpwnam(pending["username"])
    assert not Path(partial.pw_dir).exists()
    recovered = WorkspaceProvisioner(**options).resolve(identity)
    assert_ready_account(recovered, identity, options["home_root"])
    assert (recovered.username, recovered.uid, recovered.gid, recovered.home) == (
        partial.pw_name, partial.pw_uid, partial.pw_gid, partial.pw_dir,
    )


def system_snapshot():
    return list(pwd.getpwall()), list(grp.getgrall())


def metadata_snapshot(path):
    info = path.lstat()
    return info.st_ino, info.st_uid, info.st_gid, info.st_mode


@pytest.mark.parametrize("target", ["directory", "registry", "lock"])
@pytest.mark.parametrize("damage", ["mode", "owner", "symlink"])
def test_untrusted_metadata_is_rejected_without_repair_or_rebinding(workspace, target, damage):
    """Design metadata contract: exact owner, type, 0700/0600 and no symlinks."""
    provisioner, options, identity = workspace
    account = provisioner.resolve(identity)
    sentinel = Path(account.home) / "keep.txt"
    sentinel.write_text("original workspace data", encoding="utf-8")
    state_dir = Path(options["state_dir"])
    registry = state_dir / "registry.json"
    path = {
        "directory": state_dir,
        "registry": registry,
        "lock": state_dir / "provision.lock",
    }[target]
    if damage == "mode":
        path.chmod(0o750 if target == "directory" else 0o640)
    elif damage == "owner":
        os.chown(path, account.uid, account.gid)
    else:
        original = path.with_name(path.name + ".original")
        path.rename(original)
        path.symlink_to(original, target_is_directory=target == "directory")
    before = system_snapshot(), registry.read_bytes(), metadata_snapshot(path)

    with pytest.raises(WorkspaceProvisionError) as failure:
        WorkspaceProvisioner(**options).resolve(identity)

    assert failure.value.code == "workspace_state_invalid"
    assert (system_snapshot(), registry.read_bytes(), metadata_snapshot(path)) == before
    assert sentinel.read_text(encoding="utf-8") == "original workspace data"


@pytest.mark.parametrize("use", ["primary", "supplementary", "gid-alias"])
def test_bound_private_group_cannot_be_shared_with_foreign_resources(workspace, use):
    """AC-002/033: a bound private group is not silently accepted after conflict."""
    provisioner, options, identity = workspace
    account = provisioner.resolve(identity)
    foreign = "r5_foreign_" + uuid.uuid4().hex[:12]
    if use == "gid-alias":
        run_account_tool("groupadd", "-o", "-g", str(account.gid), foreign)
    else:
        run_account_tool(
            "useradd", "-M", "-N", "-u", str(free_uid()),
            "-g", str(account.gid if use == "primary" else 0), foreign,
        )
        if use == "supplementary":
            run_account_tool("usermod", "-aG", account.username, foreign)
    registry = Path(options["state_dir"]) / "registry.json"
    before = system_snapshot(), registry.read_bytes(), metadata_snapshot(Path(account.home))

    with pytest.raises(WorkspaceProvisionError) as failure:
        provisioner.resolve(identity)

    assert failure.value.code == "identity_conflict"
    assert (system_snapshot(), registry.read_bytes(), metadata_snapshot(Path(account.home))) == before


def test_ready_home_owner_mismatch_is_not_recursively_repaired(workspace):
    """A ready binding must reject conflicting ownership, not adopt new data."""
    provisioner, options, identity = workspace
    account = provisioner.resolve(identity)
    home = Path(account.home)
    sentinel = home / "foreign-owned-data.txt"
    sentinel.write_text("keep the observed owner and data", encoding="utf-8")
    os.chown(home, 0, 0)
    registry = Path(options["state_dir"]) / "registry.json"
    before = system_snapshot(), registry.read_bytes(), metadata_snapshot(home), metadata_snapshot(sentinel)

    with pytest.raises(WorkspaceProvisionError) as failure:
        provisioner.resolve(identity)

    assert failure.value.code == "identity_conflict"
    assert (system_snapshot(), registry.read_bytes(), metadata_snapshot(home), metadata_snapshot(sentinel)) == before
    assert sentinel.read_text(encoding="utf-8") == "keep the observed owner and data"


def test_retained_in_range_orphan_group_can_recover_when_gid_pool_is_full(workspace, monkeypatch):
    """AC-033/034: a proven private group needs no new GID during recovery."""
    provisioner, options, identity = workspace
    original = provisioner.resolve(identity)
    sentinel = Path(original.home) / "persistent.txt"
    sentinel.write_text("retained account data", encoding="utf-8")
    sentinel.chmod(0o640)
    run_account_tool("userdel", original.username)
    try:
        grp.getgrnam(original.username)
    except KeyError:
        # userdel may automatically remove an empty same-name private group.
        run_account_tool("groupadd", "-g", str(original.gid), original.username)
    real_getgrall = grp.getgrall

    def full_gid_snapshot():
        current = list(real_getgrall())
        present = {item.gr_gid for item in current}
        return current + [
            grp.struct_group((f"r5_occupied_gid_{number}", "x", number, []))
            for number in range(10001, 20000) if number not in present
        ]

    monkeypatch.setattr(grp, "getgrall", full_gid_snapshot)
    recovered = WorkspaceProvisioner(**options).resolve(identity)

    assert_ready_account(recovered, identity, options["home_root"])
    assert (recovered.username, recovered.home, recovered.gid) == (
        original.username, original.home, original.gid,
    )
    assert sentinel.read_text(encoding="utf-8") == "retained account data"
    assert stat.S_IMODE(sentinel.stat().st_mode) == 0o640
    assert (sentinel.stat().st_uid, sentinel.stat().st_gid) == (recovered.uid, recovered.gid)


@pytest.mark.parametrize("boundary", ["planned", "group_ready", "ready"])
def test_directory_fsync_failure_is_reported_and_visible_binding_is_retried(workspace, monkeypatch, boundary):
    """AC-023: replacement succeeded but directory durability was not confirmed."""
    provisioner, options, identity = workspace
    registry = Path(options["state_dir"]) / "registry.json"
    real_fsync = os.fsync
    injected = False

    def fail_directory_fsync_once(fd):
        nonlocal injected
        if not injected and stat.S_ISDIR(os.fstat(fd).st_mode) and registry.exists():
            saved = json.loads(registry.read_text(encoding="utf-8"))["users"][identity]
            if saved["state"] == boundary:
                injected = True
                raise OSError(errno.ENOSPC, "injected directory fsync failure")
        return real_fsync(fd)

    with monkeypatch.context() as fault:
        fault.setattr(os, "fsync", fail_directory_fsync_once)
        with pytest.raises(WorkspaceProvisionError) as failure:
            provisioner.resolve(identity)
        assert injected and failure.value.code == "workspace_unavailable"

    visible = json.loads(registry.read_text(encoding="utf-8"))["users"][identity]
    assert visible["state"] == boundary
    marker = None
    if boundary == "ready":
        marker = Path(visible["home"]) / "after-failed-durability.txt"
        marker.write_text("preserve while retrying", encoding="utf-8")
    recovered = WorkspaceProvisioner(**options).resolve(identity)

    assert_ready_account(recovered, identity, options["home_root"])
    assert (recovered.username, recovered.home, recovered.uid, recovered.gid) == (
        visible["username"], visible["home"], visible["uid"], visible["gid"],
    )
    if marker is not None:
        assert marker.read_text(encoding="utf-8") == "preserve while retrying"


@pytest.mark.parametrize("retry_fault_active", [True, False])
def test_visible_ready_after_failed_directory_fsync_requires_durable_retry(
    workspace, monkeypatch, retry_fault_active
):
    """AC-023: visible ready data cannot bypass an unconfirmed durable commit."""
    provisioner, options, identity = workspace
    state_dir = Path(options["state_dir"])
    registry = state_dir / "registry.json"
    real_fsync = os.fsync
    retrying = False
    fault_active = True
    directory_attempts = []
    completed_directory_syncs = []

    def sync_metadata_directory(fd):
        info = os.fstat(fd)
        metadata = state_dir.stat() if state_dir.exists() else None
        is_metadata_directory = (
            metadata is not None
            and stat.S_ISDIR(info.st_mode)
            and (info.st_dev, info.st_ino) == (metadata.st_dev, metadata.st_ino)
        )
        if is_metadata_directory and registry.exists():
            saved = json.loads(registry.read_text(encoding="utf-8"))["users"][identity]
            if retrying or saved["state"] == "ready":
                directory_attempts.append(saved["state"])
                if fault_active:
                    raise OSError(errno.ENOSPC, "injected metadata directory fsync failure")
                result = real_fsync(fd)
                completed_directory_syncs.append(saved["state"])
                return result
        return real_fsync(fd)

    with monkeypatch.context() as fault:
        fault.setattr(os, "fsync", sync_metadata_directory)
        with pytest.raises(WorkspaceProvisionError) as first_failure:
            provisioner.resolve(identity)
        assert first_failure.value.code == "workspace_unavailable"
        assert directory_attempts == ["ready"]
        visible = json.loads(registry.read_text(encoding="utf-8"))["users"][identity]
        assert visible["state"] == "ready"
        marker = Path(visible["home"]) / "preserve-across-durable-retry.txt"
        marker.write_text("same user and data after an uncertain commit", encoding="utf-8")
        os.chown(marker, visible["uid"], visible["gid"])
        marker.chmod(0o640)
        original_system = system_snapshot()
        original_marker = metadata_snapshot(marker)

        # A fresh resolver must handle a ready record read from the filesystem;
        # the failed commit cannot be tracked only in the original instance.
        retrying = True
        fault_active = retry_fault_active
        directory_attempts.clear()
        recovered_provisioner = WorkspaceProvisioner(**options)
        if retry_fault_active:
            with pytest.raises(WorkspaceProvisionError) as retry_failure:
                recovered_provisioner.resolve(identity)
            assert retry_failure.value.code == "workspace_unavailable"
            assert directory_attempts
            assert completed_directory_syncs == []
        else:
            recovered = recovered_provisioner.resolve(identity)
            assert completed_directory_syncs, "A retry must confirm the registry directory is durable before success"
            assert_ready_account(recovered, identity, options["home_root"])
            assert (recovered.username, recovered.home, recovered.uid, recovered.gid) == (
                visible["username"], visible["home"], visible["uid"], visible["gid"],
            )

        saved = json.loads(registry.read_text(encoding="utf-8"))["users"][identity]
        for field in ("username", "home", "uid", "gid", "prefix"):
            assert saved[field] == visible[field]
        assert system_snapshot() == original_system
        assert metadata_snapshot(marker) == original_marker
        assert marker.read_text(encoding="utf-8") == "same user and data after an uncertain commit"


def test_visible_planned_after_failed_directory_fsync_cannot_start_os_creation(
    workspace, monkeypatch
):
    """A reservation must be durable before any group or account is created."""
    provisioner, options, identity = workspace
    state_dir = Path(options["state_dir"])
    registry = state_dir / "registry.json"
    original_system = system_snapshot()
    real_fsync = os.fsync
    directory_attempts = []

    def unavailable_metadata_directory_sync(fd):
        info = os.fstat(fd)
        metadata = state_dir.stat() if state_dir.exists() else None
        if (
            metadata is not None
            and stat.S_ISDIR(info.st_mode)
            and (info.st_dev, info.st_ino) == (metadata.st_dev, metadata.st_ino)
        ):
            saved = json.loads(registry.read_text(encoding="utf-8"))["users"][identity]
            directory_attempts.append(saved["state"])
            raise OSError(errno.ENOSPC, "persistent metadata directory fsync failure")
        return real_fsync(fd)

    with monkeypatch.context() as fault:
        fault.setattr(os, "fsync", unavailable_metadata_directory_sync)
        with pytest.raises(WorkspaceProvisionError) as first_failure:
            provisioner.resolve(identity)
        assert first_failure.value.code == "workspace_unavailable"
        assert directory_attempts == ["planned"]
        visible = json.loads(registry.read_text(encoding="utf-8"))["users"][identity]
        assert visible["state"] == "planned"
        assert system_snapshot() == original_system
        assert not Path(visible["home"]).exists()

        with pytest.raises(WorkspaceProvisionError) as retry_failure:
            WorkspaceProvisioner(**options).resolve(identity)
        assert retry_failure.value.code == "workspace_unavailable"
        assert system_snapshot() == original_system, (
            "An unconfirmed planned reservation must not create OS users or groups on retry"
        )
        assert not Path(visible["home"]).exists()
        saved = json.loads(registry.read_text(encoding="utf-8"))["users"][identity]
        for field in ("username", "home", "uid", "gid", "prefix"):
            assert saved[field] == visible[field]


@pytest.mark.parametrize("boundary", ["planned", "group_ready", "ready"])
def test_directory_fsync_retry_confirms_all_states_before_progress(
    workspace, monkeypatch, boundary
):
    """A persistent commit failure must clear before creation or ready reuse."""
    provisioner, options, identity = workspace
    state_dir = Path(options["state_dir"])
    registry = state_dir / "registry.json"
    real_fsync = os.fsync
    phase = "initial"
    attempts = []
    successful_retry_observations = []

    def observe_directory_sync(fd):
        info = os.fstat(fd)
        metadata = state_dir.stat() if state_dir.exists() else None
        is_metadata_directory = (
            metadata is not None
            and stat.S_ISDIR(info.st_mode)
            and (info.st_dev, info.st_ino) == (metadata.st_dev, metadata.st_ino)
        )
        if not is_metadata_directory or not registry.exists():
            return real_fsync(fd)
        saved = json.loads(registry.read_text(encoding="utf-8"))["users"].get(identity)
        if saved is None or (phase == "initial" and saved["state"] != boundary):
            return real_fsync(fd)
        attempts.append((phase, saved["state"]))
        if phase != "recovered":
            raise OSError(errno.ENOSPC, "persistent metadata directory fsync failure")
        # Observe actual NSS/home state at the external durability boundary.
        # This does not replace or alter groupadd/useradd or their results.
        home = Path(saved["home"])
        observed = system_snapshot(), metadata_snapshot(home) if home.exists() else None
        result = real_fsync(fd)
        successful_retry_observations.append(observed)
        return result

    with monkeypatch.context() as fault:
        fault.setattr(os, "fsync", observe_directory_sync)
        with pytest.raises(WorkspaceProvisionError) as first_failure:
            provisioner.resolve(identity)
        assert first_failure.value.code == "workspace_unavailable"
        assert attempts == [("initial", boundary)]
        visible = state_record(options, identity)
        assert visible["state"] == boundary
        home = Path(visible["home"])
        if boundary == "ready":
            marker = home / "keep-through-directory-sync-retries.txt"
        else:
            marker = Path(options["home_root"]).parent / "keep-unrelated-data.txt"
        marker.write_text("preserve bytes and metadata through repeated failures", encoding="utf-8")
        if boundary == "ready":
            os.chown(marker, visible["uid"], visible["gid"])
        marker.chmod(0o640)
        original_marker = metadata_snapshot(marker)
        original_resources = system_snapshot(), metadata_snapshot(home) if home.exists() else None

        phase = "blocked"
        attempts.clear()
        with pytest.raises(WorkspaceProvisionError) as retry_failure:
            WorkspaceProvisioner(**options).resolve(identity)
        assert retry_failure.value.code == "workspace_unavailable"
        assert attempts, "A new resolver must retry the failed directory commit"
        assert successful_retry_observations == []
        assert (system_snapshot(), metadata_snapshot(home) if home.exists() else None) == original_resources
        saved = state_record(options, identity)
        for field in ("username", "home", "uid", "gid", "prefix"):
            assert saved[field] == visible[field]
        assert metadata_snapshot(marker) == original_marker

        phase = "recovered"
        attempts.clear()
        recovered = WorkspaceProvisioner(**options).resolve(identity)
        assert successful_retry_observations, "Success requires a completed metadata directory fsync"
        assert successful_retry_observations[0] == original_resources, (
            "A retry must confirm durability before creating the missing group, account or home"
        )
        assert_ready_account(recovered, identity, options["home_root"])
        assert (recovered.username, recovered.home, recovered.uid, recovered.gid) == (
            visible["username"], visible["home"], visible["uid"], visible["gid"],
        )
        assert state_record(options, identity)["state"] == "ready"
        assert metadata_snapshot(marker) == original_marker
        assert marker.read_text(encoding="utf-8") == "preserve bytes and metadata through repeated failures"
