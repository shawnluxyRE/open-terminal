"""Explicit lab verification through WebUI v0.12.3 ordinary-user sessions.

Run via stdin in the existing WebUI container with REQ002_WEBUI_LAB=1.
Credentials remain in memory. Reuses REQ-001 lab accounts, creates/reuses two
REQ-002 review chats per account, and leaves only named test artifacts.
"""
import hashlib
import hmac
import io
import json
import os
import shlex
import uuid
import zipfile

import requests
from docx import Document

BASE = "http://127.0.0.1:8080"
PREFIX = "/api/v1/terminals/open-terminal"
KEY = ""


def check(name, **data):
    print(json.dumps({"check": name, "result": "passed", **data}), flush=True)


def call(user, path, method="GET", expected=200, **kwargs):
    headers = kwargs.pop("headers", {})
    if user:
        headers["Authorization"] = "Bearer " + user["token"]
    response = requests.request(method, BASE + path, headers=headers, timeout=90, **kwargs)
    assert response.status_code == expected, (method, path, response.status_code, expected)
    assert not KEY or KEY.encode() not in response.content, "Credential in product response"
    if expected == 403:
        assert response.json() == {"detail": "Access denied"}
    return response


def terminal(user, path, method="GET", chat=0, **kwargs):
    return call(user, PREFIX + path, method, headers={"X-Session-Id": user["chats"][chat]}, **kwargs)


def login(email, password):
    return call(None, "/api/v1/auths/signin", "POST", json={"email": email, "password": password}).json()


def write(user, path, content):
    return terminal(user, "/files/write", "POST", json={"path": path, "content": content})


