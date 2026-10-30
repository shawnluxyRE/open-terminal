"""Explicit real-model lab checks through WebUI's ordinary-user chat pipeline.

Stream this Windows-authored file into the WebUI container as python -.
REQ002_LLM_LAB=1 and REQ002_LLM_RUN_ID are required. PHASE=prepare creates
synthetic fixtures; PHASE=run executes actual model calls. No tool responses
are injected. Credentials remain in memory; only synthetic artifacts are used.
"""
import asyncio
import hashlib
import hmac
import json
import os
import shlex
import time
import uuid

import requests
import socketio

BASE = "http://127.0.0.1:8080"
TERMINAL = "/api/v1/terminals/open-terminal"
MODEL = "qwen3.8-flash-next"
RUN = os.environ.get("REQ002_LLM_RUN_ID", "")
SECRETS = []
ALLOWED_TOOLS = {
    "write_file", "replace_file_content", "read_file", "display_file",
    "list_files", "grep_search", "glob_search", "match_files", "search_files",
    "view_skill", "read_skill_file", "grep_skill_files", "search_tools",
}


def emit(data):
    text = json.dumps(data, ensure_ascii=False)
    for secret in SECRETS:
        if secret and (secret in text or json.dumps(secret, ensure_ascii=False)[1:-1] in text):
            raise AssertionError("Credential detected in evidence; output suppressed")
    print(text, flush=True)


