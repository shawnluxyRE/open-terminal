"""REQ-001 public provisioning contracts; run only in a disposable Linux container.

These tests deliberately expect working behavior. R2 leaves the new entry points
unimplemented, so failures at those entry points are the recorded design baseline.
No test treats NotImplementedError as success or mocks WorkspaceProvisioner.resolve.
"""

from concurrent.futures import ThreadPoolExecutor
import grp
import os
from pathlib import Path
import platform
import pwd
import re
import subprocess
import threading
import uuid

import pytest

from open_terminal.utils.identity import WorkspaceProvisionError
from open_terminal.utils import user_isolation
from open_terminal.utils.user_isolation import (
    WorkspaceProvisioner,
    allocate_workspace_ids,
    derive_workspace_username,
    generate_workspace_username,
)


@pytest.mark.parametrize("user_id", ["a", "root", "user", "Alice", "A.b:c@d-_", "9" * 128])
def test_username_is_stable_bounded_and_not_an_input_account(user_id):
    """AC-001/002: short and system-like IDs are identities, not account names."""
    name = derive_workspace_username(user_id)
    assert name == derive_workspace_username(user_id)
    assert re.fullmatch(r"(?:[a-f][a-f0-9]{7}|u[0-9][a-f0-9]{7})", name)
    assert name != user_id
    assert len(name) in (8, 9)


@pytest.mark.parametrize(
    "user_id,expected",
    [
        ("a", "ca978112"),
        ("abc", "ba7816bf"),
        ("alice", "u2bd806c9"),
        ("sample-1", "u0899cd85"),
        ("sample-9", "u1a9f9193"),
        ("sample-2", "u2be9fec9"),
        ("sample-12", "u3a7d1f95"),
        ("sample-35", "u45ea2d13"),
        ("sample-24", "u595b6ec3"),
        ("sample-4", "u6e16930f"),
        ("sample-3", "u73014671"),
        ("sample-23", "u839c3dfb"),
        ("sample-22", "u964e7ddb"),
        ("sample-0", "a5bb10eb"),
        ("sample-8", "b1b7dd7a"),
        ("sample-33", "ca1eed8e"),
        ("sample-5", "d069ad2e"),
        ("sample-36", "e81ec5bc"),
        ("sample-11", "f897eedc"),
    ],
)
def test_fixed_eight_digit_hash_examples_apply_numeric_leading_guard(user_id, expected):
    """Independent .NET SHA256 fixtures cover every possible first hex character."""
    assert derive_workspace_username(user_id) == expected


@pytest.mark.parametrize(
    "first,second",
    [
        ("abcdefgh-one", "abcdefgh-two"),
        ("ab.cd", "abcd"),
        ("Alice", "alice"),
        ("a", "b"),
    ],
)
def test_complete_case_sensitive_identity_controls_mapping(first, second):
    """AC-002: old cleaning/truncation collisions must not share a name."""
    assert derive_workspace_username(first) != derive_workspace_username(second)


@pytest.mark.parametrize(
    "user_id,prefix,expected",
    [
        ("alice", "", "u2bd806c9"),
        ("alice", "r2_", "r2_2bd806c9"),
        ("alice", "_", "_2bd806c9"),
        ("alice", "abcdefg", "abcdefg2bd806c9"),
        ("a", "", "ca978112"),
        ("a", "r2_", "r2_ca978112"),
        ("a", "_", "_ca978112"),
        ("a", "abcdefg", "abcdefgca978112"),
    ],
)
def test_prefix_is_applied_before_checking_final_leading_character(user_id, prefix, expected):
    name = derive_workspace_username(user_id, prefix=prefix)
    assert name == expected
    assert len(name) <= 15


@pytest.mark.parametrize("prefix", ["abcdefgh", "UPPER", "1bad", "a-b", "../", " x", "中"])
def test_invalid_prefix_is_rejected(prefix):
    with pytest.raises(ValueError):
        derive_workspace_username("prefix-example", prefix=prefix)


@pytest.mark.parametrize("prefix,expected", [("", "u19042bfe"), ("r2_", "r2_19042bfe")])
def test_known_distinct_ids_share_an_eight_digit_candidate(prefix, expected):
    """A fixed real collision is a regression fixture, not a probability claim."""
    assert derive_workspace_username("req001-collision-49585", prefix=prefix) == expected
    assert derive_workspace_username("req001-collision-89826", prefix=prefix) == expected


