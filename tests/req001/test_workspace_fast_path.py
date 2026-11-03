"""Measure warmed resolution and verify that cached readiness stays observable.

Timing is diagnostic, not a machine-specific latency threshold. The performance
gate checks external preparation work; no resolver/cache result is substituted.
"""

import builtins
from collections import Counter
import grp
import io
import json
import math
import multiprocessing
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import subprocess
import tempfile
import time
import uuid

import pytest

from open_terminal.utils import user_isolation
from open_terminal.utils.identity import WorkspaceProvisionError
from open_terminal.utils.user_isolation import WorkspaceProvisioner
from test_workspace_provisioning import run_account_tool, workspace


def percentiles(samples):
    ordered = sorted(samples)
    return {
        "p50_ms": ordered[math.ceil(len(ordered) * .50) - 1] * 1000,
        "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1] * 1000,
    }


def test_stable_ready_hits_do_not_repeat_preparation_io(workspace, monkeypatch, record_property):
    provisioner, options, identity = workspace
    ready = provisioner.resolve(identity)
    for _ in range(5):
        assert WorkspaceProvisioner(**options).resolve(identity) == ready
    registry = Path(options["state_dir"]) / "registry.json"
    original_registry = registry.read_bytes()
    directory_info = Path(options["state_dir"]).stat()
    calls = Counter()
    real_open, real_io_open, real_os_open, real_fsync = builtins.open, io.open, os.open, os.fsync
    real_load, real_loads = json.load, json.loads
    real_users, real_groups = pwd.getpwall, grp.getgrall
    real_command = user_isolation._run_privileged

    def observe_open(path, *args, **kwargs):
        if isinstance(path, (str, bytes, os.PathLike)) and Path(os.fsdecode(path)) == registry:
            calls["registry_open"] += 1
        return real_open(path, *args, **kwargs)

    def observe_io_open(path, *args, **kwargs):
        if isinstance(path, (str, bytes, os.PathLike)) and Path(os.fsdecode(path)) == registry:
            calls["registry_io_open"] += 1
        return real_io_open(path, *args, **kwargs)

    def observe_os_open(path, *args, **kwargs):
        target = Path(os.fsdecode(path))
        if not target.is_absolute() and kwargs.get("dir_fd") is not None:
            target = Path(os.readlink(f"/proc/self/fd/{kwargs['dir_fd']}")) / target
        if target == registry:
            calls["registry_os_open"] += 1
        return real_os_open(path, *args, **kwargs)

    def observe_load(*args, **kwargs):
        calls["json_load"] += 1
        return real_load(*args, **kwargs)

    def observe_loads(*args, **kwargs):
        calls["json_loads"] += 1
        return real_loads(*args, **kwargs)

    def observe_fsync(fd):
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == (
            directory_info.st_dev, directory_info.st_ino,
        ):
            calls["registry_directory_fsync"] += 1
        return real_fsync(fd)

    def observe_users():
        calls["full_passwd_scan"] += 1
        return real_users()

    def observe_groups():
        calls["full_group_scan"] += 1
        return real_groups()

    def observe_command(command):
        calls["privileged_command"] += 1
        return real_command(command)

    samples = []
    with monkeypatch.context() as observer:
        observer.setattr(builtins, "open", observe_open)
        observer.setattr(io, "open", observe_io_open)
        observer.setattr(os, "open", observe_os_open)
        observer.setattr(json, "load", observe_load)
        observer.setattr(json, "loads", observe_loads)
        observer.setattr(os, "fsync", observe_fsync)
        observer.setattr(pwd, "getpwall", observe_users)
        observer.setattr(grp, "getgrall", observe_groups)
        observer.setattr(user_isolation, "_run_privileged", observe_command)
        for _ in range(100):
            start = time.perf_counter()
            actual = WorkspaceProvisioner(**options).resolve(identity)
            samples.append(time.perf_counter() - start)
            assert actual == ready

    report = {"samples": len(samples), **percentiles(samples), "external_calls": dict(calls)}
    record_property("warmed_resolution", json.dumps(report, sort_keys=True))
    print("WARMED_RESOLUTION " + json.dumps(report, sort_keys=True))
    assert registry.read_bytes() == original_registry
    assert not calls, f"Stable ready requests repeated preparation work: {report}"


def test_warmed_binding_observes_replaced_invalid_registry(workspace):
    provisioner, options, identity = workspace
    ready = provisioner.resolve(identity)
    assert WorkspaceProvisioner(**options).resolve(identity) == ready
    registry = Path(options["state_dir"]) / "registry.json"
    temporary = registry.with_name("invalid-replacement.json")
    temporary.write_text('{"version":99,"users":{}}', encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, registry)
    with pytest.raises(WorkspaceProvisionError) as rejected:
        WorkspaceProvisioner(**options).resolve(identity)
    assert rejected.value.code == "workspace_state_invalid"


