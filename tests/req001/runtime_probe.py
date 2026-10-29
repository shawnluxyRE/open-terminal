"""Disposable-container integration probe; no deployment credentials or volumes.

The multi-user probe runs two real non-root Python server processes with the
standard image's python3-ot CAP_SETGID executable. Each starts its FastAPI
lifespan and warms executor threads before any account is prepared.
"""

import asyncio
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import uuid


KEY = "req001-test-key-not-a-secret"


def accepted(response):
    assert response.status_code == 200, (response.status_code, response.text)
    return response.json()


def headers(identity=None, chat="runtime-chat"):
    result = {"Authorization": f"Bearer {KEY}", "X-Session-Id": chat}
    if identity is not None:
        result["X-User-Id"] = identity
    return result


async def warm_threads():
    import aiofiles
    import anyio

    # Keep executor workers alive across preparation, not just one fresh thread.
    await asyncio.gather(*(asyncio.to_thread(lambda: os.getgroups()) for _ in range(8)))
    await anyio.to_thread.run_sync(os.getgroups)
    async with aiofiles.open("/etc/hostname") as stream:
        await stream.read()


def check_command(client, identity, uid):
    response = accepted(client.post(
        "/execute?wait=10", headers=headers(identity),
        json={"command": "printf '__REQ_COMMAND_%s__\\n' OK; id -u"},
    ))
    assert response["exit_code"] == 0, response
    output = "".join(item["data"] for item in response["output"])
    assert "__REQ_COMMAND_OK__" in output, output
    assert str(uid) in output.split(), output
    if identity is not None:
        assert client.get(f"/execute/{response['id']}/status", headers=headers(identity + "-other")).status_code == 404
        assert client.delete(f"/execute/{response['id']}", headers=headers(identity, "other-chat")).status_code == 404
    return response["id"]


def check_websocket(client, identity, uid):
    from starlette.websockets import WebSocketDisconnect

    for chat_via_header in (True, False):
        terminal = accepted(client.post("/api/terminals", headers=headers(identity)))
        endpoint = f"/api/terminals/{terminal['id']}"
        if identity is not None:
            assert client.get(endpoint, headers=headers(identity + "-other")).status_code == 404
            assert client.delete(endpoint, headers=headers(identity + "-other")).status_code == 404
            try:
                with client.websocket_connect(endpoint, headers=headers(identity + "-other")) as ws:
                    ws.send_json({"type": "auth", "token": KEY})
                    ws.receive_bytes()
            except WebSocketDisconnect as failure:
                assert failure.code == 4004
            else:
                raise AssertionError("Another identity connected to a real terminal")
        ws_headers = headers(identity)
        if not chat_via_header:
            ws_headers.pop("X-Session-Id")
        with client.websocket_connect(endpoint, headers=ws_headers) as ws:
            ws.send_json({
                "type": "auth", "token": KEY,
                "chat_id": "ignored-wrong-chat" if chat_via_header else "runtime-chat",
            })
            ws.send_json({"type": "resize", "cols": 100, "rows": 30})
            # The complete expected marker is absent from the echoed command.
            ws.send_bytes(b"printf '__REQ_UID_%s__\\n' \"$(id -u)\"\n")
            expected = f"__REQ_UID_{uid}__".encode()
            output = bytearray()
            while expected not in output:
                output.extend(ws.receive_bytes())
                assert len(output) < 65536, bytes(output)
            # Complete the close handshake before TestClient cancels its app
            # task on context exit, so the real terminal cleanup can finish.
            ws.close(code=1000)
            while True:
                message = ws.receive()
                if message["type"] == "websocket.close":
                    assert message["code"] == 1000, message
                    break
                assert message["type"] == "websocket.send", message
                output.extend(message.get("bytes") or message.get("text", "").encode())
                assert len(output) < 65536, bytes(output)
        assert client.get(endpoint, headers=headers(identity)).status_code == 404


def exercise(client, identity, label, *, terminal=False):
    cwd = accepted(client.get("/files/cwd", headers=headers(identity)))["cwd"]
    account = pwd.getpwnam(Path(cwd).name)
    assert os.getuid() != 0
    assert account.pw_gid in os.getgroups(), (account.pw_gid, os.getgroups())
    assert 10001 <= account.pw_uid <= 19999 and 10001 <= account.pw_gid <= 19999
    filename = f"{label}.txt"
    for content in ("first write", "replacement via warmed aiofiles"):
        accepted(client.post("/files/write", headers=headers(identity), json={"path": filename, "content": content}))
        result = accepted(client.get("/files/read", headers=headers(identity), params={"path": filename}))
        assert result["content"] == content, result
    entry = Path(cwd) / filename
    assert (entry.stat().st_uid, entry.stat().st_gid) == (account.pw_uid, account.pw_gid)
    command_id = check_command(client, identity, account.pw_uid)
    if terminal:
        check_websocket(client, identity, account.pw_uid)
    return {"identity": identity, "home": cwd, "uid": account.pw_uid, "gid": account.pw_gid, "command_id": command_id}