@pytest.mark.parametrize(
    "entropy,prefix,expected",
    [
        ("0123abcd", "", "u0123abcd"),
        ("abcdef01", "", "abcdef01"),
        ("0123abcd", "r2_", "r2_0123abcd"),
        ("abcdef01", "abcdefg", "abcdefgabcdef01"),
    ],
)
def test_random_candidate_uses_four_entropy_bytes_and_final_leading_guard(monkeypatch, entropy, prefix, expected):
    calls = []

    def token_hex(byte_count):
        calls.append(byte_count)
        return entropy

    monkeypatch.setattr(user_isolation.secrets, "token_hex", token_hex)
    assert generate_workspace_username(prefix=prefix) == expected
    assert calls == [4]


@pytest.mark.parametrize(
    "used_uids,used_gids,expected",
    [
        (set(), set(), (10001, 10001)),
        ({10001, 10002}, {10001}, (10003, 10002)),
        ({0, 1000, 10000, 20000}, {0, 10000, 20000}, (10001, 10001)),
        (set(range(10001, 19999)), set(range(10001, 19999)), (19999, 19999)),
        ({10001}, set(range(10001, 19999)), (10002, 19999)),
    ],
)
def test_allocation_uses_independent_inclusive_pools(used_uids, used_gids, expected):
    """AC-032/033: test specified examples, including both inclusive endpoints."""
    before_uids, before_gids = used_uids.copy(), used_gids.copy()
    assert allocate_workspace_ids(used_uids, used_gids) == expected
    assert used_uids == before_uids
    assert used_gids == before_gids


@pytest.mark.parametrize(
    "used_uids,used_gids,code",
    [
        (set(range(10001, 20000)), set(), "uid_pool_exhausted"),
        (set(), set(range(10001, 20000)), "gid_pool_exhausted"),
        (set(range(10001, 20000)), set(range(10001, 20000)), "uid_pool_exhausted"),
    ],
)
def test_pool_exhaustion_is_explicit_and_does_not_change_inputs(used_uids, used_gids, code):
    before_uids, before_gids = used_uids.copy(), used_gids.copy()
    with pytest.raises(WorkspaceProvisionError) as failure:
        allocate_workspace_ids(used_uids, used_gids)
    assert failure.value.code == code
    assert used_uids == before_uids
    assert used_gids == before_gids


@pytest.fixture
def workspace(tmp_path):
    """Every OS-mutating test requires explicit opt-in and disposable Docker root."""
    if (
        os.environ.get("REQ001_DISPOSABLE_CONTAINER") != "1"
        or platform.system() != "Linux"
        or os.geteuid() != 0
        or not Path("/.dockerenv").is_file()
    ):
        pytest.fail("OS tests require REQ001_DISPOSABLE_CONTAINER=1 in a disposable root Linux Docker container")
    home_root = tmp_path / "homes"
    home_root.mkdir()
    options = {
        "state_dir": str(tmp_path / "state"),
        "home_root": str(home_root),
        "username_prefix": "r2_",
    }
    return WorkspaceProvisioner(**options), options, "req001-" + uuid.uuid4().hex


def assert_ready_account(account, user_id, home_root):
    """Verify the public result against real passwd/group/filesystem observations."""
    assert account.user_id == user_id
    assert 10001 <= account.uid <= 19999
    assert 10001 <= account.gid <= 19999
    assert account.home == str(Path(home_root) / account.username)
    record = pwd.getpwnam(account.username)
    assert (record.pw_uid, record.pw_gid, record.pw_dir) == (account.uid, account.gid, account.home)
    assert grp.getgrnam(account.username).gr_gid == account.gid
    home = Path(account.home)
    assert home.is_dir()
    assert (home.stat().st_uid, home.stat().st_gid) == (account.uid, account.gid)
    assert home.stat().st_mode & 0o7777 == 0o2770


def test_two_users_receive_distinct_ready_workspaces(workspace):
    """AC-001/032: account, private group, and home must all be distinct."""
    provisioner, options, identity = workspace
    original_users = {record.pw_name: (record.pw_uid, record.pw_gid) for record in pwd.getpwall()}
    original_groups = {record.gr_name: record.gr_gid for record in grp.getgrall()}
    first = provisioner.resolve(identity + "-A")
    second = provisioner.resolve(identity + "-B")
    assert_ready_account(first, identity + "-A", options["home_root"])
    assert_ready_account(second, identity + "-B", options["home_root"])
    for field in ("username", "home", "uid", "gid"):
        assert getattr(first, field) != getattr(second, field)
    for name, expected in original_users.items():
        record = pwd.getpwnam(name)
        assert (record.pw_uid, record.pw_gid) == expected
    for name, expected in original_groups.items():
        assert grp.getgrnam(name).gr_gid == expected


