"""Compare public resolve_user costs in isolated containers with one runtime.

Read a JSON object from stdin: label, revision, and optional historical source.
Historical user_isolation.py is compiled in memory, not installed or written.
The legacy path is only a latency reference; it lacks current ownership and
durability semantics and must never be used as a deployment fallback.
"""

import builtins
from collections import Counter
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
import grp
import hashlib
from importlib.metadata import version
import io
import json
import math
import os
from pathlib import Path
import platform
import pwd
import stat
import sys
import threading
import time
import types


REGISTRY = Path("/home/.open-terminal/registry.json")
COUNTER_KEYS = (
    "registry_open", "registry_io_open", "registry_os_open", "json_load", "json_loads",
    "registry_directory_fsync", "full_passwd_scan", "full_group_scan", "privileged_command",
)


def percentiles(samples):
    ordered = sorted(samples)
    return {
        "p50_ms": ordered[math.ceil(len(ordered) * .50) - 1] * 1000,
        "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1] * 1000,
    }


@contextmanager
def external_work(module):
    """Observe real external calls while leaving their arguments/results intact."""
    counts = Counter({key: 0 for key in COUNTER_KEYS})
    old_open, old_io, old_os = builtins.open, io.open, os.open
    old_load, old_loads, old_fsync = json.load, json.loads, os.fsync
    old_users, old_groups, old_command = pwd.getpwall, grp.getgrall, module._run_privileged
    directory = REGISTRY.parent.stat() if REGISTRY.exists() else None

    def target(path, kwargs):
        if not isinstance(path, (str, bytes, os.PathLike)):
            return False
        value = Path(os.fsdecode(path))
        if not value.is_absolute() and kwargs.get("dir_fd") is not None:
            value = Path(os.readlink(f"/proc/self/fd/{kwargs['dir_fd']}")) / value
        return value == REGISTRY

    def opening(original, name, path, *args, **kwargs):
        if target(path, kwargs):
            counts[name] += 1
        return original(path, *args, **kwargs)

    def load(*args, **kwargs):
        counts["json_load"] += 1
        return old_load(*args, **kwargs)

    def loads(*args, **kwargs):
        counts["json_loads"] += 1
        return old_loads(*args, **kwargs)

    def sync(fd):
        info = os.fstat(fd)
        if directory is not None and stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == (directory.st_dev, directory.st_ino):
            counts["registry_directory_fsync"] += 1
        return old_fsync(fd)

    def users():
        counts["full_passwd_scan"] += 1
        return old_users()

    def groups():
        counts["full_group_scan"] += 1
        return old_groups()

    def command(arguments):
        counts["privileged_command"] += 1
        return old_command(arguments)

    replacements = [
        (builtins, "open", lambda p, *a, **k: opening(old_open, "registry_open", p, *a, **k)),
        (io, "open", lambda p, *a, **k: opening(old_io, "registry_io_open", p, *a, **k)),
        (os, "open", lambda p, *a, **k: opening(old_os, "registry_os_open", p, *a, **k)),
        (json, "load", load), (json, "loads", loads), (os, "fsync", sync),
        (pwd, "getpwall", users), (grp, "getgrall", groups), (module, "_run_privileged", command),
    ]
    originals = [(owner, name, getattr(owner, name)) for owner, name, _ in replacements]
    try:
        for owner, name, value in replacements:
            setattr(owner, name, value)
        yield counts
    finally:
        for owner, name, value in reversed(originals):
            setattr(owner, name, value)


def start_thread(function, workers):
    future = Future()

    def invoke():
        future.set_running_or_notify_cancel()
        try:
            future.set_result(function())
        except BaseException as error:
            future.set_exception(error)

    worker = threading.Thread(target=invoke, daemon=True)
    workers.append(worker)
    worker.start()
    return future