def main():
    global KEY
    assert os.environ.get("REQ002_WEBUI_LAB") == "1"
    connection = next(x for x in json.loads(os.environ["TERMINAL_SERVER_CONNECTIONS"]) if x["id"] == "open-terminal")
    KEY = connection["key"]
    admin = login(os.environ["WEBUI_ADMIN_EMAIL"], os.environ["WEBUI_ADMIN_PASSWORD"])
    configured = requests.get(BASE + "/api/v1/configs/terminal_servers", headers={
        "Authorization": "Bearer " + admin["token"]}, timeout=20)
    assert configured.status_code == 200
    actual = next(x for x in configured.json()["TERMINAL_SERVER_CONNECTIONS"] if x["id"] == "open-terminal")
    assert actual["key"] == KEY and actual["url"] == connection["url"]
    check("active_system_connection_matches_deployment")
    users = []
    for label in ("a", "b"):
        password = "Req001!" + hmac.new(KEY.encode(), ("req001-r4-lab-" + label).encode(), hashlib.sha256).hexdigest()
        user = login(f"req001-r4-{label}@example.com", password)
        assert user["role"] == "user"
        saved = call(user, "/api/v1/chats/list").json()
        if isinstance(saved, dict):
            saved = saved.get("items", saved.get("data", []))
        user["chats"] = []
        for number in (1, 2):
            title = f"REQ002 R4 {label.upper()} files {number}"
            chat = next((x for x in saved if x["title"] == title), None)
            if chat is None:
                chat = call(user, "/api/v1/chats/new", "POST", json={"chat": {
                    "title": title, "models": [], "messages": [],
                    "history": {"messages": {}, "currentId": None},
                }}).json()
            user["chats"].append(chat["id"])
        user["home"] = terminal(user, "/files/cwd").json()["home"]
        for chat_index in (0, 1):
            terminal(user, "/files/cwd", "POST", chat=chat_index, json={"path": user["home"]})
        users.append(user)
    a, b = users
    assert a["home"] != b["home"]
    check("ordinary_accounts_and_saved_chats", users=[{k: u[k] for k in ("id", "home", "chats")} for u in users])
    call(None, PREFIX + "/files/read", expected=401, params={"path": b["home"] + "/req002-r4/same.txt"})
    assert terminal(a, "/api/config").json()["features"]["notebooks"] is False

    for user, label in zip(users, ("A", "B")):
        terminal(user, "/files/upload", "POST", params={"directory": "req002-r4"},
                 files={"file": ("same.txt", ("needle " + label).encode(), "text/plain")})
        terminal(user, "/files/replace", "POST", json={"path": "req002-r4/same.txt",
                 "replacements": [{"target": "needle", "replacement": "edited"}]})
        read = terminal(user, "/files/read", chat=1, params={"path": "req002-r4/same.txt"})
        assert read.json()["content"] == "edited " + label
        for endpoint, params in (("grep", {"query": "edited"}), ("glob", {"pattern": "*.txt"}),
                                  ("search", {"query": "same"}), ("matches", {"query": "edited"})):
            result = terminal(user, "/files/" + endpoint, params={"path": "req002-r4", **params})
            assert "same.txt" in result.text
        source = "req002-r4/move-" + uuid.uuid4().hex + ".txt"
        destination = source + ".moved"
        write(user, source, "move fixture")
        terminal(user, "/files/move", "POST", json={"source": source, "destination": destination})
        terminal(user, "/files/delete", "DELETE", params={"path": destination})
        listing = terminal(user, "/files/list", params={"directory": "req002-r4"}).json()
        assert any(x["name"] == "same.txt" for x in listing["entries"])
    check("same_name_crud_search_and_cross_chat")

    private = b["home"] + "/req002-r4/same.txt"
    for endpoint in ("read", "display", "view", "grep"):
        terminal(a, "/files/" + endpoint, expected=403, params={"path": private, "query": "edited"})
    terminal(a, "/files/read", expected=403, params={"path": b["home"] + "/missing-private.txt"})
    terminal(a, "/files/cwd", "POST", expected=403, json={"path": b["home"]})
    terminal(a, "/files/archive", "POST", expected=403, json={"paths": ["req002-r4/same.txt", private]})
    terminal(a, "/files/compare", "POST", expected=403, json={"original": "req002-r4/same.txt", "revised": private})
    for user, other in ((a, b), (b, a)):
        source = "from pathlib import Path; p=Path('req002-r4/forbidden-link.txt'); p.unlink(missing_ok=True); p.symlink_to(" + repr(other["home"] + "/req002-r4/same.txt") + ")"
        result = terminal(user, "/execute", "POST", params={"wait": 10}, json={"command": "python3 -c " + shlex.quote(source)}).json()
        assert result["exit_code"] == 0
        terminal(user, "/files/read", expected=403, params={"path": "req002-r4/forbidden-link.txt"})
        for endpoint, params in (("list", {"directory": "req002-r4"}), ("grep", {"path": "req002-r4", "query": "edited"}),
                                  ("glob", {"path": "req002-r4", "pattern": "*"}), ("search", {"path": "req002-r4", "query": ""})):
            assert "forbidden-link" not in terminal(user, "/files/" + endpoint, params=params).text
    call(admin, PREFIX + "/files/read", expected=403, params={"path": private})
    assert terminal(b, "/files/read", params={"path": "req002-r4/same.txt"}).json()["content"] == "edited B"
    check("cross_user_links_admin_and_uniform_denial")

    for user in users:
        public = "/opt/req002-reference/manual.txt"
        assert terminal(user, "/files/read", params={"path": public}).json()["content"] == "REQ002 public reference\n"
        listing = terminal(user, "/files/list", params={"directory": "/opt/req002-reference"}).json()
        assert listing["writable"] is False and all(not x["writable"] for x in listing["entries"])
        terminal(user, "/files/write", "POST", expected=403, json={"path": public, "content": "bad"})
        terminal(user, "/files/replace", "POST", expected=403, json={"path": public, "replacements": [{"target": "REQ002", "replacement": "bad"}]})
        terminal(user, "/files/mkdir", "POST", expected=403, json={"path": "/opt/req002-reference/new"})
        terminal(user, "/files/delete", "DELETE", expected=403, params={"path": public})
        terminal(user, "/files/upload", "POST", expected=403, params={"directory": "/opt/req002-reference"}, files={"file": ("new.txt", b"bad")})
        terminal(user, "/files/move", "POST", expected=403, json={"source": public, "destination": "req002-r4/taken.txt"})
        terminal(user, "/files/move", "POST", expected=403, json={"source": "req002-r4/same.txt", "destination": "/opt/req002-reference/new.txt"})
    check("mounted_public_reference_readonly_all_mutations")

    document = Document()
    document.add_paragraph("REQ002 own Office document")
    buffer = io.BytesIO()
    document.save(buffer)
    terminal(a, "/files/upload", "POST", params={"directory": "req002-r4"}, files={"file": ("office.docx", buffer.getvalue())})
    assert "REQ002 own Office" in terminal(a, "/files/read", params={"path": "req002-r4/office.docx"}).json()["content"]
    preview = terminal(a, "/files/view", params={"path": "req002-r4/office.docx", "preview": "true"})
    assert preview.content.startswith(b"%PDF-")
    write(a, "req002-r4/page.html", "<p>REQ002 static fixture</p>")
    served = terminal(a, "/files/serve/" + a["home"].lstrip("/") + "/req002-r4/page.html")
    assert served.content == b"<p>REQ002 static fixture</p>"
    terminal(b, "/files/view", expected=403, params={"path": a["home"] + "/req002-r4/office.docx", "preview": "true"})
    terminal(b, "/files/serve/" + a["home"].lstrip("/") + "/req002-r4/page.html", expected=403)
    compared = terminal(a, "/files/compare", "POST", json={"original": "req002-r4/same.txt", "revised": "/opt/req002-reference/manual.txt"}).json()
    assert compared["additions"] > 0
    archive = terminal(a, "/files/archive", "POST", json={"paths": ["req002-r4"]})
    with zipfile.ZipFile(io.BytesIO(archive.content)) as output:
        assert "req002-r4/same.txt" in output.namelist()
        assert all("forbidden-link" not in x for x in output.namelist())
    skill = ".agents/skills/req002-r4-demo"
    write(a, skill + "/SKILL.md", "---\nname: req002-r4-demo\ndescription: REQ002 test skill\n---\nREQ002 skill body\n")
    write(a, skill + "/data.txt", "REQ002 skill resource")
    assert any(x["name"] == "req002-r4-demo" for x in terminal(a, "/skills").json())
    assert terminal(a, "/skills/read", params={"name": "req002-r4-demo"}).json()["resources"] == ["data.txt"]
    terminal(b, "/skills/read", expected=404, params={"name": "req002-r4-demo"})
    terminal(b, "/files/read", expected=403, params={"path": a["home"] + "/" + skill + "/data.txt"})
    check("real_office_static_compare_archive_and_skill")
    models = call(a, "/api/models").json().get("data", [])
    print(json.dumps({"check": "model_generated_file_tool_call", "result": "not_verified",
                      "available_model_count": len(models), "user_decision": "2026-10-30 explicitly retain as unverified"}), flush=True)
    check("integration_complete_except_user_accepted_model_gap")


if __name__ == "__main__":
    main()