def test_repeated_access_and_new_provisioner_preserve_home_and_ids(workspace):
    """AC-001/034: no memory-cache dependence or per-chat/display-name mapping."""
    provisioner, options, identity = workspace
    first = provisioner.resolve(identity)
    marker = Path(first.home) / "keep.txt"
    marker.write_text("same account after reconnect", encoding="utf-8")
    assert provisioner.resolve(identity) == first
    assert WorkspaceProvisioner(**options).resolve(identity) == first
    assert marker.read_text(encoding="utf-8") == "same account after reconnect"


@pytest.mark.parametrize("same_user", [True, False], ids=["same-user", "different-users"])
def test_concurrent_first_access_has_no_duplicate_or_shared_identity(workspace, same_user):
    """AC-004: separate resolver instances contend for one shared registry."""
    _, options, identity = workspace
    count = 6
    barrier = threading.Barrier(count)
    identities = [identity if same_user else f"{identity}-{index}" for index in range(count)]

    def prepare(user_id):
        resolver = WorkspaceProvisioner(**options)
        barrier.wait(timeout=10)
        return resolver.resolve(user_id)

    with ThreadPoolExecutor(max_workers=count) as executor:
        accounts = list(executor.map(prepare, identities))
    for user_id, account in zip(identities, accounts):
        assert_ready_account(account, user_id, options["home_root"])
    expected_count = 1 if same_user else count
    for field in ("username", "home", "uid", "gid"):
        assert len({getattr(account, field) for account in accounts}) == expected_count


def run_account_tool(*args):
    subprocess.run(args, check=True, capture_output=True, text=True)


@pytest.mark.parametrize("resource", ["account", "group", "home"])
def test_unowned_existing_resource_is_skipped_without_adoption(workspace, resource):
    """AC-002/033: a fresh binding chooses another name, preserving the occupant."""
    provisioner, options, identity = workspace
    name = derive_workspace_username(identity, prefix=options["username_prefix"])
    home = Path(options["home_root"]) / name
    if resource == "account":
        run_account_tool("useradd", "-U", "-m", "-d", str(home), name)
    elif resource == "group":
        run_account_tool("groupadd", name)
    else:
        home.mkdir()
    sentinel = None
    if home.exists():
        sentinel = home / "not-owned-by-requester.txt"
        sentinel.write_text("must remain unchanged", encoding="utf-8")
    users_before = list(pwd.getpwall())
    groups_before = list(grp.getgrall())
    home_before = (home.stat().st_uid, home.stat().st_gid, home.stat().st_mode) if home.exists() else None
    account = provisioner.resolve(identity)
    assert_ready_account(account, identity, options["home_root"])
    assert account.username != name
    assert account.home != str(home)
    for record in users_before:
        assert pwd.getpwnam(record.pw_name) == record
    for record in groups_before:
        assert grp.getgrnam(record.gr_name) == record
    if sentinel:
        assert sentinel.read_text(encoding="utf-8") == "must remain unchanged"
        assert (home.stat().st_uid, home.stat().st_gid, home.stat().st_mode) == home_before


def test_retry_after_permission_step_failure_reuses_original_account(workspace, monkeypatch):
    """AC-023: isolate an OS-command failure, not the business result under test."""
    provisioner, options, identity = workspace
    real_run = user_isolation._run_privileged
    injected = False
    name = derive_workspace_username(identity, prefix=options["username_prefix"])
    expected_home = str(Path(options["home_root"]) / name)

    def fail_chmod_once(command):
        nonlocal injected
        if (
            command and Path(command[0]).name == "chmod"
            and "2770" in command and command[-1] == expected_home
            and not injected
        ):
            injected = True
            raise subprocess.CalledProcessError(1, command, stderr=b"injected permission failure")
        return real_run(command)

    monkeypatch.setattr(user_isolation, "_run_privileged", fail_chmod_once)
    with pytest.raises(WorkspaceProvisionError):
        provisioner.resolve(identity)
    assert injected, "The test must reach the real OS permission step"
    partial = pwd.getpwnam(name)
    marker = Path(partial.pw_dir) / "preserve-on-retry.txt"
    marker.write_text("retry retains data", encoding="utf-8")
    recovered = WorkspaceProvisioner(**options).resolve(identity)
    assert_ready_account(recovered, identity, options["home_root"])
    assert (recovered.uid, recovered.gid, recovered.home) == (partial.pw_uid, partial.pw_gid, partial.pw_dir)
    assert marker.read_text(encoding="utf-8") == "retry retains data"


