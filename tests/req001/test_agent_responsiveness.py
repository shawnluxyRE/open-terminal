"""Slow workspace preparation must leave the serving event loop responsive.

All app requests and the real PTY share one TestClient portal/event loop. Only
the external useradd command is delayed; identity resolution and provisioning
run normally. An independent watchdog releases the command even if the app's
event loop is blocked, so a regression fails an assertion instead of deadlocking.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import fcntl
from functools import partial
import grp
import hashlib
import json
from pathlib import Path
import pwd
import stat
import subprocess
import threading
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

from open_terminal.utils.identity import WorkspaceProvisionError

from test_workspace_provisioning import workspace


KEY = "req001-test-key-not-a-secret"


def headers(identity):
    return {
        "Authorization": f"Bearer {KEY}",
        "X-User-Id": identity,
        "X-Session-Id": "responsive-chat",
    }


@pytest.fixture
def runtime(workspace, monkeypatch):
    from open_terminal import dependencies, env, main
    from open_terminal.utils import user_isolation

    _, options, identity = workspace
    # Inject constructor configuration, never a resolved account or filesystem.
    monkeypatch.setattr(user_isolation, "WorkspaceProvisioner", partial(
        user_isolation.WorkspaceProvisioner,
        state_dir=options["state_dir"], home_root=options["home_root"],
    ))
    monkeypatch.setattr(env, "USER_PREFIX", options["username_prefix"])
    monkeypatch.setattr(main, "_processes", {})
    monkeypatch.setattr(main, "_terminal_sessions", {})
    monkeypatch.setattr(main, "_session_cwds", {})
    monkeypatch.setattr(main, "detect_listening_ports", lambda: [])
    log_dir = Path(options["home_root"]).parent / "logs"
    (log_dir / "processes").mkdir(parents=True)
    monkeypatch.setattr(main, "LOG_DIR", str(log_dir))
    home_root = Path(options["home_root"]).resolve()
    temporary_root = Path("/tmp").resolve()
    search_modes = {}
    try:
        # Real sudo commands must be able to traverse pytest's root-owned 700
        # ancestors before entering their own home. Change only this temporary
        # path inside the disposable container, and restore its original modes.
        assert temporary_root in home_root.parents
        for directory in (home_root, *home_root.parents):
            if directory == temporary_root:
                break
            search_modes[directory] = stat.S_IMODE(directory.stat().st_mode)
            directory.chmod(search_modes[directory] | 0o011)
        with TestClient(main.app) as client:
            yield client, dependencies, user_isolation, identity, options
    finally:
        # Lifespan closes the test's PTYs/processes before their accounts go away.
        # Match the exact isolated home root, not a broad username prefix.
        try:
            accounts = [entry for entry in pwd.getpwall() if Path(entry.pw_dir).parent == home_root]
            for account in accounts:
                subprocess.run(["userdel", account.pw_name], check=True, capture_output=True, timeout=15)
                try:
                    grp.getgrnam(account.pw_name)
                except KeyError:
                    continue
                subprocess.run(["groupdel", account.pw_name], check=True, capture_output=True, timeout=15)
        finally:
            for directory, mode in search_modes.items():
                directory.chmod(mode)


class CommandGate:
    """Delay one real useradd, with release independent of the app event loop."""

    def __init__(self, real_run):
        self.real_run = real_run
        self.entered = threading.Event()
        self.released = threading.Event()
        self.watchdog_fired = threading.Event()
        self.timer = None

    def __call__(self, command):
        if Path(command[0]).name == "useradd" and not self.entered.is_set():
            self.timer = threading.Timer(5.0, self._watchdog_release)
            self.timer.start()
            self.entered.set()
            assert self.released.wait(10), "Preparation watchdog failed to release the external command"
        return self.real_run(command)

    def _watchdog_release(self):
        self.watchdog_fired.set()
        self.released.set()

    def release(self):
        self.released.set()
        if self.timer is not None:
            self.timer.cancel()
            self.timer.join(timeout=5)
            assert not self.timer.is_alive()


def receive_marker(ws, marker, disconnected):
    # The extra close deadline also bounds an unrelated PTY startup/I/O failure.
    # It cannot make the responsiveness assertion pass: a close has no marker.
    deadline = threading.Timer(10, ws.close)
    deadline.start()
    try:
        output = bytearray()
        while marker not in output:
            output.extend(ws.receive_bytes())
            assert len(output) < 65536, "PTY did not produce the requested marker"
    except WebSocketDisconnect:
        disconnected.set()
        raise
    finally:
        deadline.cancel()
        deadline.join(timeout=5)


def send_marker(ws, marker, disconnected):
    # The echoed command contains a format placeholder rather than the complete
    # expected output, so the assertion proves the shell processed fresh input.
    ws.send_bytes(b"printf '\\nASYNC_%s\\n' " + marker + b"\n")
    receive_marker(ws, b"ASYNC_" + marker, disconnected)


def close_websocket(ws, disconnected):
    if disconnected.is_set():
        return
    ws.close(code=1000)
    while True:
        message = ws.receive()
        if message["type"] == "websocket.close":
            disconnected.set()
            assert message["code"] == 1000
            return
        assert message["type"] == "websocket.send"


@pytest.mark.parametrize(
    "method,path,payload,expected_status",
    [
        ("POST", "/execute?wait=5", {"command": "true"}, 200),
        ("POST", "/api/terminals", None, 200),
        ("GET", "/ports", None, 200),
        ("GET", "/proxy/18080/", None, 404),
    ],
    ids=["execute", "terminal-create", "ports", "proxy"],
)
def test_preparing_request_does_not_stall_health_tasks_or_connected_pty(
    runtime, monkeypatch, method, path, payload, expected_status,
):
    client, _, user_isolation, identity, _ = runtime
    owner, newcomer = identity + "-owner", identity + "-new"
    created = client.post("/api/terminals", headers=headers(owner))
    assert created.status_code == 200, created.text
    endpoint = "/api/terminals/" + created.json()["id"]
    with client.websocket_connect(endpoint, headers=headers(owner)) as ws:
        disconnected = threading.Event()
        ws.send_json({"type": "auth", "token": KEY})
        send_marker(ws, ("ready_" + uuid.uuid4().hex).encode(), disconnected)
        gate = CommandGate(user_isolation._run_privileged)
        monkeypatch.setattr(user_isolation, "_run_privileged", gate)
        observations = {}
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(
                    client.request, method, path, headers=headers(newcomer), json=payload,
                )
                try:
                    assert gate.entered.wait(10), "Request never reached real account preparation"
                    health = client.get("/health")
                    observations["health"] = health.status_code == 200 and not gate.released.is_set()
                    tasks = client.get("/execute", headers=headers(owner))
                    observations["task_list"] = tasks.status_code == 200 and not gate.released.is_set()
                    send_marker(ws, ("live_" + uuid.uuid4().hex).encode(), disconnected)
                    observations["pty_roundtrip"] = not gate.released.is_set()
                    observations["preparation_still_waiting"] = not pending.done() and not gate.released.is_set()
                    observations["watchdog_not_needed"] = not gate.watchdog_fired.is_set()
                finally:
                    gate.release()
                response = pending.result(timeout=20)
                assert response.status_code == expected_status, response.text
                if path.startswith("/execute"):
                    assert response.json()["exit_code"] == 0, response.text
                elif path == "/api/terminals":
                    cleanup = client.delete("/api/terminals/" + response.json()["id"], headers=headers(newcomer))
                    assert cleanup.status_code == 200, cleanup.text
                elif path == "/ports":
                    assert response.json() == {"ports": []}
        finally:
            gate.release()
            close_websocket(ws, disconnected)
        assert all(observations.values()), f"Serving loop stalled until preparation was released: {observations}"
    assert client.get(endpoint, headers=headers(owner)).status_code == 404


def test_async_filesystem_adapter_returns_the_real_prepared_workspace(runtime):
    _, dependencies, _, identity, options = runtime
    request = Request({
        "type": "http",
        "headers": [(b"x-user-id", identity.encode("ascii"))],
    })
    filesystem = asyncio.run(dependencies.get_filesystem_async(request))
    account = pwd.getpwnam(filesystem.username)
    assert filesystem.home == account.pw_dir
    assert Path(filesystem.home).parent == Path(options["home_root"])


def test_async_filesystem_adapter_preserves_identity_rejection_without_preparation(runtime):
    _, dependencies, _, _, options = runtime
    before = list(Path(options["home_root"]).iterdir()), list(pwd.getpwall())
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(dependencies.get_filesystem_async(Request({"type": "http", "headers": []})))
    assert rejected.value.status_code == 400
    assert rejected.value.detail == {"code": "invalid_user_identity", "message": "Invalid user identity"}
    assert (list(Path(options["home_root"]).iterdir()), list(pwd.getpwall())) == before


async def wait_for_thread_event(event):
    """Observe an external worker without consuming the executor under test."""
    async with asyncio.timeout(10):
        while not event.is_set():
            await asyncio.sleep(0.001)


def test_cancelled_preparation_keeps_worker_ownership_until_same_user_is_ready(runtime, monkeypatch):
    _, dependencies, user_isolation, identity, options = runtime
    gate = CommandGate(user_isolation._run_privileged)
    useradd_calls = []

    def gated_command(command):
        if Path(command[0]).name == "useradd":
            useradd_calls.append(tuple(command))
        return gate(command)

    monkeypatch.setattr(user_isolation, "_run_privileged", gated_command)
    request = Request({"type": "http", "headers": [(b"x-user-id", identity.encode("ascii"))]})

    async def scenario():
        first = asyncio.create_task(dependencies.get_filesystem_async(request))
        second = None
        try:
            await wait_for_thread_event(gate.entered)
            first.cancel()
            second = asyncio.create_task(dependencies.get_filesystem_async(request))
            # Deliver cancellation and start the second adapter while useradd
            # is externally gated. Neither may publish a half-prepared result.
            await asyncio.sleep(0)
            assert not first.done(), "Cancellation must await the still-running worker's cleanup"
            assert not second.done()
            assert not gate.released.is_set() and not gate.watchdog_fired.is_set()
            assert len(useradd_calls) == 1
        finally:
            gate.release()
            results = await asyncio.gather(
                first, *([second] if second is not None else []), return_exceptions=True,
            )
        assert isinstance(results[0], asyncio.CancelledError)
        assert len(results) == 2 and not isinstance(results[1], BaseException)
        return results[1]

    filesystem = asyncio.run(scenario())
    assert len(useradd_calls) == 1
    account = pwd.getpwnam(filesystem.username)
    assert filesystem.home == account.pw_dir
    saved = json.loads((Path(options["state_dir"]) / "registry.json").read_text())
    assert saved["users"][identity]["state"] == "ready"
    assert saved["users"][identity]["username"] == filesystem.username


def test_async_preparation_respects_the_existing_executor_worker_limit(runtime):
    _, dependencies, _, identity, _ = runtime

    class ObservedExecutor(ThreadPoolExecutor):
        """Gate the external execution facility, keeping real preparation intact."""

        def __init__(self):
            super().__init__(max_workers=2)
            self.guard = threading.Lock()
            self.release_workers = threading.Event()
            self.two_workers_started = threading.Event()
            self.all_submitted = threading.Event()
            self.submitted = self.active = self.maximum_active = 0
            self.worker_ids = set()

        def submit(self, function, /, *args, **kwargs):
            with self.guard:
                self.submitted += 1
                if self.submitted == 6:
                    self.all_submitted.set()

            def observed():
                with self.guard:
                    self.active += 1
                    self.maximum_active = max(self.maximum_active, self.active)
                    self.worker_ids.add(threading.get_ident())
                    if self.active == 2:
                        self.two_workers_started.set()
                try:
                    assert self.release_workers.wait(10), "Executor gate was not released"
                    return function(*args, **kwargs)
                finally:
                    with self.guard:
                        self.active -= 1

            return super().submit(observed)

    async def scenario():
        executor = ObservedExecutor()
        # The loop owns this configured, bounded facility and closes it at the
        # end of asyncio.run; no replacement account or filesystem is injected.
        asyncio.get_running_loop().set_default_executor(executor)
        pending = [asyncio.create_task(dependencies.get_filesystem_async(Request({
            "type": "http", "headers": [(b"x-user-id", f"{identity}-{index}".encode("ascii"))],
        }))) for index in range(6)]
        try:
            await wait_for_thread_event(executor.all_submitted)
            await wait_for_thread_event(executor.two_workers_started)
            assert executor.maximum_active == 2
            assert all(not task.done() for task in pending)
        finally:
            executor.release_workers.set()
            results = await asyncio.gather(*pending, return_exceptions=True)
        assert all(not isinstance(result, BaseException) for result in results)
        assert executor.maximum_active == 2 and len(executor.worker_ids) == 2
        return results

    filesystems = asyncio.run(scenario())
    assert len({filesystem.username for filesystem in filesystems}) == 6
    for filesystem in filesystems:
        assert pwd.getpwnam(filesystem.username).pw_dir == filesystem.home


@pytest.mark.parametrize("code,status", [("identity_conflict", 409), ("private-unknown-code", 503)])
def test_async_filesystem_adapter_preserves_safe_preparation_errors(runtime, monkeypatch, code, status):
    _, dependencies, _, identity, options = runtime
    before = list(Path(options["home_root"]).iterdir()), list(pwd.getpwall())

    def rejected(_identity):
        raise WorkspaceProvisionError(code, "private account details " + KEY)

    monkeypatch.setattr(dependencies, "resolve_user", rejected)
    with pytest.raises(HTTPException) as failure:
        asyncio.run(dependencies.get_filesystem_async(Request({
            "type": "http", "headers": [(b"x-user-id", identity.encode("ascii"))],
        })))
    assert failure.value.status_code == status
    assert failure.value.detail == {
        "code": code if code == "identity_conflict" else "workspace_unavailable",
        "message": "Workspace preparation failed",
    }
    assert (list(Path(options["home_root"]).iterdir()), list(pwd.getpwall())) == before


def test_async_filesystem_adapter_preserves_explicit_single_user_mode(runtime, monkeypatch):
    _, dependencies, _, _, options = runtime
    before = list(Path(options["home_root"]).iterdir()), list(pwd.getpwall())

    def forbidden(_identity):
        pytest.fail("Single-user mode must not prepare a workspace")

    monkeypatch.setattr(dependencies, "MULTI_USER", False)
    monkeypatch.setattr(dependencies, "resolve_user", forbidden)
    filesystem = asyncio.run(dependencies.get_filesystem_async(Request({"type": "http", "headers": []})))
    assert filesystem.username is None
    assert (list(Path(options["home_root"]).iterdir()), list(pwd.getpwall())) == before


def test_slow_terminal_preparation_checks_capacity_before_allocating_a_pty(runtime, monkeypatch):
    """A request awaiting preparation must not reserve a PTY or stale capacity."""
    from open_terminal import main

    client, _, user_isolation, identity, _ = runtime
    owner, newcomer = identity + "-ready", identity + "-pending"
    warmed = client.get("/files/cwd", headers=headers(owner))
    assert warmed.status_code == 200, warmed.text
    monkeypatch.setattr(main, "MAX_TERMINAL_SESSIONS", 1)
    real_openpty = main.pty.openpty
    opened = []

    def observed_openpty():
        descriptors = real_openpty()
        opened.append(descriptors)
        return descriptors

    monkeypatch.setattr(main.pty, "openpty", observed_openpty)
    gate = CommandGate(user_isolation._run_privileged)
    monkeypatch.setattr(user_isolation, "_run_privileged", gate)
    owner_response = pending_response = None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, "/api/terminals", headers=headers(newcomer))
            try:
                assert gate.entered.wait(10), "Pending terminal never reached real useradd"
                assert opened == [], "Preparation must complete before allocating PTY descriptors"
                owner_response = client.post("/api/terminals", headers=headers(owner))
                assert owner_response.status_code == 200, owner_response.text
                assert not gate.released.is_set() and not gate.watchdog_fired.is_set()
                assert not pending.done()
                assert len(opened) == 1
            finally:
                gate.release()
                pending_response = pending.result(timeout=20)

        assert pending_response.status_code == 429, pending_response.text
        assert len(opened) == 1, "A preparation that resumes at capacity must not allocate another PTY"
        assert set(main._terminal_sessions) == {owner_response.json()["id"]}
        assert client.get("/api/terminals", headers=headers(newcomer)).json() == []
    finally:
        gate.release()
        for response, user in ((owner_response, owner), (pending_response, newcomer)):
            if response is not None and response.status_code == 200:
                removed = client.delete("/api/terminals/" + response.json()["id"], headers=headers(user))
                assert removed.status_code == 200, removed.text


def test_repeated_cancellation_keeps_real_worker_lock_until_preparation_finishes(runtime, monkeypatch):
    """Cancelling the waiter twice must not unlock its still-running OS worker."""
    _, dependencies, user_isolation, identity, options = runtime
    gate = CommandGate(user_isolation._run_privileged)
    real_getpwuid = pwd.getpwuid
    second_resolve_started = threading.Event()
    observe_second = threading.Event()
    useradd_calls = []

    def gated_command(command):
        if Path(command[0]).name == "useradd":
            useradd_calls.append(tuple(command))
        return gate(command)

    def observe_server_lookup(uid):
        # The first resolver is paused in useradd when observation is enabled.
        # This real NSS lookup proves the next worker entered resolve itself,
        # rather than merely having an asyncio task queued for future execution.
        result = real_getpwuid(uid)
        if observe_second.is_set():
            second_resolve_started.set()
        return result

    monkeypatch.setattr(user_isolation, "_run_privileged", gated_command)
    monkeypatch.setattr(pwd, "getpwuid", observe_server_lookup)
    request = Request({"type": "http", "headers": [(b"x-user-id", identity.encode("ascii"))]})
    registry = Path(options["state_dir"]) / "registry.json"
    lock_path = Path(options["state_dir"]) / "user-locks" / (hashlib.sha256(identity.encode()).hexdigest() + ".lock")

    async def scenario():
        first = asyncio.create_task(dependencies.get_filesystem_async(request))
        second = None
        try:
            await wait_for_thread_event(gate.entered)
            first.cancel()
            await asyncio.sleep(0)  # Deliver the first cancellation to run_io.
            first.cancel()
            await asyncio.sleep(0)  # Deliver the second cancellation as well.
            assert not gate.released.is_set() and not gate.watchdog_fired.is_set()

            # A separate open description tests the actual file lock. The
            # request coroutine may already be cancelled; its worker must retain
            # this lock until the gated OS operation and preparation finish.
            with lock_path.open("r+b") as independent_lock:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(independent_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            observe_second.set()
            second = asyncio.create_task(dependencies.get_filesystem_async(request))
            await wait_for_thread_event(second_resolve_started)
            done, _ = await asyncio.wait({second}, timeout=0.25)
            assert not done, "A second caller must wait for the cancelled request's real worker"
            assert len(useradd_calls) == 1
            assert json.loads(registry.read_text())["users"][identity]["state"] != "ready"
            assert not gate.released.is_set() and not gate.watchdog_fired.is_set()
        finally:
            gate.release()
            results = await asyncio.gather(
                first, *([second] if second is not None else []), return_exceptions=True,
            )
        assert isinstance(results[0], asyncio.CancelledError)
        assert len(results) == 2 and not isinstance(results[1], BaseException)
        return results[1]

    filesystem = asyncio.run(scenario())
    assert len(useradd_calls) == 1
    saved = json.loads(registry.read_text())["users"][identity]
    assert saved["state"] == "ready" and saved["username"] == filesystem.username
    assert pwd.getpwnam(filesystem.username).pw_dir == filesystem.home
