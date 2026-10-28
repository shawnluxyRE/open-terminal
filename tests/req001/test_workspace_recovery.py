"""REQ-001 ownership journal, retry and process-concurrency contracts.

Fixtures write the documented registry format as external persistent input. Tests
never replace the resolver's business result. OS state changes are restricted by
the disposable-container fixture imported below.
"""

from concurrent.futures import ProcessPoolExecutor
import grp
import hashlib
import json
import multiprocessing
from pathlib import Path
import pwd
import subprocess
import time

import pytest

from open_terminal.utils.identity import WorkspaceProvisionError
from open_terminal.utils import user_isolation
from open_terminal.utils.user_isolation import WorkspaceProvisioner, derive_workspace_username
from test_workspace_provisioning import assert_ready_account, run_account_tool, workspace


def write_registry(options, users, *, version=1):
    directory = Path(options["state_dir"])
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    registry = directory / "registry.json"
    registry.write_text(json.dumps({"version": version, "users": users}), encoding="utf-8")
    registry.chmod(0o600)
    return registry


def free_uid(start=10001):
    occupied = {record.pw_uid for record in pwd.getpwall()}
    return next(number for number in range(start, start + 9999) if number not in occupied)


def free_gid(start=10001):
    occupied = {record.gr_gid for record in grp.getgrall()}
    return next(number for number in range(start, start + 9999) if number not in occupied)


def binding(options, identity, *, state="ready", uid=None, gid=None):
    name = derive_workspace_username(identity, prefix=options["username_prefix"])
    return {
        "username": name,
        "prefix": options["username_prefix"],
        "home": str(Path(options["home_root"]) / name),
        "uid": free_uid() if uid is None else uid,
        "gid": free_gid() if gid is None else gid,
        "state": state,
    }


@pytest.mark.parametrize("invalid_state", ["bad-json", "unknown-version"])
def test_invalid_persistent_state_fails_without_repairing_or_adopting_data(workspace, invalid_state):
    provisioner, options, identity = workspace
    registry = write_registry(options, {}, version=99 if invalid_state == "unknown-version" else 1)
    if invalid_state == "bad-json":
        registry.write_text('{"version":1,"users":', encoding="utf-8")
    content = registry.read_bytes()
    sentinel = Path(options["home_root"]) / "existing-data.txt"
    sentinel.write_text("existing data remains", encoding="utf-8")
    users_before, groups_before = list(pwd.getpwall()), list(grp.getgrall())
    with pytest.raises(WorkspaceProvisionError) as failure:
        provisioner.resolve(identity)
    assert failure.value.code == "workspace_state_invalid"
    assert registry.read_bytes() == content
    assert sentinel.read_text(encoding="utf-8") == "existing data remains"
    assert list(pwd.getpwall()) == users_before
    assert list(grp.getgrall()) == groups_before


def test_another_complete_id_reserving_candidate_name_is_preserved_and_skipped(workspace):
    """A registry reservation is occupied even before its OS resources exist."""
    provisioner, options, identity = workspace
    reserved = binding(options, identity, state="planned")
    registry = write_registry(options, {identity + "-other": reserved})
    users_before, groups_before = list(pwd.getpwall()), list(grp.getgrall())
    account = provisioner.resolve(identity)
    assert_ready_account(account, identity, options["home_root"])
    assert account.username != reserved["username"]
    assert account.uid != reserved["uid"]
    assert account.gid != reserved["gid"]
    persisted = json.loads(registry.read_text(encoding="utf-8"))
    assert persisted["users"][identity + "-other"] == reserved
    assert persisted["users"][identity]["username"] == account.username
    assert persisted["users"][identity]["prefix"] == options["username_prefix"]
    for record in users_before:
        assert pwd.getpwnam(record.pw_name) == record
    for record in groups_before:
        assert grp.getgrnam(record.gr_name) == record


