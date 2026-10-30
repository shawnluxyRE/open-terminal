"""Verify archive filenames through the existing WebUI ordinary-user proxy.

Run explicitly via stdin in WebUI with REQ002_ARCHIVE_LAB=1. Uses synthetic
files in a unique test directory and leaves them for review; no keys are logged.
"""
from email.message import Message
import hashlib
import hmac
import io
import json
import os
import uuid
import zipfile

import requests

BASE = "http://127.0.0.1:8080"
PREFIX = "/api/v1/terminals/open-terminal"


def main():
    assert os.environ.get("REQ002_ARCHIVE_LAB") == "1"
    connection = next(c for c in json.loads(os.environ["TERMINAL_SERVER_CONNECTIONS"]) if c["id"] == "open-terminal")
    key = connection["key"]
    run_id = "req002-r5-002-" + uuid.uuid4().hex[:8]
    cases = []
    for label in ("a", "b"):
        password = "Req001!" + hmac.new(key.encode(), ("req001-r4-lab-" + label).encode(), hashlib.sha256).hexdigest()
        response = requests.post(BASE + "/api/v1/auths/signin", json={
            "email": f"req001-r4-{label}@example.com", "password": password,
        }, timeout=20)
        assert response.status_code == 200
        user = response.json()
        assert user["role"] == "user"
        headers = {"Authorization": "Bearer " + user["token"]}
        saved = requests.get(BASE + "/api/v1/chats/list", headers=headers, timeout=20)
        assert saved.status_code == 200 and saved.json()
        headers["X-Session-Id"] = saved.json()[0]["id"]

        def api(path, method="GET", **kwargs):
            result = requests.request(method, BASE + PREFIX + path, headers=headers, timeout=45, **kwargs)
            assert result.status_code == 200, (method, path, result.status_code)
            assert key.encode() not in result.content, "Service key in ordinary response"
            return result

        home = api("/files/cwd").json()["home"]
        root = home + "/" + run_id
        for name, directory in (("项目说明.txt", False), ("项目资料", True), ('report "draft".txt', False), ("图表📊.txt", False)):
            member = name + "/说明.txt" if directory else name
            content = ("archive fixture " + label + " " + member).encode("utf-8")
            api("/files/write", "POST", json={"path": root + "/" + member, "content": content.decode("utf-8")})
            result = api("/files/archive", "POST", json={"paths": [root + "/" + name]})
            assert result.headers["content-type"].startswith("application/zip")
            header = Message()
            header["Content-Disposition"] = result.headers["content-disposition"]
            assert header.get_content_disposition() == "attachment"
            assert header.get_filename() == name + ".zip"
            with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
                assert archive.namelist() == [member]
                assert archive.read(member) == content
            cases.append({"user": label, "input": name, "directory": directory,
                          "status": result.status_code, "download_name": header.get_filename(),
                          "zip_member_and_content_verified": True})
    print(json.dumps({"result": "passed", "run_id": run_id, "cases": cases}, ensure_ascii=False))


if __name__ == "__main__":
    main()
