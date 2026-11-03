"""Observable workspace progress while another preparation is paused at the OS.

The command wrapper delays a real home-permission operation; it does not replace
the resolver, account allocation, persistent state, or an OS command's result.
Short result deadlines detect lack of progress while the gate remains closed.
Every test releases its gate before bounded worker cleanup.
"""

from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import threading
import time

import pytest

from open_terminal.utils import user_isolation
from open_terminal.utils.identity import WorkspaceProvisionError
from open_terminal.utils.user_isolation import WorkspaceProvisioner
from test_workspace_provisioning import assert_ready_account, workspace


_PROGRESS_TIMEOUT = 5.0
_START_TIMEOUT = 10.0
_GATE_TIMEOUT = 15.0
_CLEANUP_TIMEOUT = 10.0


def registry_users(options):
    path = Path(options["state_dir"]) / "registry.json"
    return json.loads(path.read_text(encoding="utf-8"))["users"]


def assert_saved_ready(options, account):
    record = registry_users(options)[account.user_id]
    assert record["state"] == "ready"
    assert (record["username"], record["home"], record["uid"], record["gid"]) == (
        account.username, account.home, account.uid, account.gid,
    )


def home_permission_gate(real_command, options, identity, entered, release, calls):
    """Delay only the selected binding's actual chmod, identified by saved home."""
    def command_boundary(command):
        if command and Path(command[0]).name == "chmod" and "2770" in command:
            record = registry_users(options).get(identity)
            if record is not None and command[-1] == record["home"]:
                calls.append(command[-1])
                entered.set()
                if not release.wait(timeout=_GATE_TIMEOUT):
                    raise TimeoutError("Test gate was not released")
        return real_command(command)

    return command_boundary


@contextmanager
def thread_calls(release):
    """Run calls with futures; always release the OS gate before joining workers."""
    workers = []

    def start(function, *, started=None):
        future = Future()

        def run():
            if not future.set_running_or_notify_cancel():
                return
            if started is not None:
                started.set()
            try:
                future.set_result(function())
            except BaseException as exc:
                future.set_exception(exc)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        workers.append(worker)
        return future

    try:
        yield start
    finally:
        release.set()
        deadline = time.monotonic() + _CLEANUP_TIMEOUT
        for worker in workers:
            worker.join(timeout=max(0.0, deadline - time.monotonic()))
        assert not any(worker.is_alive() for worker in workers), "Resolver threads did not finish after gate release"


def finish_while_other_user_is_paused(future, entered, release):
    assert entered.is_set() and not release.is_set()
    try:
        result = future.result(timeout=_PROGRESS_TIMEOUT)
    except FutureTimeoutError:
        pytest.fail("Independent user did not finish while another user's OS preparation was paused")
    assert not release.is_set(), "Progress must occur before the other preparation is released"
    return result


def test_ready_user_returns_while_another_user_is_paused(workspace, monkeypatch):
    provisioner, options, identity = workspace
    ready_id, slow_id = identity + "-ready", identity + "-slow"
    ready = provisioner.resolve(ready_id)
    assert_ready_account(ready, ready_id, options["home_root"])
    assert_saved_ready(options, ready)
    entered, release = threading.Event(), threading.Event()
    calls = []
    monkeypatch.setattr(
        user_isolation, "_run_privileged",
        home_permission_gate(user_isolation._run_privileged, options, slow_id, entered, release, calls),
    )
    with thread_calls(release) as start:
        slow_future = start(lambda: WorkspaceProvisioner(**options).resolve(slow_id))
        assert entered.wait(timeout=_START_TIMEOUT), "Slow user's home-permission operation was not reached"
        ready_future = start(lambda: WorkspaceProvisioner(**options).resolve(ready_id))
        result = finish_while_other_user_is_paused(ready_future, entered, release)
        assert result == ready
        assert registry_users(options)[slow_id]["state"] != "ready"
        assert not slow_future.done()
        release.set()
        slow = slow_future.result(timeout=_CLEANUP_TIMEOUT)
    assert_ready_account(slow, slow_id, options["home_root"])
    assert_saved_ready(options, ready)
    assert_saved_ready(options, slow)
    assert calls


def test_distinct_new_users_progress_and_merge_their_ready_records(workspace, monkeypatch):
    _, options, identity = workspace
    first_id, second_id = identity + "-first", identity + "-second"
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(
        user_isolation, "_run_privileged",
        home_permission_gate(user_isolation._run_privileged, options, first_id, entered, release, []),
    )
    with thread_calls(release) as start:
        first_future = start(lambda: WorkspaceProvisioner(**options).resolve(first_id))
        assert entered.wait(timeout=_START_TIMEOUT), "First user's home-permission operation was not reached"
        second_future = start(lambda: WorkspaceProvisioner(**options).resolve(second_id))
        second = finish_while_other_user_is_paused(second_future, entered, release)
        assert_ready_account(second, second_id, options["home_root"])
        during_pause = registry_users(options)
        assert during_pause[first_id]["state"] != "ready"
        assert during_pause[second_id]["state"] == "ready"
        second_record = dict(during_pause[second_id])
        assert not first_future.done()
        release.set()
        first = first_future.result(timeout=_CLEANUP_TIMEOUT)
    assert_ready_account(first, first_id, options["home_root"])
    assert set(registry_users(options)) == {first_id, second_id}
    assert registry_users(options)[second_id] == second_record
    assert_saved_ready(options, first)
    assert_saved_ready(options, second)
    for field in ("username", "home", "uid", "gid"):
        assert getattr(first, field) != getattr(second, field)