def test_real_eight_digit_collision_persists_distinct_names_across_retry_and_recreation(workspace, monkeypatch):
    """A real hash collision gets a persistent random name, including after OS loss."""
    provisioner, options, _ = workspace
    first_id = "req001-collision-49585"
    second_id = "req001-collision-89826"
    first = provisioner.resolve(first_id)
    assert_ready_account(first, first_id, options["home_root"])
    assert first.username == "r2_19042bfe"
    sentinel = Path(first.home) / "first-owner.txt"
    sentinel.write_text("only the first complete identity owns this data", encoding="utf-8")
    content_before, file_before = sentinel.read_bytes(), sentinel.stat()
    home_before = Path(first.home).stat()
    users_before, groups_before = list(pwd.getpwall()), list(grp.getgrall())
    registry = Path(options["state_dir"]) / "registry.json"
    first_binding = json.loads(registry.read_text(encoding="utf-8"))["users"][first_id]
    second = provisioner.resolve(second_id)
    assert_ready_account(second, second_id, options["home_root"])
    for field in ("username", "home", "uid", "gid"):
        assert getattr(first, field) != getattr(second, field)
    assert json.loads(registry.read_text(encoding="utf-8"))["users"][first_id] == first_binding
    for record in users_before:
        assert pwd.getpwnam(record.pw_name) == record
    for record in groups_before:
        assert grp.getgrnam(record.gr_name) == record
    assert sentinel.read_bytes() == content_before
    file_after, home_after = sentinel.stat(), Path(first.home).stat()
    assert (file_after.st_uid, file_after.st_gid, file_after.st_mode) == (
        file_before.st_uid, file_before.st_gid, file_before.st_mode,
    )
    assert (home_after.st_uid, home_after.st_gid, home_after.st_mode) == (
        home_before.st_uid, home_before.st_gid, home_before.st_mode,
    )
    second_sentinel = Path(second.home) / "random-name-owner.txt"
    second_sentinel.write_text("random binding survives account recreation", encoding="utf-8")

    def unexpected_randomness(_byte_count):
        raise AssertionError("An existing full-ID binding must reuse its selected name")

    monkeypatch.setattr(user_isolation.secrets, "token_hex", unexpected_randomness)
    assert provisioner.resolve(second_id) == second
    assert WorkspaceProvisioner(**options).resolve(second_id) == second
    assert WorkspaceProvisioner(**options).resolve(first_id) == first
    run_account_tool("userdel", second.username)
    try:
        grp.getgrnam(second.username)
    except KeyError:
        pass
    else:
        run_account_tool("groupdel", second.username)
    recovered = WorkspaceProvisioner(**options).resolve(second_id)
    assert_ready_account(recovered, second_id, options["home_root"])
    assert recovered.username == second.username
    assert recovered.home == second.home
    assert second_sentinel.read_text(encoding="utf-8") == "random binding survives account recreation"
    assert (second_sentinel.stat().st_uid, second_sentinel.stat().st_gid) == (recovered.uid, recovered.gid)
    assert sentinel.read_bytes() == content_before
    assert json.loads(registry.read_text(encoding="utf-8"))["users"][first_id] == first_binding