def test_warmed_binding_repairs_changed_home_permissions_without_losing_data(workspace):
    provisioner, options, identity = workspace
    ready = provisioner.resolve(identity)
    assert WorkspaceProvisioner(**options).resolve(identity) == ready
    home = Path(ready.home)
    marker = home / "keep.txt"
    marker.write_text("same owned workspace", encoding="utf-8")
    home.chmod(0o700)
    actual = WorkspaceProvisioner(**options).resolve(identity)
    assert actual == ready
    assert stat.S_IMODE(home.stat().st_mode) == 0o2770
    assert marker.read_text(encoding="utf-8") == "same owned workspace"


def resolve_after_fork(options, identity, pipe):
    """A new process must establish its own durable observation before success."""
    directory = Path(options["state_dir"]).stat()
    real_fsync = os.fsync
    confirmations = []

    def observe(fd):
        info = os.fstat(fd)
        result = real_fsync(fd)
        if stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == (directory.st_dev, directory.st_ino):
            confirmations.append(True)
        return result

    os.fsync = observe
    try:
        account = WorkspaceProvisioner(**options).resolve(identity)
        pipe.send({"account": account, "durability_confirmed": bool(confirmations)})
    except BaseException as exc:
        pipe.send({"error": type(exc).__name__})
    finally:
        os.fsync = real_fsync
        pipe.close()


def test_forked_process_does_not_inherit_a_trusted_ready_cache(workspace):
    provisioner, options, identity = workspace
    ready = provisioner.resolve(identity)
    assert WorkspaceProvisioner(**options).resolve(identity) == ready
    context = multiprocessing.get_context("fork")
    receiving, sending = context.Pipe(duplex=False)
    worker = context.Process(target=resolve_after_fork, args=(options, identity, sending))
    worker.start()
    sending.close()
    try:
        assert receiving.poll(10), "Child resolution did not finish"
        result = receiving.recv()
        assert "error" not in result, result
        assert result["account"] == ready
        assert result["durability_confirmed"], "Inherited memory is not a new process's durability proof"
        worker.join(timeout=5)
        assert worker.exitcode == 0
    finally:
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)
        if worker.is_alive():
            worker.kill()
            worker.join(timeout=5)
        receiving.close()
    assert provisioner.resolve(identity) == ready


@pytest.mark.parametrize("alias", ["uid", "gid"])
def test_warmed_binding_detects_new_nss_numeric_aliases(workspace, alias):
    provisioner, options, identity = workspace
    account = provisioner.resolve(identity)
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    marker = Path(account.home) / "keep-before-alias.txt"
    marker.write_text("original owner")
    foreign = "alias_" + uuid.uuid4().hex[:12]
    if alias == "uid":
        run_account_tool("useradd", "-M", "-N", "-o", "-u", str(account.uid), "-g", "0", foreign)
    else:
        run_account_tool("groupadd", "-o", "-g", str(account.gid), foreign)
    registry = Path(options["state_dir"]) / "registry.json"
    before = registry.read_bytes()
    with pytest.raises(WorkspaceProvisionError) as failure:
        WorkspaceProvisioner(**options).resolve(identity)
    assert failure.value.code == "identity_conflict"
    assert registry.read_bytes() == before
    assert marker.read_text() == "original owner"


def test_unobservable_nss_backend_uses_full_verification_on_each_call(workspace, monkeypatch):
    provisioner, options, identity = workspace
    account = provisioner.resolve(identity)
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    configuration = Path("/etc/nsswitch.conf")
    original = configuration.read_text()
    changed = re.sub(r"^(passwd|group):([^\n]*)", r"\1:\2 req001_missing_backend", original, flags=re.MULTILINE)
    assert changed != original
    scans = Counter()
    real_users, real_groups = pwd.getpwall, grp.getgrall

    def users():
        scans["users"] += 1
        return real_users()

    def groups():
        scans["groups"] += 1
        return real_groups()

    try:
        configuration.write_text(changed)
        with monkeypatch.context() as observe:
            observe.setattr(pwd, "getpwall", users)
            observe.setattr(grp, "getgrall", groups)
            for _ in range(2):
                before = dict(scans)
                assert WorkspaceProvisioner(**options).resolve(identity) == account
                assert scans["users"] > before.get("users", 0)
                assert scans["groups"] > before.get("groups", 0)
    finally:
        configuration.write_text(original)