def test_same_user_waits_for_ready_without_preparing_a_second_binding(workspace, monkeypatch):
    _, options, identity = workspace
    entered, release = threading.Event(), threading.Event()
    second_started = threading.Event()
    calls = []
    monkeypatch.setattr(
        user_isolation, "_run_privileged",
        home_permission_gate(user_isolation._run_privileged, options, identity, entered, release, calls),
    )
    with thread_calls(release) as start:
        first_future = start(lambda: WorkspaceProvisioner(**options).resolve(identity))
        assert entered.wait(timeout=_START_TIMEOUT), "Initial home-permission operation was not reached"
        pending_record = dict(registry_users(options)[identity])
        assert pending_record["state"] != "ready"
        second_future = start(
            lambda: WorkspaceProvisioner(**options).resolve(identity), started=second_started,
        )
        assert second_started.wait(timeout=_START_TIMEOUT)
        with pytest.raises(FutureTimeoutError):
            second_future.result(timeout=0.25)
        assert len(calls) == 1, "The second caller began preparing the same incomplete binding"
        assert registry_users(options)[identity] == pending_record
        assert not first_future.done()
        release.set()
        first = first_future.result(timeout=_CLEANUP_TIMEOUT)
        second = second_future.result(timeout=_CLEANUP_TIMEOUT)
    assert first == second
    assert_ready_account(first, identity, options["home_root"])
    assert set(registry_users(options)) == {identity}
    assert_saved_ready(options, first)


def resolve_in_process(options, identity, connection, entered=None, release=None):
    """Wrap the external command boundary inside a fresh spawned interpreter."""
    real_command = user_isolation._run_privileged
    if entered is not None:
        user_isolation._run_privileged = home_permission_gate(
            real_command, options, identity, entered, release, [],
        )
    try:
        connection.send(("started", None))
        account = WorkspaceProvisioner(**options).resolve(identity)
        connection.send(("ready", account))
    except BaseException as exc:
        connection.send(("error", (type(exc).__name__, str(exc))))
    finally:
        user_isolation._run_privileged = real_command
        connection.close()


@contextmanager
def process_calls(context, release):
    """Release first, then join or terminate every spawned test worker boundedly."""
    workers, connections = [], []

    def start(options, identity, *, entered=None):
        parent, child = context.Pipe(duplex=False)
        connections.append(parent)
        worker = context.Process(
            target=resolve_in_process,
            args=(options, identity, child, entered, release if entered is not None else None),
            daemon=True,
        )
        try:
            worker.start()
        finally:
            child.close()
        workers.append(worker)
        return parent

    try:
        yield start
    finally:
        release.set()
        deadline = time.monotonic() + _CLEANUP_TIMEOUT
        for worker in workers:
            worker.join(timeout=max(0.0, deadline - time.monotonic()))
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=2)
        for connection in connections:
            connection.close()
        assert not any(worker.is_alive() for worker in workers), "A test process could not be reaped"


def receive_stage(connection, expected, *, timeout):
    assert connection.poll(timeout), f"Child did not report {expected} before the bounded deadline"
    stage, result = connection.recv()
    assert stage == expected, f"Expected {expected}, received {stage}: {result!r}"
    return result


@pytest.mark.parametrize("already_ready", [True, False], ids=["ready-user", "new-user"])
def test_processes_allow_independent_progress_and_preserve_both_records(workspace, already_ready):
    provisioner, options, identity = workspace
    slow_id, independent_id = identity + "-slow-process", identity + "-independent-process"
    initial = provisioner.resolve(independent_id) if already_ready else None
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    with process_calls(context, release) as start:
        slow_pipe = start(options, slow_id, entered=entered)
        receive_stage(slow_pipe, "started", timeout=_START_TIMEOUT)
        assert entered.wait(timeout=_START_TIMEOUT), "Child did not reach the home-permission gate"
        independent_pipe = start(options, independent_id)
        receive_stage(independent_pipe, "started", timeout=_START_TIMEOUT)
        independent = receive_stage(independent_pipe, "ready", timeout=_PROGRESS_TIMEOUT)
        assert entered.is_set() and not release.is_set()
        assert not slow_pipe.poll(0), "Paused preparation returned before its home was ready"
        assert_ready_account(independent, independent_id, options["home_root"])
        if initial is not None:
            assert independent == initial
        during_pause = registry_users(options)
        assert during_pause[slow_id]["state"] != "ready"
        assert during_pause[independent_id]["state"] == "ready"
        independent_record = dict(during_pause[independent_id])
        release.set()
        slow = receive_stage(slow_pipe, "ready", timeout=_CLEANUP_TIMEOUT)
    assert_ready_account(slow, slow_id, options["home_root"])
    assert set(registry_users(options)) == {slow_id, independent_id}
    assert registry_users(options)[independent_id] == independent_record
    assert_saved_ready(options, independent)
    assert_saved_ready(options, slow)
    for field in ("username", "home", "uid", "gid"):
        assert getattr(independent, field) != getattr(slow, field)


