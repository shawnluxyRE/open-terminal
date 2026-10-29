"""Run explicitly inside the WebUI v0.12.3 lab container, outside pytest.

Uses real admin/user login and the installed system connection. Creates/reuses
two named lab accounts and leaves their marker files for review. Credentials
come from the container environment and are never printed or written to disk.
The ordinary-user requests only use WebUI session tokens, never the service key.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import os
import time

import aiohttp
import requests


BASE = "http://127.0.0.1:8080"
PREFIX = "/api/v1/terminals/open-terminal"
KEY = ""


def call(path, *, token=None, method="GET", data=None, headers=None, expected=200):
    outgoing = dict(headers or {})
    if token:
        outgoing["Authorization"] = "Bearer " + token
    response = requests.request(
        method, BASE + path, headers=outgoing, json=data, timeout=50
    )
    assert response.status_code == expected, f"{method} {path}: HTTP {response.status_code}, expected {expected}"
    assert not KEY or KEY not in response.text, "Service credential in ordinary response"
    if not response.content:
        return None
    return response.json()


def terminal(user, path, *, chat=None, **kwargs):
    headers = kwargs.pop("headers", {})
    headers["X-Session-Id"] = user["other_chat_id"] if chat == "req001-chat-other" else (chat or user["chat_id"])
    return call(PREFIX + path, token=user["token"], headers=headers, **kwargs)


def check(label, **evidence):
    print(json.dumps({"check": label, "result": "passed", **evidence}), flush=True)


def login(email, password):
    return call("/api/v1/auths/signin", method="POST", data={"email": email, "password": password})


def lab_password(label):
    # Stable, unlogged lab passwords allow rerunning after a failed verification.
    return "Req001!" + hmac.new(KEY.encode(), ("req001-r4-lab-" + label).encode(), hashlib.sha256).hexdigest()


async def ws_roundtrip(user, sid, home, uid, username):
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect(BASE + PREFIX + "/api/terminals/" + sid) as ws:
            await ws.send_json({"type": "auth", "token": user["token"], "chat_id": user["chat_id"]})
            # The echo of this command cannot contain the complete expected output.
            await ws.send_bytes(
                b"printf '\\nREQ001_%s\\n' PTY_OK; "
                b"printf 'REQ001_UID_%s__\\n' \"$(id -u)\"; "
                b"printf 'REQ001_USER_%s__\\n' \"$(whoami)\"; pwd\n"
            )
            expected_uid = f"REQ001_UID_{uid}__".encode()
            expected_username = f"REQ001_USER_{username}__".encode()
            output = b""
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                msg = await ws.receive(timeout=10)
                if msg.type == aiohttp.WSMsgType.BINARY:
                    output += msg.data
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    output += msg.data.encode()
                else:
                    raise AssertionError("WebUI WS closed before PTY output")
                assert KEY.encode() not in output
                if (
                    b"REQ001_PTY_OK" in output and home.encode() in output
                    and expected_uid in output and expected_username in output
                ):
                    break
            else:
                raise AssertionError("WebUI WS roundtrip timed out")
    check("webui_authenticated_ws_pty", home=home, uid=uid, username=username)


async def ws_denied(token, sid, *, expected=None, chat_id="req001-chat-a"):
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect(BASE + PREFIX + "/api/terminals/" + sid) as ws:
            await ws.send_json({"type": "auth", "token": token, "chat_id": chat_id})
            msg = await ws.receive(timeout=15)
            assert msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED), "Unauthorized WS produced data"
            if expected is not None:
                assert ws.close_code == expected


def main():
    global KEY
    assert os.getenv("REQ001_WEBUI_LAB") == "1", "Explicit lab opt-in required"
    connection = next(c for c in json.loads(os.environ["TERMINAL_SERVER_CONNECTIONS"]) if c["id"] == "open-terminal")
    KEY = connection["key"]
    assert KEY
    admin = login(os.environ["WEBUI_ADMIN_EMAIL"], os.environ["WEBUI_ADMIN_PASSWORD"])
    assert admin["role"] == "admin"
    # Only this administrator-only response contains the configured service key.
    configured = requests.get(BASE + "/api/v1/configs/terminal_servers", headers={"Authorization": "Bearer " + admin["token"]}, timeout=20)
    assert configured.status_code == 200
    active_connection = next(c for c in configured.json()["TERMINAL_SERVER_CONNECTIONS"] if c["id"] == "open-terminal")
    assert active_connection["key"] == KEY and active_connection["url"] == connection["url"]
    check("active_database_connection_matches_deployment")
    existing = call("/api/v1/users/?query=req001-r4-", token=admin["token"])["users"]
    users, created = [], []
    for label in ("a", "b"):
        email = f"req001-r4-{label}@example.com"
        if not any(u["email"] == email for u in existing):
            added = call("/api/v1/auths/add", token=admin["token"], method="POST", data={
                "email": email, "password": lab_password(label),
                "name": "REQ001 R4 " + label.upper(), "role": "user",
            })
            created.append(added["id"])
        user = login(email, lab_password(label))
        assert user["role"] == "user"
        chats = []
        for number in (1, 2):
            saved = call("/api/v1/chats/new", token=user["token"], method="POST", data={"chat": {
                "title": f"REQ001 R4 {label.upper()} workspace check {number}", "models": [],
                "messages": [], "history": {"messages": {}, "currentId": None},
            }})
            chats.append(saved["id"])
        user["chat_id"], user["other_chat_id"] = chats
        users.append(user)
    a, b = users
    check(
        "two_real_ordinary_logins",
        users=[{"id": u["id"], "role": u["role"], "chats": [u["chat_id"], u["other_chat_id"]]} for u in users],
        newly_created=created,
    )

    call(PREFIX + "/files/cwd", expected=401)
    asyncio.run(ws_denied("invalid-session-token", "unknown", expected=4001, chat_id=a["chat_id"]))
    check("unauthenticated_http_and_ws_rejected")
    for user in users:
        call("/api/v1/configs/terminal_servers", token=user["token"], expected=401)
        connections = call("/api/v1/terminals/", token=user["token"])
        assert any(c["id"] == "open-terminal" for c in connections)
        assert all("key" not in c and "auth_type" not in c for c in connections)
        call("/api/config", token=user["token"])
        config = terminal(user, "/api/config")
        assert config["features"]["notebooks"] is False
    check("ordinary_connection_config_and_capabilities_without_service_key")

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [(index, pool.submit(terminal, users[index % 2], "/files/cwd")) for index in range(16)]
        homes = {0: set(), 1: set()}
        for index, future in futures:
            homes[index % 2].add(future.result()["cwd"])
    assert all(len(group) == 1 for group in homes.values())
    home_a, home_b = next(iter(homes[0])), next(iter(homes[1]))
    assert home_a != home_b
    check("concurrent_resource_preparation", requests=16, homes=[home_a, home_b])
    for user, home, marker in ((a, home_a, "A"), (b, home_b, "B")):
        written = terminal(user, "/files/write", method="POST", data={"path": "req001-r4-marker.txt", "content": "REQ001-" + marker})
        assert written["path"] == home + "/req001-r4-marker.txt"
        read = terminal(user, "/files/read?path=req001-r4-marker.txt")
        assert read["content"] == "REQ001-" + marker
        cross_chat = terminal(user, "/files/read?path=req001-r4-marker.txt", chat="req001-chat-other")
        assert cross_chat["content"] == "REQ001-" + marker
    assert terminal(a, "/files/read?path=req001-r4-marker.txt", chat="req001-chat-other")["content"] == "REQ001-A"
    assert terminal(b, "/files/cwd", headers={"X-User-Id": a["id"]})["cwd"] == home_b
    check("distinct_files_cross_chat_stability_and_spoofed_header_ignored")

    original_id, original_name = a["id"], a["name"]
    try:
        call("/api/v1/auths/update/profile", token=a["token"], method="POST", data={"name": "REQ001 renamed A", "profile_image_url": "/user.png"})
        renewed = login("req001-r4-a@example.com", lab_password("a"))
        assert renewed["id"] == original_id and renewed["name"] == "REQ001 renamed A"
        a.update(renewed)
        assert a["id"] == original_id and a["name"] == "REQ001 renamed A"
        assert terminal(a, "/files/cwd")["cwd"] == home_a
        assert terminal(a, "/files/read?path=req001-r4-marker.txt")["content"] == "REQ001-A"
    finally:
        call("/api/v1/auths/update/profile", token=a["token"], method="POST", data={"name": original_name, "profile_image_url": "/user.png"})
    check("relogin_and_display_name_stable_binding", user_id=original_id)

    jobs = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(terminal, u, "/execute?wait=5", method="POST", data={"command": "id -u; id -g; whoami; pwd"}) for u in (a, b)]
        jobs = [future.result() for future in pending]
    identities = []
    for index, (owner, other, home) in enumerate(((a, b, home_a), (b, a, home_b))):
        job = jobs[index]
        assert job["status"] == "done" and job["exit_code"] == 0
        assert job["cwd"] == home
        output = "".join(item["data"] for item in job["output"])
        lines = output.splitlines()
        assert len(lines) == 4 and lines[3] == home
        assert 10001 <= int(lines[0]) <= 19999 and 10001 <= int(lines[1]) <= 19999
        assert lines[2] == home.rsplit("/", 1)[-1]
        identities.append({"id": owner["id"], "home": home, "username": lines[2], "uid": int(lines[0]), "gid": int(lines[1])})
        terminal(owner, "/execute/" + job["id"] + "/status")
        terminal(other, "/execute/" + job["id"] + "/status", chat=owner["chat_id"], expected=404)
        terminal(owner, "/execute/" + job["id"] + "/status", chat="req001-chat-other", expected=404)
        visible = terminal(other, "/execute", chat=owner["chat_id"])
        assert job["id"] not in json.dumps(visible)
    assert identities[0]["uid"] != identities[1]["uid"] and identities[0]["gid"] != identities[1]["gid"]
    check("concurrent_real_commands_and_task_user_chat_ownership", jobs=[j["id"] for j in jobs], identities=identities)

    sid = terminal(a, "/api/terminals", method="POST")["id"]
    try:
        terminal(b, "/api/terminals/" + sid, chat=a["chat_id"], expected=404)
        terminal(a, "/api/terminals/" + sid, chat="req001-chat-other", expected=404)
        assert sid not in json.dumps(terminal(b, "/api/terminals", chat=a["chat_id"]))
        # Keep the owner's actual chat ID so this checks user ownership alone.
        asyncio.run(ws_denied(b["token"], sid, chat_id=a["chat_id"]))
        asyncio.run(ws_roundtrip(a, sid, home_a, identities[0]["uid"], identities[0]["username"]))
    finally:
        # Closing WS follows the established terminal cleanup contract.
        response = requests.delete(BASE + PREFIX + "/api/terminals/" + sid, headers={"Authorization": "Bearer " + a["token"], "X-Session-Id": a["chat_id"]}, timeout=20)
        assert response.status_code in (200, 404)
        assert KEY not in response.text
    check("terminal_http_ws_ownership")
    print(json.dumps({"result": "passed", "scope": "REQ-001 real WebUI v0.12.3 API integration", "users": [u["id"] for u in users], "homes": [home_a, home_b]}))


if __name__ == "__main__":
    main()