@pytest.mark.parametrize(
    "prefix,candidates,expected",
    [
        ("", ("u11111111", "u22222222", "u33333333", "u44444444"), "u5abcde90"),
        ("rng_", ("rng_11111111", "rng_22222222", "rng_33333333", "rng_44444444"), "rng_5abcde90"),
    ],
)
def test_random_retry_checks_registry_account_group_home_and_repeated_candidates(workspace, monkeypatch, prefix, candidates, expected):
    """Only the entropy source is controlled; the real resolver checks all owners."""
    _, original_options, identity = workspace
    options = {**original_options, "username_prefix": prefix}
    provisioner = WorkspaceProvisioner(**options)
    home_root = Path(options["home_root"])
    preferred = home_root / derive_workspace_username(identity, prefix=prefix)
    preferred.mkdir()
    preferred_marker = preferred / "original.txt"
    preferred_marker.write_text("original unbound home", encoding="utf-8")
    account_home = home_root / candidates[1]
    run_account_tool("useradd", "-U", "-m", "-d", str(account_home), candidates[1])
    run_account_tool("groupadd", candidates[2])
    occupied_home = home_root / candidates[3]
    occupied_home.mkdir()
    sentinel = occupied_home / "preserve.txt"
    sentinel.write_text("occupied random candidate", encoding="utf-8")
    reserved = {
        "username": candidates[0], "prefix": prefix,
        "home": str(home_root / candidates[0]),
        "uid": free_uid(), "gid": free_gid(), "state": "planned",
    }
    registry = write_registry(options, {identity + "-reservation": reserved})
    users_before, groups_before = list(pwd.getpwall()), list(grp.getgrall())
    samples = iter(("11111111", "22222222", "33333333", "44444444", "11111111", "5abcde90"))
    calls = []

    def next_entropy(byte_count):
        assert byte_count == 4
        sample = next(samples)
        calls.append(sample)
        return sample

    monkeypatch.setattr(user_isolation.secrets, "token_hex", next_entropy)
    account = provisioner.resolve(identity)
    assert_ready_account(account, identity, options["home_root"])
    assert account.username == expected
    assert calls == ["11111111", "22222222", "33333333", "44444444", "11111111", "5abcde90"]
    persisted = json.loads(registry.read_text(encoding="utf-8"))
    assert persisted["users"][identity + "-reservation"] == reserved
    assert persisted["users"][identity]["username"] == expected
    assert persisted["users"][identity]["prefix"] == prefix
    for record in users_before:
        assert pwd.getpwnam(record.pw_name) == record
    for record in groups_before:
        assert grp.getgrnam(record.gr_name) == record
    assert preferred_marker.read_text(encoding="utf-8") == "original unbound home"
    assert sentinel.read_text(encoding="utf-8") == "occupied random candidate"


def test_32_occupied_random_candidates_fail_without_partial_binding(workspace, monkeypatch):
    _, original_options, identity = workspace
    options = {**original_options, "username_prefix": "ex_"}
    provisioner = WorkspaceProvisioner(**options)
    home_root = Path(options["home_root"])
    preferred = home_root / derive_workspace_username(identity, prefix="ex_")
    preferred.mkdir()
    occupied = home_root / "ex_aaaaaaaa"
    occupied.mkdir()
    sentinel = occupied / "preserve.txt"
    sentinel.write_text("do not adopt exhausted candidates", encoding="utf-8")
    registry = write_registry(options, {})
    users_before, groups_before = list(pwd.getpwall()), list(grp.getgrall())
    entries_before = sorted(home_root.iterdir())
    calls = []

    def occupied_entropy(byte_count):
        calls.append(byte_count)
        return "aaaaaaaa"

    monkeypatch.setattr(user_isolation.secrets, "token_hex", occupied_entropy)
    with pytest.raises(WorkspaceProvisionError) as failure:
        provisioner.resolve(identity)
    assert failure.value.code == "username_allocation_failed"
    assert calls == [4] * 32
    assert json.loads(registry.read_text(encoding="utf-8"))["users"] == {}
    assert list(pwd.getpwall()) == users_before
    assert list(grp.getgrall()) == groups_before
    assert sorted(home_root.iterdir()) == entries_before
    assert sentinel.read_text(encoding="utf-8") == "do not adopt exhausted candidates"