@pytest.mark.parametrize("source", ["/run/host/userdb", "/run/systemd/userdb"])
def test_systemd_source_appearance_invalidates_a_warmed_observation(workspace, monkeypatch, source):
    provisioner, options, identity = workspace
    account = provisioner.resolve(identity)
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    directory = Path(source)
    created = []
    candidate = directory
    while not candidate.exists():
        created.append(candidate)
        candidate = candidate.parent
    marker = directory / ("req001-observation-" + uuid.uuid4().hex)
    real_users, real_groups = pwd.getpwall, grp.getgrall
    scans = Counter()

    def users():
        scans["users"] += 1
        return real_users()

    def groups():
        scans["groups"] += 1
        return real_groups()

    try:
        directory.mkdir(parents=True, exist_ok=True)
        marker.write_text("external NSS source appeared")
        with monkeypatch.context() as observe:
            observe.setattr(pwd, "getpwall", users)
            observe.setattr(grp, "getgrall", groups)
            assert WorkspaceProvisioner(**options).resolve(identity) == account
        assert scans["users"] and scans["groups"], "Changed NSS sources require full verification"
    finally:
        marker.unlink(missing_ok=True)
        for path in created:
            path.rmdir()


def test_warmed_owner_reconfirms_a_version_published_by_another_process(workspace, monkeypatch):
    provisioner, options, identity = workspace
    account = provisioner.resolve(identity)
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    context = multiprocessing.get_context("spawn")
    receiving, sending = context.Pipe(duplex=False)
    child = context.Process(target=resolve_after_fork, args=(options, identity + "-other", sending))
    child.start()
    sending.close()
    try:
        assert receiving.poll(10)
        result = receiving.recv()
        assert "error" not in result, result
        assert result["durability_confirmed"]
        child.join(5)
        assert child.exitcode == 0
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)
        receiving.close()
    real_fsync = os.fsync
    directory = Path(options["state_dir"]).stat()
    confirmations = []

    def confirm(fd):
        info = os.fstat(fd)
        result = real_fsync(fd)
        if stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == (directory.st_dev, directory.st_ino):
            confirmations.append(True)
        return result

    with monkeypatch.context() as observe:
        observe.setattr(os, "fsync", confirm)
        assert WorkspaceProvisioner(**options).resolve(identity) == account
    assert confirmations, "The parent's old cache is not proof of the child's new registry version"
    saved = json.loads((Path(options["state_dir"]) / "registry.json").read_text())["users"]
    assert set(saved) == {identity, identity + "-other"}
    assert all(record["state"] == "ready" for record in saved.values())


def test_registry_change_during_cache_observation_cannot_label_old_checks_as_new(workspace, monkeypatch):
    provisioner, options, identity = workspace
    account = provisioner.resolve(identity)
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    registry = Path(options["state_dir"]) / "registry.json"
    replacement = registry.with_name("replace-during-observation.json")
    replacement.write_text('{"version":99,"users":{}}')
    replacement.chmod(0o600)
    real_stat, real_lstat = os.stat, os.lstat
    injected = False

    def observe(real_function, path, *args, **kwargs):
        nonlocal injected
        result = real_function(path, *args, **kwargs)
        if not injected and isinstance(path, (str, bytes, os.PathLike)) and Path(os.fsdecode(path)) == registry:
            # Return the genuine old observation after publishing a new version.
            # A second observation must catch this controlled filesystem change.
            injected = True
            os.replace(replacement, registry)
        return result

    with monkeypatch.context() as change:
        change.setattr(os, "stat", lambda path, *a, **k: observe(real_stat, path, *a, **k))
        change.setattr(os, "lstat", lambda path, *a, **k: observe(real_lstat, path, *a, **k))
        with pytest.raises(WorkspaceProvisionError) as failure:
            WorkspaceProvisioner(**options).resolve(identity)
        assert failure.value.code == "workspace_state_invalid"
    assert injected