def slow_other_user(module, owner_id, owner):
    entered, released = threading.Event(), threading.Event()
    old_command = module._run_privileged
    workers = []
    report = {}

    def gate(command):
        if Path(command[0]).name == "useradd" and not entered.is_set():
            entered.set()
            if not released.wait(15):
                raise TimeoutError("Benchmark command gate was not released")
        return old_command(command)

    module._run_privileged = gate
    try:
        slow = start_thread(lambda: module.resolve_user("benchB001-user"), workers)
        assert entered.wait(10), "Slow user did not reach real useradd"
        with external_work(module) as counts:
            started = time.perf_counter()
            independent = start_thread(lambda: module.resolve_user(owner_id), workers)
            try:
                result = independent.result(timeout=5)
                report["owner_finished_before_release"] = not released.is_set()
                report["owner_latency_ms"] = (time.perf_counter() - started) * 1000
                assert result == owner
            except FutureTimeoutError:
                report["owner_finished_before_release"] = False
                report["owner_latency_ms"] = None
            report["external_calls_while_paused"] = dict(counts)
            report["slow_user_still_pending"] = not slow.done()
        released.set()
        slow_account = slow.result(timeout=10)
        assert independent.result(timeout=10) == owner
        assert slow_account[0] != owner[0] and slow_account[1] != owner[1]
        report["gate"] = "real useradd before execution; owner must finish before release"
        report["progress_deadline_seconds"] = 5
        return report
    finally:
        released.set()
        module._run_privileged = old_command
        deadline = time.monotonic() + 10
        for worker in workers:
            worker.join(max(0, deadline - time.monotonic()))
        assert not any(worker.is_alive() for worker in workers), "Benchmark worker failed to finish"


def main():
    assert os.environ.get("REQ001_DISPOSABLE_CONTAINER") == "1"
    assert os.geteuid() == 0 and Path("/.dockerenv").is_file()
    payload = json.load(sys.stdin)
    os.environ.update({
        "OPEN_TERMINAL_MULTI_USER": "true", "OPEN_TERMINAL_USER_PREFIX": "bench_",
        "OPEN_TERMINAL_API_KEY": "req001-test-key-not-a-secret", "USER": "root",
        "OPEN_TERMINAL_ENABLE_NOTEBOOKS": "false",
    })
    source = payload.get("source")
    if source is None:
        from open_terminal.utils import user_isolation as module
        source = Path(module.__file__).read_text(encoding="utf-8")
        source_kind = "installed current test image"
    else:
        module = types.ModuleType("workspace_performance_reference")
        sys.modules[module.__name__] = module
        exec(compile(source, f"git:{payload['revision']}:user_isolation.py", "exec"), module.__dict__)
        source_kind = "historical module source provided over stdin; compiled only in memory"
    assert not REGISTRY.exists(), "Use a fresh container without a retained home volume"
    owner_id = "benchA001-user"
    accounts = [module.resolve_user(identity) for identity in (owner_id, "benchC001-user")]
    assert accounts[0] != accounts[1]
    for name, home in accounts:
        entry = pwd.getpwnam(name)
        marker = Path(home) / "benchmark-marker.txt"
        marker.write_bytes(b"x" * 4096)
        marker.chmod(0o660)
        os.chown(marker, entry.pw_uid, entry.pw_gid)
    owner = accounts[0]
    for _ in range(5):
        assert module.resolve_user(owner_id) == owner
    registry_before = REGISTRY.read_bytes() if REGISTRY.exists() else None
    timings = []
    with external_work(module) as calls:
        for _ in range(100):
            started = time.perf_counter()
            account = module.resolve_user(owner_id)
            timings.append(time.perf_counter() - started)
            assert account == owner
    assert (REGISTRY.read_bytes() if REGISTRY.exists() else None) == registry_before
    report = {
        "label": payload["label"], "revision": payload["revision"],
        "source_sha256_lf": hashlib.sha256(source.replace("\r\n", "\n").encode()).hexdigest(),
        "source_kind": source_kind, "entrypoint": "resolve_user(exact upstream ID)",
        "warmup_calls": 5, "samples": 100, **percentiles(timings), "external_calls": dict(calls),
        "prepared_users_before_sampling": 2, "marker_bytes_per_user": 4096,
        "nss_user_count_before_slow_user": len(pwd.getpwall()),
        "nss_group_count_before_slow_user": len(grp.getgrall()),
        "versioned_registry": registry_before is not None,
        "legacy_semantic_warning": (
            "Old unversioned memory cache; legacy names/automatic IDs/no durable registry; latency reference only"
            if registry_before is None else None
        ),
        "environment": {
            "python": platform.python_version(), "kernel": platform.release(), "uid": os.getuid(),
            "fastapi": version("fastapi"), "starlette": version("starlette"), "pytest": version("pytest"),
            "nsswitch": Path("/etc/nsswitch.conf").read_text(),
        },
    }
    report["slow_other_user"] = slow_other_user(module, owner_id, owner)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