def test_random_name_permission_failure_retries_the_persisted_binding(workspace, monkeypatch):
    """A partial random-name binding is resumed, not replaced by another draw."""
    _, original_options, identity = workspace
    options = {**original_options, "username_prefix": "rr_"}
    provisioner = WorkspaceProvisioner(**options)
    preferred = Path(options["home_root"]) / derive_workspace_username(identity, prefix="rr_")
    preferred.mkdir()
    original_marker = preferred / "not-ours.txt"
    original_marker.write_text("belongs to the previous occupant", encoding="utf-8")
    selected_name = "rr_b1234567"
    selected_home = str(Path(options["home_root"]) / selected_name)
    entropy_calls = []

    def selected_entropy(byte_count):
        entropy_calls.append(byte_count)
        return "b1234567"

    real_run = user_isolation._run_privileged
    failed = False

    def fail_selected_chmod_once(command):
        nonlocal failed
        if (
            command and Path(command[0]).name == "chmod" and "2770" in command
            and command[-1] == selected_home and not failed
        ):
            failed = True
            raise subprocess.CalledProcessError(1, command, stderr=b"injected permission failure")
        return real_run(command)

    monkeypatch.setattr(user_isolation.secrets, "token_hex", selected_entropy)
    monkeypatch.setattr(user_isolation, "_run_privileged", fail_selected_chmod_once)
    with pytest.raises(WorkspaceProvisionError) as failure:
        provisioner.resolve(identity)
    assert failure.value.code == "workspace_unavailable"
    assert failed and entropy_calls == [4]
    partial = pwd.getpwnam(selected_name)
    registry = Path(options["state_dir"]) / "registry.json"
    record = json.loads(registry.read_text(encoding="utf-8"))["users"][identity]
    assert record["username"] == selected_name
    assert record["prefix"] == "rr_"
    assert record["state"] != "ready"
    sentinel = Path(selected_home) / "preserve-on-retry.txt"
    sentinel.write_text("keep the selected random workspace", encoding="utf-8")

    def unexpected_randomness(_byte_count):
        raise AssertionError("Retry must use the persisted random name")

    monkeypatch.setattr(user_isolation.secrets, "token_hex", unexpected_randomness)
    recovered = WorkspaceProvisioner(**options).resolve(identity)
    assert_ready_account(recovered, identity, options["home_root"])
    assert (recovered.username, recovered.home, recovered.uid, recovered.gid) == (
        selected_name, selected_home, partial.pw_uid, partial.pw_gid,
    )
    assert sentinel.read_text(encoding="utf-8") == "keep the selected random workspace"
    assert original_marker.read_text(encoding="utf-8") == "belongs to the previous occupant"


def test_verified_existing_out_of_range_account_keeps_its_numeric_ids(workspace):
    """AC-034: interval constraints apply to newly created accounts, not migration."""
    provisioner, options, identity = workspace
    record = binding(options, identity, uid=free_uid(25001), gid=free_gid(26001))
    name, home = record["username"], record["home"]
    run_account_tool("groupadd", "-g", str(record["gid"]), name)
    marker = "ot-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    run_account_tool(
        "useradd", "-m", "-s", "/bin/bash", "-d", home,
        "-u", str(record["uid"]), "-g", str(record["gid"]), "-c", marker, name,
    )
    write_registry(options, {identity: record})
    sentinel = Path(home) / "existing.txt"
    sentinel.write_text("out-of-range account is preserved", encoding="utf-8")
    account = provisioner.resolve(identity)
    assert (account.user_id, account.username, account.home) == (identity, name, home)
    assert (account.uid, account.gid) == (record["uid"], record["gid"])
    assert (pwd.getpwnam(name).pw_uid, pwd.getpwnam(name).pw_gid) == (record["uid"], record["gid"])
    assert grp.getgrnam(name).gr_gid == record["gid"]
    assert sentinel.read_text(encoding="utf-8") == "out-of-range account is preserved"


def test_absent_os_account_is_recreated_from_retained_registry_and_home(workspace):
    """AC-034: model container-local passwd/group loss while retaining the volume."""
    provisioner, options, identity = workspace
    original = provisioner.resolve(identity)
    sentinel = Path(original.home) / "persistent.txt"
    sentinel.write_text("retained volume data", encoding="utf-8")
    run_account_tool("userdel", original.username)  # Intentionally omit -r.
    try:
        grp.getgrnam(original.username)
    except KeyError:
        pass  # Some distributions remove an unused private group with userdel.
    else:
        run_account_tool("groupdel", original.username)
    assert sentinel.read_text(encoding="utf-8") == "retained volume data"
    recovered = WorkspaceProvisioner(**options).resolve(identity)
    assert_ready_account(recovered, identity, options["home_root"])
    assert recovered.username == original.username
    assert recovered.home == original.home
    assert sentinel.read_text(encoding="utf-8") == "retained volume data"
    assert (sentinel.stat().st_uid, sentinel.stat().st_gid) == (recovered.uid, recovered.gid)