def worker():
    from fastapi.testclient import TestClient
    from open_terminal.main import app

    with TestClient(app) as client:
        client.portal.call(warm_threads)
        print(json.dumps({"ready": True, "pid": os.getpid(), "uid": os.getuid()}), flush=True)
        for line in sys.stdin:
            request = json.loads(line)
            if request.get("stop"):
                break
            result = exercise(client, request["identity"], request["label"], terminal=request.get("terminal", False))
            print(json.dumps(result), flush=True)


def read_result(process):
    line = process.stdout.readline()
    if not line:
        raise AssertionError(f"Probe process exited {process.poll()}: {process.stderr.read()}")
    return json.loads(line)


def send(process, identity, label, *, terminal=False):
    process.stdin.write(json.dumps({"identity": identity, "label": label, "terminal": terminal}) + "\n")
    process.stdin.flush()


def nonroot_processes():
    assert os.geteuid() == 0 and Path("/.dockerenv").exists()
    assert os.environ.get("REQ001_DISPOSABLE_CONTAINER") == "1"
    executable = "/usr/local/bin/python3-ot"
    assert Path(executable).is_file(), "Standard runtime capability-bearing Python is required"
    token = uuid.uuid4().hex
    processes = []
    try:
        for index in range(2):
            command = [
                "sudo", "-u", "user", "env",
                "USER=deliberately-not-the-service-user", "PYTHONDONTWRITEBYTECODE=1",
                f"OPEN_TERMINAL_API_KEY={KEY}", "OPEN_TERMINAL_MULTI_USER=true",
                "OPEN_TERMINAL_USER_PREFIX=rt_", "OPEN_TERMINAL_ENABLE_NOTEBOOKS=false",
                "OPEN_TERMINAL_ENABLE_TERMINAL=true", "OPEN_TERMINAL_ENABLE_SYSTEM_PROMPT=true",
                f"OPEN_TERMINAL_LOG_DIR=/tmp/req001-runtime-{token}-{index}",
                executable, str(Path(__file__).resolve()), "worker",
            ]
            processes.append(subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd="/tmp"))
        ready = [read_result(process) for process in processes]
        assert all(item["ready"] and item["uid"] != 0 for item in ready)
        assert ready[0]["pid"] != ready[1]["pid"]
        # Same identity's first requests run concurrently in independent servers.
        identity = "runtime-same-" + token
        for index, process in enumerate(processes):
            send(process, identity, f"first-{index}")
        same = [read_result(process) for process in processes]
        assert same[0]["home"] == same[1]["home"]
        assert (same[0]["uid"], same[0]["gid"]) == (same[1]["uid"], same[1]["gid"])
        # Different identities allocate concurrently after all IO pools are warm.
        identities = ["runtime-A-" + token, "runtime-B-" + token]
        for index, process in enumerate(processes):
            send(process, identities[index], f"distinct-{index}")
        distinct = [read_result(process) for process in processes]
        for field in ("home", "uid", "gid"):
            assert distinct[0][field] != distinct[1][field], distinct
        # Each already-running server must pick up the other server's new group.
        for index, process in enumerate(processes):
            send(process, identities[1 - index], f"cross-process-{index}", terminal=True)
        resumed = [read_result(process) for process in processes]
        assert resumed[0]["home"] == distinct[1]["home"]
        assert resumed[1]["home"] == distinct[0]["home"]
        registry = json.loads(Path("/home/.open-terminal/registry.json").read_text())
        for value in [identity, *identities]:
            assert registry["users"][value]["state"] == "ready"
        print(json.dumps({"result": "passed", "mode": "two-nonroot-servers", "same_identity": same, "distinct_identities": distinct, "warmed_io": True, "normal_command_and_pty": True}))
    finally:
        errors = []
        for process in processes:
            if process.poll() is None:
                try:
                    process.stdin.write('{"stop":true}\n')
                    process.stdin.flush()
                    process.wait(timeout=10)
                except (BrokenPipeError, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait()
            if process.returncode != 0:
                errors.append(process.stderr.read())
        assert not errors, "\n".join(errors)


def single_user():
    assert os.environ["OPEN_TERMINAL_MULTI_USER"] == "false"
    from fastapi.testclient import TestClient
    from open_terminal.main import app

    assert "open_terminal.utils.user_isolation" not in sys.modules
    with tempfile.TemporaryDirectory(prefix="req001-single-") as directory:
        os.chdir(directory)
        with TestClient(app) as client:
            assert client.get("/system", headers=headers()).status_code == 200
            assert accepted(client.get("/files/cwd", headers=headers()))["cwd"] == directory
            accepted(client.post("/files/write", headers=headers(), json={"path": "single.txt", "content": "single-user compatibility"}))
            assert accepted(client.get("/files/read?path=single.txt", headers=headers()))["content"] == "single-user compatibility"
            check_command(client, None, os.getuid())
            check_websocket(client, None, os.getuid())
    assert "open_terminal.utils.user_isolation" not in sys.modules
    print(json.dumps({"result": "passed", "mode": "single-user", "lifespan": True, "files_commands_pty": True}))


if __name__ == "__main__":
    {"worker": worker, "nonroot": nonroot_processes, "single": single_user}[sys.argv[1]]()