def test_user_and_global_file_lock_waits_share_one_thirty_second_budget(workspace):
    """Ten seconds spent at the user lock leave only twenty for the global lock."""
    provisioner, options, identity = workspace
    state_dir = Path(options["state_dir"])
    state_dir.mkdir(mode=0o700)
    lock_dir = state_dir / "user-locks"
    lock_dir.mkdir(mode=0o700)
    user_path = lock_dir / (hashlib.sha256(identity.encode()).hexdigest() + ".lock")
    global_path = state_dir / "provision.lock"
    user_fd = os.open(user_path, os.O_CREAT | os.O_RDWR, 0o600)
    global_fd = os.open(global_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(user_fd, fcntl.LOCK_EX)
    fcntl.flock(global_fd, fcntl.LOCK_EX)
    released_user, watchdog_fired = threading.Event(), threading.Event()

    def release_user():
        fcntl.flock(user_fd, fcntl.LOCK_UN)
        released_user.set()

    def release_global_watchdog():
        watchdog_fired.set()
        fcntl.flock(global_fd, fcntl.LOCK_UN)

    user_timer = threading.Timer(10.0, release_user)
    global_timer = threading.Timer(35.0, release_global_watchdog)
    try:
        started = time.monotonic()
        user_timer.start()
        global_timer.start()
        with pytest.raises(WorkspaceProvisionError) as failure:
            provisioner.resolve(identity)
        elapsed = time.monotonic() - started
        assert failure.value.code == "workspace_busy"
        assert released_user.is_set() and not watchdog_fired.is_set()
        assert 29.0 <= elapsed < 34.5, elapsed
        assert not (state_dir / "registry.json").exists()
    finally:
        user_timer.cancel()
        global_timer.cancel()
        user_timer.join(timeout=2)
        global_timer.join(timeout=2)
        fcntl.flock(user_fd, fcntl.LOCK_UN)
        fcntl.flock(global_fd, fcntl.LOCK_UN)
        os.close(user_fd)
        os.close(global_fd)


def resolve_after_forked_busy_parent(options, identity, connection, command):
    # Do not inherit the parent's testing gate, only its real lock state.
    user_isolation._run_privileged = command
    try:
        connection.send(("started", None))
        connection.send(("ready", WorkspaceProvisioner(**options).resolve(identity)))
    except BaseException as exc:
        connection.send(("error", (type(exc).__name__, str(exc))))
    finally:
        connection.close()


def test_fork_rebuilds_memory_locks_held_by_another_thread(workspace, monkeypatch):
    """The child waits on the shared file lock, then progresses after release."""
    _, options, identity = workspace
    entered, release = threading.Event(), threading.Event()
    real_command = user_isolation._run_privileged
    monkeypatch.setattr(
        user_isolation, "_run_privileged",
        home_permission_gate(real_command, options, identity, entered, release, []),
    )
    child = None
    receiving = sending = None
    try:
        with thread_calls(release) as start:
            parent_result = start(lambda: WorkspaceProvisioner(**options).resolve(identity))
            assert entered.wait(_START_TIMEOUT)
            context = multiprocessing.get_context("fork")
            receiving, sending = context.Pipe(duplex=False)
            child = context.Process(
                target=resolve_after_forked_busy_parent,
                args=(options, identity, sending, real_command),
            )
            child.start()
            sending.close()
            receive_stage(receiving, "started", timeout=_START_TIMEOUT)
            assert not receiving.poll(0.25), "A child cannot bypass its parent's live file lock"
            release.set()
            parent_account = parent_result.result(_CLEANUP_TIMEOUT)
            child_account = receive_stage(receiving, "ready", timeout=_PROGRESS_TIMEOUT)
            assert child_account == parent_account
            assert_saved_ready(options, child_account)
            child.join(_CLEANUP_TIMEOUT)
            assert child.exitcode == 0
    finally:
        release.set()
        if child is not None and child.is_alive():
            child.terminate()
            child.join(5)
        if child is not None and child.is_alive():
            child.kill()
            child.join(5)
        if receiving is not None:
            receiving.close()
        if sending is not None:
            sending.close()