@pytest.mark.parametrize("state", ["planned", "group_ready"])
def test_reserved_private_group_can_be_resumed_after_interruption(workspace, state):
    """AC-023: the durable full-ID reservation permits its own incomplete group."""
    provisioner, options, identity = workspace
    record = binding(options, identity, state=state)
    write_registry(options, {identity: record})
    run_account_tool("groupadd", "-g", str(record["gid"]), record["username"])
    account = provisioner.resolve(identity)
    assert_ready_account(account, identity, options["home_root"])
    assert (account.uid, account.gid) == (record["uid"], record["gid"])
    persisted = json.loads((Path(options["state_dir"]) / "registry.json").read_text(encoding="utf-8"))
    assert persisted["users"][identity]["state"] == "ready"


def test_symbolic_link_home_is_not_adopted_or_repaired(workspace):
    provisioner, options, identity = workspace
    record = binding(options, identity, state="planned")
    target = Path(options["home_root"]).parent / "someone-else"
    target.mkdir()
    sentinel = target / "private.txt"
    sentinel.write_text("belongs to someone else", encoding="utf-8")
    home = Path(record["home"])
    home.symlink_to(target, target_is_directory=True)
    write_registry(options, {identity: record})
    ownership = (target.stat().st_uid, target.stat().st_gid, target.stat().st_mode)
    users_before, groups_before = list(pwd.getpwall()), list(grp.getgrall())
    with pytest.raises(WorkspaceProvisionError) as failure:
        provisioner.resolve(identity)
    assert failure.value.code == "identity_conflict"
    assert home.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "belongs to someone else"
    assert (target.stat().st_uid, target.stat().st_gid, target.stat().st_mode) == ownership
    assert list(pwd.getpwall()) == users_before
    assert list(grp.getgrall()) == groups_before


def resolve_in_child(options, identity, barrier):
    """Spawned processes get fresh module state and no inherited resolver cache."""
    resolver = WorkspaceProvisioner(**options)
    barrier.wait(timeout=20)
    return resolver.resolve(identity)


@pytest.mark.parametrize("scenario", ["same-user", "different-users", "colliding-users"])
def test_processes_share_one_consistent_initialization_journal(workspace, scenario):
    """AC-004: a thread-only lock cannot satisfy this black-box test."""
    _, options, identity = workspace
    if scenario == "colliding-users":
        options = {**options, "username_prefix": "pc_"}
        identities = ["req001-collision-49585", "req001-collision-89826"]
    else:
        identities = [identity if scenario == "same-user" else f"{identity}-{index}" for index in range(4)]
    count = len(identities)
    context = multiprocessing.get_context("spawn")
    with context.Manager() as manager:
        barrier = manager.Barrier(count)
        executor = ProcessPoolExecutor(max_workers=count, mp_context=context)
        futures = [executor.submit(resolve_in_child, options, user_id, barrier) for user_id in identities]
        try:
            deadline = time.monotonic() + 65
            accounts = [future.result(timeout=max(0.1, deadline - time.monotonic())) for future in futures]
        finally:
            # Python 3.12 has no public terminate_workers. Retain handles only for
            # bounded cleanup of test workers if a broken lock implementation hangs.
            workers = list(executor._processes.values())
            executor.shutdown(wait=False, cancel_futures=True)
            for worker in workers:
                worker.join(timeout=2)
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=5)
    for user_id, account in zip(identities, accounts):
        assert_ready_account(account, user_id, options["home_root"])
    expected_count = len(set(identities))
    for field in ("username", "home", "uid", "gid"):
        assert len({getattr(account, field) for account in accounts}) == expected_count
    if scenario == "colliding-users":
        assert "pc_19042bfe" in {account.username for account in accounts}
        for user_id, account in zip(identities, accounts):
            assert WorkspaceProvisioner(**options).resolve(user_id) == account
