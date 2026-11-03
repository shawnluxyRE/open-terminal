"""Exercise an isolated Terminal through the existing WebUI lab, then clean up.

Run explicitly inside the WebUI container. JSON stdin supplies the temporary
container name/key, a nonsecret workspace seed and integration_webui.py source.
The temporary connection is visible only to the existing two lab users. Existing
connections are preserved byte-for-byte as JSON values, and no chats/accounts
are created. The calling test runner owns the isolated Terminal container.
"""

import asyncio
import json
import os
import sys
import types

from open_webui.models.config import Config


CONFIG_KEY = "terminal_server.connections"


async def add_connection(connection):
    from open_webui.internal.db import get_async_db
    from sqlalchemy import select

    async with get_async_db() as db:
        row = (await db.execute(select(Config).where(Config.key == CONFIG_KEY).with_for_update())).scalar_one()
        original = row.value
        assert not any(item.get("id") == connection["id"] for item in original)
        await Config.upsert({CONFIG_KEY: [*original, connection]}, db=db)
        await db.commit()
    return original


async def remove_connection(connection, original):
    from open_webui.internal.db import get_async_db
    from sqlalchemy import select

    async with get_async_db() as db:
        row = (await db.execute(select(Config).where(Config.key == CONFIG_KEY).with_for_update())).scalar_one()
        current = row.value
        owned = [item for item in current if item.get("id") == connection["id"]]
        assert owned == [connection], "Temporary connection changed unexpectedly"
        remaining = [item for item in current if item.get("id") != connection["id"]]
        await Config.upsert({CONFIG_KEY: remaining}, db=db)
        await db.commit()
    # Preserve concurrent configuration edits; report a mismatch instead of
    # replacing someone else's current connections with our old snapshot.
    assert remaining == original, "Other connections changed during the probe"
    assert await Config.get(CONFIG_KEY) == original


def main():
    assert os.environ.get("REQ001_WEBUI_LAB") == "1", "Explicit lab opt-in required"
    payload = json.load(sys.stdin)
    server_id = payload["server_id"]
    assert server_id.startswith("req001-perf-") and server_id.replace("-", "").isalnum()
    helper = types.ModuleType("workspace_live_probe_helpers")
    exec(compile(payload["helper_source"], "integration_webui.py", "exec"), helper.__dict__)
    original_connections = asyncio.run(Config.get(CONFIG_KEY))
    production = next(item for item in original_connections if item.get("id") == "open-terminal")
    helper.KEY = production["key"]
    users = []
    for label in ("a", "b"):
        user = helper.login(f"req001-r4-{label}@example.com", helper.lab_password(label))
        assert user["role"] == "user" and user["id"] in payload["seed"]["registry"]["users"]
        existing = helper.call("/api/v1/chats/", token=user["token"])
        chats = [item["id"] for item in existing if item["title"].startswith(f"REQ001 R4 {label.upper()} workspace check")]
        assert len(chats) >= 2, "Two original saved lab chats are required"
        user.update(chat_id=chats[0], other_chat_id=chats[1], label=label)
        users.append(user)
    connection = {
        "id": server_id, "name": "REQ001 temporary performance verification",
        "url": f"http://{server_id}:8000", "path": "/openapi.json",
        "key": payload["api_key"], "auth_type": "bearer", "enabled": True,
        "server_type": "terminal", "config": {"access_grants": [
            {"principal_type": "user", "principal_id": user["id"], "permission": "read"}
            for user in users
        ]},
    }
    original = asyncio.run(add_connection(connection))
    try:
        assert original == original_connections
        helper.KEY = payload["api_key"]
        helper.PREFIX = "/api/v1/terminals/" + server_id
        helper.call(helper.PREFIX + "/files/cwd", expected=401)
        for index, user in enumerate(users):
            record = payload["seed"]["registry"]["users"][user["id"]]
            home = record["home"]
            assert any(c["id"] == server_id for c in helper.call("/api/v1/terminals/", token=user["token"]))
            assert helper.terminal(user, "/files/cwd")["cwd"] == home
            marker = "REQ001-" + user["label"].upper()
            assert helper.terminal(user, "/files/read?path=req001-r4-marker.txt")["content"] == marker
            helper.terminal(user, "/files/write", method="POST", data={"path": "req001-r4-marker.txt", "content": marker})
            assert helper.terminal(user, "/files/read?path=req001-r4-marker.txt", chat="req001-chat-other")["content"] == marker
            command = helper.terminal(user, "/execute?wait=5", method="POST", data={"command": "id -u; id -g; whoami; pwd"})
            assert command["status"] == "done" and command["exit_code"] == 0
            lines = "".join(entry["data"] for entry in command["output"]).splitlines()
            assert lines == [str(record["uid"]), str(record["gid"]), record["username"], home]
            other = users[1 - index]
            helper.terminal(other, "/execute/" + command["id"] + "/status", chat=user["chat_id"], expected=404)
            helper.terminal(user, "/execute/" + command["id"] + "/status", chat="req001-chat-other", expected=404)
            helper.terminal(user, "/ports")
            sid = helper.terminal(user, "/api/terminals", method="POST")["id"]
            try:
                helper.terminal(other, "/api/terminals/" + sid, chat=user["chat_id"], expected=404)
                asyncio.run(helper.ws_denied(other["token"], sid, chat_id=user["chat_id"]))
                asyncio.run(helper.ws_roundtrip(user, sid, home, record["uid"], record["username"]))
            finally:
                response = helper.requests.delete(
                    helper.BASE + helper.PREFIX + "/api/terminals/" + sid,
                    headers={"Authorization": "Bearer " + user["token"], "X-Session-Id": user["chat_id"]}, timeout=20,
                )
                assert response.status_code in (200, 404)
            assert helper.terminal(user, "/api/terminals") == []
            helper.check("isolated_webui_files_command_ports_pty", user_id=user["id"], home=home, uid=record["uid"])
        assert helper.terminal(users[1], "/files/cwd", headers={"X-User-Id": users[0]["id"]})["cwd"] == payload["seed"]["registry"]["users"][users[1]["id"]]["home"]
    finally:
        asyncio.run(remove_connection(connection, original))
        print(json.dumps({"check": "temporary_connection_removed_and_original_configuration_preserved", "result": "passed"}))
    print(json.dumps({"result": "passed", "server_id": server_id, "created_accounts": 0, "created_chats": 0}))


if __name__ == "__main__":
    main()