def api(user, path, method="GET", expected=200, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if user:
        headers["Authorization"] = "Bearer " + user["token"]
    r = requests.request(method, BASE + path, headers=headers, timeout=60, **kwargs)
    assert r.status_code == expected, f"{method} {path}: HTTP {r.status_code}, expected {expected}"
    return r.json() if r.content else None


def terminal(user, chat, endpoint, method="GET", **kwargs):
    return api(user, TERMINAL + endpoint, method,
               headers={"X-Session-Id": chat}, **kwargs)


def write(user, chat, path, content):
    return terminal(user, chat, "/files/write", "POST", json={"path": path, "content": content})


def read(user, chat, path):
    return terminal(user, chat, "/files/read", params={"path": path})["content"]


def login(email, password):
    user = api(None, "/api/v1/auths/signin", "POST", json={"email": email, "password": password})
    SECRETS.extend([password, user["token"]])
    return user


def context(prepare=False):
    assert os.environ.get("REQ002_LLM_LAB") == "1"
    assert RUN and all(c.isalnum() or c in "-_" for c in RUN)
    connection = next(c for c in json.loads(os.environ["TERMINAL_SERVER_CONNECTIONS"]) if c["id"] == "open-terminal")
    key = connection["key"]
    SECRETS.append(key)
    admin = login(os.environ["WEBUI_ADMIN_EMAIL"], os.environ["WEBUI_ADMIN_PASSWORD"])
    config = api(admin, "/openai/config")
    SECRETS.extend(config["OPENAI_API_KEYS"])
    index = next(i for i, u in enumerate(config["OPENAI_API_BASE_URLS"]) if u == "http://10.0.0.14:4000/v1")
    assert config["OPENAI_API_KEYS"][index], "LLM key has not been synchronized"
    users = {}
    for label in ("a", "b"):
        password = "Req001!" + hmac.new(key.encode(), ("req001-r4-lab-" + label).encode(), hashlib.sha256).hexdigest()
        user = login(f"req001-r4-{label}@example.com", password)
        assert user["role"] == "user"
        saved = api(user, "/api/v1/chats/list")
        chats = []
        for n in range(2 if label == "a" else 1):
            title = f"REQ002 LLM {RUN} {label.upper()} {n + 1}"
            supplied_id = os.environ.get(f"REQ002_LLM_CHAT_{label.upper()}{n + 1}")
            found = api(user, "/api/v1/chats/" + supplied_id) if supplied_id else next((c for c in saved if c["title"] == title), None)
            if not found:
                assert prepare, "Run prepare before model tests"
                found = api(user, "/api/v1/chats/new", "POST", json={"chat": {
                    "title": title, "models": [MODEL], "messages": [],
                    "history": {"messages": {}, "currentId": None},
                }})
            chats.append(found["id"])
        user["chats"] = chats
        user["home"] = terminal(user, chats[0], "/files/cwd")["home"]
        user["root"] = user["home"] + "/req002-llm/" + RUN
        user["skill"] = "req002-llm-" + label + "-" + RUN
        user["skill_dir"] = user["home"] + "/.agents/skills/" + user["skill"]
        users[label] = user
    a, b = users["a"], users["b"]
    if prepare:
        for label, user in users.items():
            for n, chat in enumerate(user["chats"]):
                cwd = user["root"] + ("/alternate" if n else "")
                terminal(user, chat, "/files/mkdir", "POST", json={"path": cwd})
                write(user, chat, cwd + "/context.txt", "CONTEXT_" + label.upper() + "_" + uuid.uuid4().hex)
                terminal(user, chat, "/files/cwd", "POST", json={"path": cwd})
            write(user, user["chats"][0], user["root"] + "/private-guard.txt", "PRIVATE_" + label.upper() + "_" + uuid.uuid4().hex)
            skill_marker = "SKILL_" + label.upper() + "_" + uuid.uuid4().hex
            resource_marker = "RESOURCE_" + label.upper() + "_" + uuid.uuid4().hex
            body = (f"---\nname: {user['skill']}\ndescription: Synthetic REQ002 file-tool verification skill for this account.\n---\n"
                    "Use only dedicated Terminal file tools. Read data.txt from this skill directory. "
                    f"Write skill-result.txt in the current working directory containing exactly {skill_marker}|"
                    "followed immediately by the complete data.txt contents, with no additional text. "
                    "Then read the result and report the full resulting string. Never use command execution.\n")
            write(user, user["chats"][0], user["skill_dir"] + "/SKILL.md", body)
            write(user, user["chats"][0], user["skill_dir"] + "/data.txt", resource_marker)
        code = ("from pathlib import Path; p=Path(" + repr(a["root"] + "/forbidden-link.txt") + "); "
                "p.unlink(missing_ok=True); p.symlink_to(" + repr(b["root"] + "/private-guard.txt") + ")")
        executed = terminal(a, a["chats"][0], "/execute", "POST", params={"wait": 10}, json={"command": "python3 -c " + shlex.quote(code)})
        assert executed["exit_code"] == 0
    emit({"type": "context", "run": RUN, "model": MODEL, "phase": "prepare" if prepare else "run",
          "users": {k: {n: u[n] for n in ("id", "home", "root", "chats", "skill")} for k, u in users.items()}})
    return users


def output_text(output):
    return "\n".join(part.get("text", "") for item in output if item.get("type") == "message"
                     for part in item.get("content", []) if part.get("type") == "output_text")


def calls(output):
    results = {item["call_id"]: item for item in output if item.get("type") == "function_call_output"}
    paired = []
    for item in output:
        if item.get("type") != "function_call":
            continue
        name = item["name"].split(".")[-1]
        assert name in ALLOWED_TOOLS, "Model used a tool outside the file-test scope: " + name
        result = results.get(item.get("call_id", item.get("id")))
        assert result is not None, "No actual output for model tool call " + name
        args = json.loads(item.get("arguments") or "{}")
        text = "\n".join(p.get("text", "") for p in result.get("output", []) if isinstance(p, dict))
        paired.append({"name": name, "arguments": args, "call_id": item.get("call_id"),
                       "status": result.get("status"), "result": text})
    return paired


def require_call(paired, name, *, path=None, denied=False):
    paths = path if isinstance(path, (tuple, list)) else [path]
    matching = [c for c in paired if c["name"] == name and (path is None or c["arguments"].get("path") in paths)]
    assert matching, f"Required actual model call missing: {name} {path or ''}"
    if denied:
        errors = [json.loads(c["result"]).get("error", "") for c in matching if c["status"] == "failed"]
        assert any("HTTP error 403" in error and "Access denied" in error for error in errors), "Missing actual 403 tool result"
    else:
        assert any('"error"' not in c["result"] and c["status"] == "completed" for c in matching), "Required tool call did not succeed"


class ModelSession:
    def __init__(self, user):
        self.user = user
        self.socket = socketio.AsyncClient(reconnection=False)
        self.current = None
        self.socket.on("events", self.event)

    async def event(self, payload):
        event = payload.get("data", {})
        if event.get("type") == "request:terminal:state":
            return {"connected": False}
        state = self.current
        if not state or payload.get("chat_id") != state["chat"] or payload.get("message_id") != state["message"]:
            return
        if event.get("type", "").startswith("terminal:"):
            state["events"].append(event)
        if event.get("type") == "chat:message:error":
            state["error"] = event.get("data")
            state["finished"].set()
        if event.get("type") == "chat:completion" and event.get("data", {}).get("done"):
            state["final"] = event["data"]
            state["finished"].set()

    async def connect(self):
        await self.socket.connect(BASE, auth={"token": self.user["token"]}, socketio_path="ws/socket.io", transports=["websocket"])

    async def ask(self, case, chat, prompt):
        saved = await asyncio.to_thread(api, self.user, "/api/v1/chats/" + chat)
        parent = saved["chat"]["history"].get("currentId")
        mid, uid = str(uuid.uuid4()), str(uuid.uuid4())
        prompt = ("This is an authorized integration test using only synthetic test files. "
                  "Use only dedicated file tools and skill tools, never run_command, execute_code, shell, browser, or delegation. "
                  "Actually call the requested tools; report their real results, and do not bypass denied operations. " + prompt)
        state = {"chat": chat, "message": mid, "events": [], "finished": asyncio.Event()}
        self.current = state
        payload = {"model": MODEL, "stream": True, "terminal_id": "open-terminal", "chat_id": chat,
                   "id": mid, "session_id": self.socket.get_sid(), "parent_id": parent,
                   "params": {"function_calling": "native", "temperature": 0},
                   "background_tasks": {"title_generation": False, "tags_generation": False, "follow_up_generation": False},
                   "user_message": {"id": uid, "role": "user", "content": prompt, "parentId": parent,
                                    "childrenIds": [mid], "models": [MODEL], "timestamp": int(time.time())},
                   "messages": [{"role": "user", "content": prompt}]}
        started = time.monotonic()
        response = await asyncio.to_thread(api, self.user, "/api/chat/completions", "POST", json=payload)
        assert response.get("status") and response.get("task_ids"), "Chat pipeline did not start"
        try:
            await asyncio.wait_for(state["finished"].wait(), timeout=180)
        except asyncio.TimeoutError:
            emit({"type": "failure", "case": case, "chat_id": chat, "message_id": mid, "reason": "model turn timed out"})
            raise
        if state.get("error"):
            emit({"type": "failure", "case": case, "error": state["error"]})
            raise AssertionError("Chat pipeline failed")
        output = state["final"].get("output", [])
        stored = await asyncio.to_thread(api, self.user, "/api/v1/chats/" + chat)
        message = stored["chat"]["history"]["messages"][mid]
        assert message.get("done") and message.get("output") == output, "Stored tool evidence differs from streamed completion"
        paired = calls(output)
        assert paired, "Model did not call an actual tool"
        last_result = max(i for i, item in enumerate(output) if item.get("type") == "function_call_output")
        answer = output_text(output[last_result + 1:])
        assert answer.strip(), "No model response after the final tool result"
        result = {"type": "model_turn", "case": case, "run": RUN, "chat_id": chat, "message_id": mid,
                  "model": MODEL, "seconds": round(time.monotonic() - started, 2), "prompt": prompt,
                  "calls": paired, "answer": answer, "terminal_events": state["events"], "persisted": True}
        emit(result)
        return result


async def run():
    users = context()
    a, b = users["a"], users["b"]
    sessions = {k: ModelSession(u) for k, u in users.items()}
    selected = set(os.environ.get("REQ002_LLM_CASES", "a-workflow,b-workflow,cross-chat,public,private-denial,foreign-skill,own-skill").split(","))
    assert selected and selected <= {"a-workflow", "b-workflow", "cross-chat", "public", "private-denial", "foreign-skill", "own-skill", "display"}, "Unknown test case"
    try:
        for session in sessions.values():
            await session.connect()
        for label in ("a", "b"):
            case = label + "-workflow"
            if case not in selected:
                continue
            user = users[label]
            marker = "REQ002_" + label.upper() + "_" + RUN
            result = await sessions[label].ask(case, user["chats"][0],
                f"In this chat's current directory, call write_file with the relative path same.txt and content {marker}_START. "
                f"Then call replace_file_content to change START to UPDATED; read_file same.txt; grep_search for {marker}_UPDATED in '.'. "
                "Read context.txt and include its entire content in your final answer. Finally call display_file on same.txt with inline=false.")
            for name in ("write_file", "replace_file_content", "read_file", "grep_search", "display_file"):
                require_call(result["calls"], name)
            require_call(result["calls"], "write_file", path="same.txt")
            for name in ("replace_file_content", "read_file", "display_file"):
                require_call(result["calls"], name, path=("same.txt", user["root"] + "/same.txt"))
            require_call(result["calls"], "read_file", path=("context.txt", user["root"] + "/context.txt"))
            searches = [json.loads(c["result"]) for c in result["calls"] if c["name"] == "grep_search" and c["arguments"].get("query") == marker + "_UPDATED"]
            assert any(any(m.get("content") == marker + "_UPDATED" and m.get("file") == user["root"] + "/same.txt" for m in s.get("matches", [])) for s in searches)
            assert read(user, user["chats"][0], "same.txt") == marker + "_UPDATED"
            assert read(user, user["chats"][0], "context.txt") in result["answer"]
            assert any(e["type"] == "terminal:display_file" and e["data"]["path"] == user["root"] + "/same.txt" for e in result["terminal_events"])
            emit({"type": "check", "case": case, "result": "passed", "owner_home": user["home"]})
        if "cross-chat" in selected:
            original = read(a, a["chats"][0], "same.txt")
            marker = "ALT_" + RUN
            result = await sessions["a"].ask("cross-chat", a["chats"][1],
                f"Read {a['root']}/same.txt and report its content. Read relative context.txt and report its entire content. "
                f"Write relative same.txt containing exactly {marker}, then read it back. Keep paths relative where requested.")
            require_call(result["calls"], "read_file", path=a["root"] + "/same.txt")
            require_call(result["calls"], "write_file", path="same.txt")
            require_call(result["calls"], "read_file", path=("same.txt", a["root"] + "/alternate/same.txt"))
            require_call(result["calls"], "read_file", path=("context.txt", a["root"] + "/alternate/context.txt"))
            assert original in result["answer"] and read(a, a["chats"][1], "context.txt") in result["answer"]
            assert read(a, a["chats"][1], "same.txt") == marker
            assert read(a, a["chats"][0], "same.txt") == original
            emit({"type": "check", "case": "cross-chat", "result": "passed"})
        if "public" in selected:
            path = "/opt/req002-reference/manual.txt"
            before = read(a, a["chats"][0], path)
            result = await sessions["a"].ask("public", a["chats"][0],
                f"Read relative same.txt, then read {path}. For this explicit read-only enforcement test call write_file on {path} "
                "with content TEST_CHANGE. Expect the tool to deny it. Report the actual tool error and the public file's read contents; do not retry by another method.")
            require_call(result["calls"], "read_file", path=("same.txt", a["root"] + "/same.txt"))
            require_call(result["calls"], "read_file", path=path)
            require_call(result["calls"], "write_file", path=path, denied=True)
            assert read(a, a["chats"][0], path) == before
            emit({"type": "check", "case": "public", "result": "passed"})
        if "private-denial" in selected:
            path = b["root"] + "/private-guard.txt"
            before = read(b, b["chats"][0], path)
            result = await sessions["a"].ask("private-denial", a["chats"][0],
                f"This is an explicit synthetic authorization-negative test. Call read_file on {path}; "
                f"call read_file with relative forbidden-link.txt; call write_file on {path} with content TEST_CHANGE. "
                "Run all three dedicated file calls even when a prior one returns 403. Report the real errors, do not use any workaround. Then read relative same.txt successfully to show recovery.")
            require_call(result["calls"], "read_file", path=path, denied=True)
            require_call(result["calls"], "read_file", path=("forbidden-link.txt", a["root"] + "/forbidden-link.txt"), denied=True)
            require_call(result["calls"], "write_file", path=path, denied=True)
            require_call(result["calls"], "read_file", path=("same.txt", a["root"] + "/same.txt"))
            assert before not in json.dumps(result) and read(b, b["chats"][0], path) == before
            emit({"type": "check", "case": "private-denial", "result": "passed", "private_hash_unchanged": True})
        if "foreign-skill" in selected:
            path = a["skill_dir"] + "/data.txt"
            secret = read(a, a["chats"][0], path)
            result = await sessions["b"].ask("foreign-skill", b["chats"][0],
                f"Read /opt/req002-reference/manual.txt. For a synthetic access-negative test, call view_skill on id terminal:{a['skill']}, "
                f"then call read_file on {path}. Report the actual outcomes without guessing the missing skill or using commands.")
            require_call(result["calls"], "read_file", path="/opt/req002-reference/manual.txt")
            require_call(result["calls"], "read_file", path=path, denied=True)
            skill_calls = [c for c in result["calls"] if c["name"] == "view_skill" and c["arguments"].get("id") == "terminal:" + a["skill"]]
            assert skill_calls and any("error" in c["result"].lower() or "not found" in c["result"].lower() for c in skill_calls)
            assert secret not in json.dumps(result)
            emit({"type": "check", "case": "foreign-skill", "result": "passed"})
        if "own-skill" in selected:
            result = await sessions["a"].ask("own-skill", a["chats"][0],
                f"Use the skill named {a['skill']}. Discover its instructions using view_skill, read its supporting resource, "
                "and carry out its file-writing instructions using dedicated file tools. Report the complete final result string. Do not use commands.")
            require_call(result["calls"], "view_skill")
            assert any(c["name"] == "view_skill" and c["arguments"].get("id") == "terminal:" + a["skill"] and c["status"] == "completed" for c in result["calls"])
            require_call(result["calls"], "read_file", path=a["skill_dir"] + "/data.txt")
            require_call(result["calls"], "write_file", path=("skill-result.txt", a["root"] + "/skill-result.txt"))
            require_call(result["calls"], "read_file", path=("skill-result.txt", a["root"] + "/skill-result.txt"))
            manifest = read(a, a["chats"][0], a["skill_dir"] + "/SKILL.md")
            marker = manifest.split("containing exactly ", 1)[1].split("|", 1)[0]
            expected = marker + "|" + read(a, a["chats"][0], a["skill_dir"] + "/data.txt")
            assert read(a, a["chats"][0], "skill-result.txt") == expected
            assert expected in result["answer"]
            emit({"type": "check", "case": "own-skill", "result": "passed", "hidden_resource_used": True})
        if "display" in selected:
            path = a["root"] + "/same.txt"
            result = await sessions["a"].ask("display", a["chats"][0],
                f"Read {path}, then call display_file with that exact path and inline=false so the live browser opens it. "
                "Reply briefly with the content you read and the actual display outcome. Do not modify files.")
            require_call(result["calls"], "read_file", path=path)
            require_call(result["calls"], "display_file", path=path)
            assert read(a, a["chats"][0], path) in result["answer"]
            assert any(e["type"] == "terminal:display_file" and e["data"]["path"] == path for e in result["terminal_events"])
            emit({"type": "check", "case": "display", "result": "passed", "visual_check": "separate browser observation required"})
        if {"a-workflow", "b-workflow"} <= selected:
            assert read(a, a["chats"][0], "same.txt") != read(b, b["chats"][0], "same.txt")
        emit({"type": "summary", "result": "passed", "run": RUN, "cases": sorted(selected), "model": MODEL})
    finally:
        for session in sessions.values():
            if session.socket.connected:
                await session.socket.disconnect()


if __name__ == "__main__":
    if os.environ.get("REQ002_LLM_PHASE") == "prepare":
        context(prepare=True)
    else:
        asyncio.run(run())