def test_resolver_skips_real_independent_uid_and_gid_reservations(workspace):
    """AC-033: actual NSS occupants remain intact while allocation skips them."""
    provisioner, options, identity = workspace
    occupied_uids = {record.pw_uid for record in pwd.getpwall()}
    occupied_gids = {record.gr_gid for record in grp.getgrall()}
    free_uids = [number for number in range(10001, 20000) if number not in occupied_uids][:3]
    free_gids = [number for number in range(10001, 20000) if number not in occupied_gids][:2]
    assert len(free_uids) == 3 and len(free_gids) == 2, "Disposable fixture requires a few free IDs"
    # Reserve one GID and one or two UIDs so the next free values are different.
    reserved_uids = free_uids[:2] if free_uids[1] == free_gids[1] else free_uids[:1]
    expected_uid = free_uids[len(reserved_uids)]
    expected_gid = free_gids[1]
    assert expected_uid != expected_gid
    token = uuid.uuid4().hex[:12]
    foreign_group = "reserved_g_" + token
    run_account_tool("groupadd", "-g", str(free_gids[0]), foreign_group)
    foreign_users = []
    sentinels = []
    for index, uid in enumerate(reserved_uids):
        name = f"reserved_u_{token}_{index}"
        home = Path(options["home_root"]).parent / name
        run_account_tool(
            "useradd", "-m", "-d", str(home), "-u", str(uid),
            "-g", str(free_gids[0]), name,
        )
        foreign_users.append(pwd.getpwnam(name))
        sentinel = home / "occupied-owner-data.txt"
        sentinel.write_text(f"belongs to occupied UID {uid}", encoding="utf-8")
        os.chown(sentinel, uid, free_gids[0])
        sentinels.append((sentinel, sentinel.read_bytes(), sentinel.stat()))
    group_before = grp.getgrnam(foreign_group)
    account = provisioner.resolve(identity)
    assert_ready_account(account, identity, options["home_root"])
    assert (account.uid, account.gid) == (expected_uid, expected_gid)
    assert account.uid not in reserved_uids
    assert account.gid != free_gids[0]
    for record in foreign_users:
        assert pwd.getpwnam(record.pw_name) == record
    assert grp.getgrnam(foreign_group) == group_before
    for sentinel, content, before in sentinels:
        assert sentinel.read_bytes() == content
        after = sentinel.stat()
        assert (after.st_uid, after.st_gid, after.st_mode) == (before.st_uid, before.st_gid, before.st_mode)


@pytest.mark.parametrize("pool", ["uid", "gid"])
def test_resolver_exhaustion_from_nss_snapshot_creates_no_os_resources(workspace, monkeypatch, pool):
    """AC-033: fake only NSS's external occupancy snapshot, never allocation."""
    provisioner, options, identity = workspace
    real_getpwall, real_getgrall = pwd.getpwall, grp.getgrall
    users_before, groups_before = list(real_getpwall()), list(real_getgrall())
    sentinel = Path(options["home_root"]) / "preserve-existing-data.txt"
    sentinel.write_text("pool exhaustion must not alter existing files", encoding="utf-8")
    content_before, stat_before = sentinel.read_bytes(), sentinel.stat()
    home_entries = sorted(Path(options["home_root"]).iterdir())
    snapshot_calls = []
    if pool == "uid":
        occupied = {record.pw_uid for record in users_before}
        full_snapshot = users_before + [
            pwd.struct_passwd((f"external_uid_{number}", "x", number, 0, "", "/nonexistent", "/usr/sbin/nologin"))
            for number in range(10001, 20000) if number not in occupied
        ]

        def read_full_snapshot():
            snapshot_calls.append("uid")
            return list(full_snapshot)

        monkeypatch.setattr(pwd, "getpwall", read_full_snapshot)
    else:
        occupied = {record.gr_gid for record in groups_before}
        full_snapshot = groups_before + [
            grp.struct_group((f"external_gid_{number}", "x", number, []))
            for number in range(10001, 20000) if number not in occupied
        ]

        def read_full_snapshot():
            snapshot_calls.append("gid")
            return list(full_snapshot)

        monkeypatch.setattr(grp, "getgrall", read_full_snapshot)
    with pytest.raises(WorkspaceProvisionError) as failure:
        provisioner.resolve(identity)
    assert failure.value.code == f"{pool}_pool_exhausted"
    assert snapshot_calls, "The real resolver must inspect the external NSS snapshot"
    assert list(real_getpwall()) == users_before
    assert list(real_getgrall()) == groups_before
    assert sorted(Path(options["home_root"]).iterdir()) == home_entries
    assert sentinel.read_bytes() == content_before
    after = sentinel.stat()
    assert (after.st_uid, after.st_gid, after.st_mode) == (stat_before.st_uid, stat_before.st_gid, stat_before.st_mode)