def test_nonroot_preexisting_workers_can_use_a_newly_warmed_binding(workspace):
    """Each real worker resolves, reads and writes with the standard SETGID runtime."""
    _, _, identity = workspace
    service = pwd.getpwnam("user")
    program = '''
import asyncio, concurrent.futures, json, os, pathlib, sys, threading
from open_terminal.utils.user_isolation import WorkspaceProvisioner
from open_terminal.utils.fs import UserFS
options, identity = json.loads(sys.argv[1]), sys.argv[2]
entered = threading.Barrier(5)
release = threading.Event()
def worker(index):
    entered.wait(timeout=10)
    assert release.wait(10)
    account = WorkspaceProvisioner(**options).resolve(identity)
    assert os.access(account.home, os.R_OK | os.W_OK | os.X_OK)
    path = str(pathlib.Path(account.home) / f"worker-{index}.txt")
    async def io():
        fs = UserFS(username=account.username, home=account.home)
        await fs.write(path, f"worker-{index}")
        assert await fs.read_text(path) == f"worker-{index}"
    asyncio.run(io())
    info = pathlib.Path(path).stat()
    assert (info.st_uid, info.st_gid) == (account.uid, account.gid)
    return threading.get_ident(), account.uid, account.gid
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
    futures = [executor.submit(worker, i) for i in range(4)]
    entered.wait(timeout=10)
    try:
        ready = WorkspaceProvisioner(**options).resolve(identity)
    finally:
        release.set()
    results = [future.result(timeout=15) for future in futures]
assert len({item[0] for item in results}) == 4
assert {(item[1], item[2]) for item in results} == {(ready.uid, ready.gid)}
print(json.dumps({"workers": 4, "uid": ready.uid, "gid": ready.gid}))
'''
    with tempfile.TemporaryDirectory(prefix="req001-worker-access-") as root:
        os.chown(root, service.pw_uid, service.pw_gid)
        # UserFS also invokes mkdir as the workspace account; like /home, the
        # test's parent must permit traversal before entering the private home.
        Path(root).chmod(0o711)
        home_root = Path(root) / "homes"
        home_root.mkdir(mode=0o755)
        options = {"state_dir": str(Path(root) / "state"), "home_root": str(home_root), "username_prefix": "wk_"}
        result = subprocess.run(
            ["sudo", "-u", service.pw_name, "env", "PYTHONDONTWRITEBYTECODE=1",
             "/usr/local/bin/python3-ot", "-c", program, json.dumps(options), identity],
            capture_output=True, text=True, timeout=35,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["workers"] == 4


def test_nonroot_concurrent_group_refresh_does_not_remove_a_returned_users_access(workspace):
    """An older A refresh cannot overwrite B's group after B becomes usable."""
    _, _, identity = workspace
    service = pwd.getpwnam("user")
    program = '''
from concurrent.futures import Future
import json, os, pathlib, pwd, sys, threading
from open_terminal.utils import user_isolation as module
options, identity = json.loads(sys.argv[1]), sys.argv[2]
entered, release, b_member, b_finished = (threading.Event() for _ in range(4))
real_groups, real_command = os.setgroups, module._run_privileged
service = pwd.getpwuid(os.getuid()).pw_name
b_name = module.derive_workspace_username(identity + "-B", options["username_prefix"])
workers = []
def command(arguments):
    result = real_command(arguments)
    if pathlib.Path(arguments[0]).name == "usermod" and "-aG" in arguments and b_name in arguments and arguments[-1] == service:
        b_member.set()
    return result
def groups(values):
    if threading.current_thread().name == "prepare-A" and not entered.is_set():
        entered.set()
        assert release.wait(15), "Group refresh gate was not released"
    return real_groups(values)
def start(label):
    future = Future()
    def prepare():
        future.set_running_or_notify_cancel()
        try:
            account = module.WorkspaceProvisioner(**options).resolve(identity + "-" + label)
            pathlib.Path(account.home, "usable.txt").write_text(label)
            future.set_result(account)
        except BaseException as error:
            future.set_exception(error)
        finally:
            if label == "B": b_finished.set()
    worker = threading.Thread(target=prepare, name="prepare-" + label, daemon=True)
    workers.append(worker)
    worker.start()
    return future
os.setgroups, module._run_privileged = groups, command
try:
    first = start("A")
    assert entered.wait(10)
    second = start("B")
    assert b_member.wait(10), "B's membership update should not wait behind A's short refresh lock"
    # Without refresh serialization B can finish here; a correct short lock
    # postpones B's refresh until A releases, after which B reads fresh groups.
    early_b = b_finished.wait(.5)
    release.set()
    a, b = first.result(10), second.result(10)
    assert {a.gid, b.gid}.issubset(os.getgroups()), (a.gid, b.gid, os.getgroups())
    assert os.access(b.home, os.R_OK | os.W_OK | os.X_OK)
    marker = pathlib.Path(b.home, "usable.txt")
    with marker.open("a") as stream: stream.write(" remains usable after A")
    assert marker.read_text() == "B remains usable after A"
    print(json.dumps({"access_preserved": True, "b_returned_before_a_release": early_b}))
finally:
    release.set()
    for worker in workers: worker.join(15)
    os.setgroups, module._run_privileged = real_groups, real_command
    assert not any(worker.is_alive() for worker in workers)
'''
    with tempfile.TemporaryDirectory(prefix="req001-refresh-order-") as root:
        os.chown(root, service.pw_uid, service.pw_gid)
        Path(root).chmod(0o711)
        home_root = Path(root) / "homes"
        home_root.mkdir(mode=0o755)
        options = {"state_dir": str(Path(root) / "state"), "home_root": str(home_root), "username_prefix": "gr_"}
        result = subprocess.run(
            ["sudo", "-u", service.pw_name, "env", "PYTHONDONTWRITEBYTECODE=1",
             "/usr/local/bin/python3-ot", "-c", program, json.dumps(options), identity],
            capture_output=True, text=True, timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["access_preserved"]


def test_warmed_binding_rejects_a_home_root_replaced_by_a_symlink(workspace):
    provisioner, options, identity = workspace
    account = provisioner.resolve(identity)
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    home_root = Path(options["home_root"])
    marker = Path(account.home) / "keep-root-replacement.txt"
    marker.write_text("same directory data")
    original_users, original_groups = list(pwd.getpwall()), list(grp.getgrall())
    retained = home_root.with_name(home_root.name + "-retained")
    home_root.rename(retained)
    home_root.symlink_to(retained, target_is_directory=True)
    try:
        with pytest.raises(WorkspaceProvisionError) as failure:
            WorkspaceProvisioner(**options).resolve(identity)
        assert failure.value.code == "workspace_config_invalid"
        assert (list(pwd.getpwall()), list(grp.getgrall())) == (original_users, original_groups)
        assert marker.read_text() == "same directory data"
    finally:
        home_root.unlink()
        retained.rename(home_root)


def test_nss_configuration_symlink_targets_require_full_verification(workspace, monkeypatch, tmp_path):
    provisioner, options, identity = workspace
    account = provisioner.resolve(identity)
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    configuration = Path("/etc/nsswitch.conf")
    assert not configuration.is_symlink()
    original = configuration.read_bytes()
    backup = configuration.with_name("nsswitch.conf.req001-" + uuid.uuid4().hex)
    targets = [tmp_path / "nss-source-one.conf", tmp_path / "nss-source-two.conf"]
    for target in targets:
        target.write_bytes(original)
        target.chmod(0o644)
    scans = Counter()
    real_users, real_groups = pwd.getpwall, grp.getgrall

    def users():
        scans["users"] += 1
        return real_users()

    def groups():
        scans["groups"] += 1
        return real_groups()

    configuration.rename(backup)
    try:
        with monkeypatch.context() as observe:
            observe.setattr(pwd, "getpwall", users)
            observe.setattr(grp, "getgrall", groups)
            for target in targets:
                configuration.unlink(missing_ok=True)
                configuration.symlink_to(target)
                before = dict(scans)
                assert WorkspaceProvisioner(**options).resolve(identity) == account
                assert scans["users"] > before.get("users", 0)
                assert scans["groups"] > before.get("groups", 0)
    finally:
        configuration.unlink(missing_ok=True)
        backup.rename(configuration)


@pytest.mark.parametrize("scenario", ["transient-membership-failure", "socket-appears", "socket-gid-changes"])
def test_ready_reuse_maintains_optional_docker_socket_access(workspace, monkeypatch, scenario):
    """The ready optimization must retain optional socket-group maintenance."""
    provisioner, options, identity = workspace
    socket_path = Path("/var/run/docker.sock")
    assert not os.path.lexists(socket_path), "This disposable test must own its socket path"
    occupied = {group.gr_gid for group in grp.getgrall()}
    identifiers = [number for number in range(25000, 60000) if number not in occupied][:2]
    assert len(identifiers) == 2
    suffix = uuid.uuid4().hex[:16]
    group_names = ["sock_" + suffix + "_a", "sock_" + suffix + "_b"]
    # Both groups exist before warming, so socket changes alone must be observed.
    for name, gid in zip(group_names, identifiers):
        run_account_tool("groupadd", "-g", str(gid), name)

    real_command = user_isolation._run_privileged
    membership_attempts = []

    def fail_first_socket_membership(command):
        if command[:3] == ["usermod", "-aG", group_names[0]]:
            membership_attempts.append(tuple(command))
            if scenario == "transient-membership-failure" and len(membership_attempts) == 1:
                raise subprocess.CalledProcessError(1, command, stderr=b"transient socket-group update failure")
        return real_command(command)

    monkeypatch.setattr(user_isolation, "_run_privileged", fail_first_socket_membership)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bound = False
    try:
        if scenario != "socket-appears":
            listener.bind(str(socket_path))
            bound = True
            os.chown(socket_path, 0, identifiers[0])
            socket_path.chmod(0o660)

        account = provisioner.resolve(identity)
        if scenario == "transient-membership-failure":
            assert len(membership_attempts) == 1
            assert account.username not in grp.getgrnam(group_names[0]).gr_mem
        else:
            assert WorkspaceProvisioner(**options).resolve(identity) == account
            if scenario == "socket-gid-changes":
                assert account.username in grp.getgrnam(group_names[0]).gr_mem

        registry = Path(options["state_dir"]) / "registry.json"
        original_registry = registry.read_bytes()
        marker = Path(account.home) / "keep-during-socket-maintenance.txt"
        marker.write_text("same workspace and data", encoding="utf-8")
        os.chown(marker, account.uid, account.gid)
        marker.chmod(0o640)
        original_marker = marker.stat()
        nss_before_change = (Path("/etc/passwd").read_bytes(), Path("/etc/group").read_bytes())

        if scenario == "socket-appears":
            listener.bind(str(socket_path))
            bound = True
            os.chown(socket_path, 0, identifiers[0])
            socket_path.chmod(0o660)
        elif scenario == "socket-gid-changes":
            os.chown(socket_path, 0, identifiers[1])
        assert (Path("/etc/passwd").read_bytes(), Path("/etc/group").read_bytes()) == nss_before_change

        expected_group = group_names[1] if scenario == "socket-gid-changes" else group_names[0]
        assert account.username not in grp.getgrnam(expected_group).gr_mem
        reused = WorkspaceProvisioner(**options).resolve(identity)
        assert reused == account
        assert account.username in grp.getgrnam(expected_group).gr_mem, (
            "A ready return must retain the existing socket-group preparation behavior"
        )
        if scenario == "transient-membership-failure":
            assert len(membership_attempts) == 2, "The recovered external command must be retried"
        assert registry.read_bytes() == original_registry
        assert marker.read_text(encoding="utf-8") == "same workspace and data"
        current_marker = marker.stat()
        for field in ("st_ino", "st_uid", "st_gid", "st_mode"):
            assert getattr(current_marker, field) == getattr(original_marker, field)
    finally:
        listener.close()
        if bound:
            socket_path.unlink(missing_ok=True)


@pytest.fixture
def optional_docker_socket(workspace):
    """Own real sockets/groups only inside the existing disposable-container gate."""
    path = Path("/var/run/docker.sock")
    assert not os.path.lexists(path), "This disposable test must own its socket path"
    occupied = {group.gr_gid for group in grp.getgrall()}
    identifiers = [number for number in range(25000, 60000) if number not in occupied][:3]
    assert len(identifiers) == 3
    suffix = uuid.uuid4().hex[:12]
    groups = [(f"retry_{suffix}_{index}", gid) for index, gid in enumerate(identifiers[:2])]
    for name, gid in groups:
        run_account_tool("groupadd", "-g", str(gid), name)
    listeners, paths = [], {path}

    def bind(target, gid):
        assert not os.path.lexists(target)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listeners.append(listener)
        listener.bind(str(target))
        paths.add(target)
        os.chown(target, 0, gid)
        target.chmod(0o660)

    try:
        yield path, groups, identifiers[2], bind
    finally:
        for listener in listeners:
            listener.close()
        for target in paths:
            target.unlink(missing_ok=True)


def socket_workspace_snapshot(account, options):
    """Record public state and user data across optional maintenance attempts."""
    registry = Path(options["state_dir"]) / "registry.json"
    marker = Path(account.home) / "keep-during-docker-retry.txt"
    marker.write_text("same workspace through optional maintenance", encoding="utf-8")
    os.chown(marker, account.uid, account.gid)
    marker.chmod(0o640)
    return registry, registry.read_bytes(), marker, marker.read_bytes(), marker.stat()


def assert_socket_workspace_preserved(snapshot):
    registry, registry_bytes, marker, marker_bytes, marker_info = snapshot
    assert registry.read_bytes() == registry_bytes
    assert marker.read_bytes() == marker_bytes
    for field in ("st_ino", "st_uid", "st_gid", "st_mode"):
        assert getattr(marker.stat(), field) == getattr(marker_info, field)


def assert_completed_docker_warm_path(options, identity, account, monkeypatch):
    import performance_probe

    samples = []
    with monkeypatch.context() as observer:
        observer.setattr(performance_probe, "REGISTRY", Path(options["state_dir"]) / "registry.json")
        with performance_probe.external_work(user_isolation) as calls:
            for _ in range(100):
                started = time.perf_counter()
                assert WorkspaceProvisioner(**options).resolve(identity) == account
                samples.append(time.perf_counter() - started)
    report = {"samples": len(samples), **percentiles(samples), "external_calls": dict(calls)}
    print("DOCKER_WARMED_RESOLUTION " + json.dumps(report, sort_keys=True))
    assert not any(calls.values()), report


def test_optional_docker_retries_each_failure_then_resumes_zero_work_cache(
    workspace, optional_docker_socket, monkeypatch,
):
    provisioner, options, identity = workspace
    path, groups, _, bind = optional_docker_socket
    name, gid = groups[0]
    bind(path, gid)
    real_command = user_isolation._run_privileged
    attempts = []

    def fail_three_times(command):
        if command[:3] == ["usermod", "-aG", name]:
            attempts.append(tuple(command))
            if len(attempts) <= 3:
                raise subprocess.CalledProcessError(1, command, stderr=b"temporary group maintenance failure")
        return real_command(command)

    monkeypatch.setattr(user_isolation, "_run_privileged", fail_three_times)
    account = provisioner.resolve(identity)
    assert len(attempts) == 1
    snapshot = socket_workspace_snapshot(account, options)
    for expected in (2, 3):
        assert WorkspaceProvisioner(**options).resolve(identity) == account
        assert len(attempts) == expected, "Every request must retry unfinished optional maintenance"
        assert account.username not in grp.getgrnam(name).gr_mem
        assert_socket_workspace_preserved(snapshot)
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    assert len(attempts) == 4
    assert account.username in grp.getgrnam(name).gr_mem
    assert_completed_docker_warm_path(options, identity, account, monkeypatch)
    assert len(attempts) == 4
    assert_socket_workspace_preserved(snapshot)


def test_optional_docker_failure_then_socket_removal_and_reappearance(
    workspace, optional_docker_socket, monkeypatch,
):
    provisioner, options, identity = workspace
    path, groups, _, bind = optional_docker_socket
    bind(path, groups[0][1])
    real_command = user_isolation._run_privileged
    attempts = []

    def fail_first_group(command):
        if command[:2] == ["usermod", "-aG"] and command[2] in {name for name, _ in groups}:
            attempts.append(command[2])
            if command[2] == groups[0][0]:
                raise subprocess.CalledProcessError(1, command, stderr=b"unavailable old socket group")
        return real_command(command)

    monkeypatch.setattr(user_isolation, "_run_privileged", fail_first_group)
    account = provisioner.resolve(identity)
    assert attempts == [groups[0][0]]
    snapshot = socket_workspace_snapshot(account, options)
    path.unlink()
    for _ in range(2):
        assert WorkspaceProvisioner(**options).resolve(identity) == account
    assert attempts == [groups[0][0]], "An absent optional socket must not trigger group updates"
    nss_before = Path("/etc/passwd").read_bytes(), Path("/etc/group").read_bytes()
    bind(path, groups[1][1])
    assert (Path("/etc/passwd").read_bytes(), Path("/etc/group").read_bytes()) == nss_before
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    assert attempts == [groups[0][0], groups[1][0]]
    assert account.username in grp.getgrnam(groups[1][0]).gr_mem
    assert_socket_workspace_preserved(snapshot)


@pytest.mark.parametrize("link", [False, True], ids=["direct-socket", "symlink-target"])
def test_optional_docker_atomic_socket_replacement_keeps_existing_memberships(
    workspace, optional_docker_socket, link,
):
    provisioner, options, identity = workspace
    path, groups, _, bind = optional_docker_socket
    target = path.with_name("req001-docker-target-" + uuid.uuid4().hex[:12]) if link else path
    bind(target, groups[0][1])
    if link:
        path.symlink_to(target)
    account = provisioner.resolve(identity)
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    assert account.username in grp.getgrnam(groups[0][0]).gr_mem
    snapshot = socket_workspace_snapshot(account, options)
    nss_before = Path("/etc/passwd").read_bytes(), Path("/etc/group").read_bytes()
    link_before = path.lstat()
    replacement = target.with_name("req001-docker-replacement-" + uuid.uuid4().hex[:12])
    bind(replacement, groups[1][1])
    os.replace(replacement, target)
    if link:
        assert path.lstat().st_ino == link_before.st_ino, "Only the followed target changed"
    assert (Path("/etc/passwd").read_bytes(), Path("/etc/group").read_bytes()) == nss_before
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    assert all(account.username in grp.getgrnam(name).gr_mem for name, _ in groups)
    assert_socket_workspace_preserved(snapshot)


def test_optional_docker_unknown_gid_retries_when_group_becomes_available(
    workspace, optional_docker_socket,
):
    provisioner, options, identity = workspace
    path, _, unknown_gid, bind = optional_docker_socket
    bind(path, unknown_gid)
    with pytest.raises(KeyError):
        grp.getgrgid(unknown_gid)
    account = provisioner.resolve(identity)
    snapshot = socket_workspace_snapshot(account, options)
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    name = "later_" + uuid.uuid4().hex[:12]
    run_account_tool("groupadd", "-g", str(unknown_gid), name)
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    assert account.username in grp.getgrnam(name).gr_mem
    assert_socket_workspace_preserved(snapshot)


@pytest.mark.parametrize("fault", ["stat-permission", "lstat-io", "group-missing"])
def test_optional_docker_observation_failure_keeps_workspace_usable_and_uncached(
    workspace, optional_docker_socket, monkeypatch, fault,
):
    import performance_probe

    provisioner, options, identity = workspace
    path, groups, _, bind = optional_docker_socket
    bind(path, groups[0][1])
    account = provisioner.resolve(identity)
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    snapshot = socket_workspace_snapshot(account, options)
    observed = []

    with monkeypatch.context() as failures:
        if fault == "group-missing":
            original = grp.getgrgid

            def failing_group(gid):
                if gid == groups[0][1]:
                    observed.append(gid)
                    raise KeyError("optional socket group temporarily unavailable")
                return original(gid)

            failures.setattr(grp, "getgrgid", failing_group)
        else:
            operation = "stat" if fault == "stat-permission" else "lstat"
            original = getattr(os, operation)

            def failing_observation(candidate, *args, **kwargs):
                if isinstance(candidate, (str, bytes, os.PathLike)) and Path(os.fsdecode(candidate)) == path:
                    observed.append(candidate)
                    if fault == "stat-permission":
                        raise PermissionError("optional socket observation temporarily denied")
                    raise OSError("optional socket observation temporarily failed")
                return original(candidate, *args, **kwargs)

            failures.setattr(os, operation, failing_observation)
        failures.setattr(performance_probe, "REGISTRY", Path(options["state_dir"]) / "registry.json")
        for _ in range(2):
            with performance_probe.external_work(user_isolation) as calls:
                assert WorkspaceProvisioner(**options).resolve(identity) == account
            assert calls["full_passwd_scan"] > 0, "Unobservable optional state must not be trusted as cached completion"
    assert observed
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    assert_socket_workspace_preserved(snapshot)


def test_optional_docker_primary_gid_needs_no_supplementary_membership(
    workspace, optional_docker_socket, monkeypatch,
):
    provisioner, options, identity = workspace
    path, _, _, bind = optional_docker_socket
    account = provisioner.resolve(identity)
    assert account.username not in grp.getgrgid(account.gid).gr_mem
    bind(path, account.gid)
    attempts = []
    real_command = user_isolation._run_privileged

    def observe(command):
        if command[:2] == ["usermod", "-aG"] and command[-1] == account.username:
            attempts.append(tuple(command))
        return real_command(command)

    monkeypatch.setattr(user_isolation, "_run_privileged", observe)
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    assert_completed_docker_warm_path(options, identity, account, monkeypatch)
    assert not attempts
    assert account.username not in grp.getgrgid(account.gid).gr_mem


def test_optional_docker_observation_churn_returns_workspace_without_caching(
    workspace, optional_docker_socket, monkeypatch,
):
    """Optional socket changes cannot prevent a core-ready workspace returning."""
    import performance_probe

    provisioner, options, identity = workspace
    path, groups, _, bind = optional_docker_socket
    bind(path, groups[0][1])
    account = provisioner.resolve(identity)
    # Satisfy both existing groups before the observation race. This isolates
    # optional socket churn from changes to core NSS sources or membership.
    run_account_tool("usermod", "-aG", groups[1][0], account.username)
    assert all(account.username in grp.getgrnam(name).gr_mem for name, _ in groups)
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    assert WorkspaceProvisioner(**options).resolve(identity) == account
    snapshot = socket_workspace_snapshot(account, options)
    nss_before = Path("/etc/passwd").read_bytes(), Path("/etc/group").read_bytes()
    real_stat = os.stat
    changed_gids = []

    def changing_socket_stat(candidate, *args, **kwargs):
        if isinstance(candidate, (str, bytes, os.PathLike)) and Path(os.fsdecode(candidate)) == path:
            gid = groups[(len(changed_gids) + 1) % 2][1]
            os.chown(path, 0, gid)
            changed_gids.append(gid)
        return real_stat(candidate, *args, **kwargs)

    with monkeypatch.context() as churn:
        churn.setattr(os, "stat", changing_socket_stat)
        churn.setattr(performance_probe, "REGISTRY", Path(options["state_dir"]) / "registry.json")
        for _ in range(2):
            with performance_probe.external_work(user_isolation) as calls:
                assert WorkspaceProvisioner(**options).resolve(identity) == account
            assert calls["full_passwd_scan"] > 0, "Unstable optional state must not bypass full revalidation"

    assert set(changed_gids) == {gid for _, gid in groups}
    assert (Path("/etc/passwd").read_bytes(), Path("/etc/group").read_bytes()) == nss_before
    assert_socket_workspace_preserved(snapshot)
    # Leave the socket exactly as the last real observation saw it. The first
    # stable request must still establish readiness, rather than reuse a proof
    # published while the optional observations disagreed.
    with monkeypatch.context() as observer:
        observer.setattr(performance_probe, "REGISTRY", Path(options["state_dir"]) / "registry.json")
        with performance_probe.external_work(user_isolation) as calls:
            assert WorkspaceProvisioner(**options).resolve(identity) == account
        assert calls["full_passwd_scan"] > 0, "Churning observations must not publish a ready cache entry"
    assert_completed_docker_warm_path(options, identity, account, monkeypatch)
    assert_socket_workspace_preserved(snapshot)
