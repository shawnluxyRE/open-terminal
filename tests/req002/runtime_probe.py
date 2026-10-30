"""Actual UID1000 file/Office probe, launched only in disposable test Docker.

Normal HTTP requests use real identity provisioning and actual helper workers.
Cancellation/failure checks explicitly inject transport or worker failures;
they do not simulate file authorization or successful document conversion.
"""

import asyncio
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from unittest.mock import patch


KEY = "req002-runtime-test-key-not-a-secret"


def headers(identity=None):
    result = {"Authorization": "Bearer " + KEY, "X-Session-Id": "runtime-files"}
    if identity:
        result["X-User-Id"] = identity
    return result


def accepted(response):
    assert response.status_code == 200, (response.status_code, response.text[:1000])
    return response


def denied(response):
    assert response.status_code == 403, (response.status_code, response.text[:1000])
    assert response.json() == {"detail": "Access denied"}


@contextmanager
def generated_files():
    created = []
    original = tempfile.mkstemp

    def capture(*args, **kwargs):
        fd, path = original(*args, **kwargs)
        created.append(path)
        return fd, path

    with patch.object(tempfile, "mkstemp", capture):
        yield created
    assert all(not os.path.lexists(path) for path in created), created


def upload(client, identity, name, content):
    return accepted(client.post("/files/upload", headers=headers(identity),
                                params={"directory": "."},
                                files={"file": (name, content)}))


async def check_cleanup(main, fs, source, document):
    from starlette.requests import Request
    from open_terminal.utils import fs as fs_module

    scope = {"type": "http", "method": "GET", "path": "/", "headers": [],
             "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "scheme": "http", "query_string": b"",
             "server": ("probe", 80), "client": ("probe", 1)}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    # ASGI transport errors and task cancellation exercise the same response
    # cleanup used by the actual PDF/ZIP/JSON responses verified over HTTP.
    response_cases = []
    for suffix in (".pdf", ".zip", ".json", ".txt"):
        for error in (RuntimeError, asyncio.CancelledError):
            fd, path = tempfile.mkstemp(suffix=suffix)
            with os.fdopen(fd, "wb") as output:
                output.write(b"controlled transport-cleanup payload")
            response = fs_module.TemporaryFileResponse(path)

            async def fail_send(message):
                if message["type"] == "http.response.body":
                    raise error("controlled response failure")

            try:
                await response(scope, receive, fail_send)
            except error:
                pass
            else:
                raise AssertionError("Injected response failure was swallowed")
            assert not os.path.exists(path)
            response_cases.append(suffix + ":" + error.__name__)

    # Extraction must dispose its text output when a helper fails or is
    # cancelled, including the JSON attempt preceding binary extraction.
    for error in (RuntimeError, asyncio.CancelledError):
        async def failed_worker(*args, **kwargs):
            raise error("controlled extractor interruption")

        with generated_files() as created, patch.object(main, "run_worker", failed_worker):
            try:
                await main.read_file(Request(scope, receive), str(document), None, None, fs)
            except error:
                pass
            else:
                raise AssertionError("Injected extraction interruption was swallowed")
        assert {Path(path).suffix for path in created} >= {".txt", ".json"}

    # Failed text construction disposes its partially constructed JSON.
    with generated_files() as created:
        try:
            await fs_module.text_file_response(str(source) + ".missing", "missing")
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("Missing text source did not fail")
    assert [Path(path).suffix for path in created] == [".json"]

    started, release = threading.Event(), threading.Event()
    original_chunks = fs_module.text_chunks

    def slow_chunks(stream):
        started.set()
        assert release.wait(10), "Text cancellation probe timed out"
        yield from original_chunks(stream)

    with generated_files(), patch.object(fs_module, "text_chunks", slow_chunks):
        task = asyncio.create_task(fs_module.text_file_response(str(source), str(source)))
        assert await asyncio.to_thread(started.wait, 10)
        task.cancel()
        release.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Text build cancellation was swallowed")

    # Archive creation failures clean up the partially created ZIP file.
    import zipfile
    with generated_files() as created, patch.object(zipfile.ZipFile, "open", side_effect=RuntimeError("controlled ZIP failure")):
        try:
            await main.archive_paths(main.ArchiveRequest(paths=[str(source)]), fs)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Archive construction failure was swallowed")
    assert [Path(path).suffix for path in created] == [".zip"]

    # Cancel a live archive-building task after opening its output. The builder
    # must finish before the owner removes the ZIP, even on cancellation.
    started, release = threading.Event(), threading.Event()
    original_open = zipfile.ZipFile.open

    def slow_zip(*args, **kwargs):
        started.set()
        assert release.wait(10), "Archive cancellation probe timed out"
        return original_open(*args, **kwargs)

    with generated_files(), patch.object(zipfile.ZipFile, "open", slow_zip):
        task = asyncio.create_task(main.archive_paths(main.ArchiveRequest(paths=[str(source)]), fs))
        assert await asyncio.to_thread(started.wait, 10)
        task.cancel()
        release.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Archive build cancellation was swallowed")

    return {"transport_cleanup": response_cases,
            "construction_failure_and_cancel": ["extracted-text", "text-json", "archive-zip"]}


def worker():
    from docx import Document
    from fastapi.testclient import TestClient
    from open_terminal import main
    from open_terminal.dependencies import resolve_user
    from open_terminal.utils.fs import UserFS

    assert os.geteuid() == 1000, os.geteuid()
    assert shutil.which("soffice") or shutil.which("libreoffice"), "Actual LibreOffice is required"
    assert str(Path(main.__file__).resolve()).startswith(os.environ["REQ002_SOURCE_ROOT"])
    token = uuid.uuid4().hex
    identities = ["req002-runtime-a-" + token, "req002-runtime-b-" + token]
    public = Path(os.environ["REQ002_PUBLIC_ROOT"])
    assert os.access(public / "manual.txt", os.W_OK), "Public fixture must be OS writable"
    results = []

    with TestClient(main.app) as client:
        # Neither missing credentials nor missing identity may prepare accounts
        # or create the requested target.
        missing = "/home/req002-forbidden-create-" + token
        bad = client.post("/files/write", json={"path": missing, "content": "bad"})
        assert bad.status_code == 401
        bad = client.post("/files/write", headers=headers(), json={"path": missing, "content": "bad"})
        assert bad.status_code == 400
        assert not os.path.exists(missing)
        assert client.get("/api/notebooks", headers=headers(identities[0])).status_code == 404

        homes = []
        for index, identity in enumerate(identities):
            home = accepted(client.get("/files/cwd", headers=headers(identity))).json()["home"]
            homes.append(Path(home))
            account = pwd.getpwnam(Path(home).name)
            assert account.pw_gid in os.getgroups()
            content = "needle runtime-" + str(index)
            upload(client, identity, "same.txt", content.encode())
            accepted(client.post("/files/replace", headers=headers(identity), json={
                "path": "same.txt", "replacements": [{"target": "needle", "replacement": "edited"}]}))
            with generated_files() as created:
                read = accepted(client.get("/files/read", headers=headers(identity), params={"path": "same.txt"}))
            assert read.json()["content"] == content.replace("needle", "edited")
            assert any(path.endswith(".json") for path in created)
            owner = (Path(home) / "same.txt").stat()
            assert (owner.st_uid, owner.st_gid) == (account.pw_uid, account.pw_gid)
            accepted(client.get("/files/read", headers=headers(identity), params={"path": str(public / "manual.txt")}))
            denied(client.post("/files/write", headers=headers(identity), json={"path": str(public / "manual.txt"), "content": "bad"}))
            listing = accepted(client.get("/files/list", headers=headers(identity), params={"directory": str(public)})).json()
            assert listing["writable"] is False and listing["entries"][0]["writable"] is False
            results.append({"uid": account.pw_uid, "gid": account.pw_gid, "home": home, "owner_matches": True})
        assert homes[0] != homes[1]
        denied(client.get("/files/read", headers=headers(identities[0]), params={"path": str(homes[1] / "same.txt")}))
        assert (homes[1] / "same.txt").read_text() == "edited runtime-1"

        document = Document()
        document.add_paragraph("REQ002 actual service Office contents")
        buffer = io.BytesIO()
        document.save(buffer)
        upload(client, identities[0], "office.docx", buffer.getvalue())
        with generated_files() as created:
            read = accepted(client.get("/files/read", headers=headers(identities[0]), params={"path": "office.docx"}))
        assert "actual service Office contents" in read.json()["content"]
        assert {Path(path).suffix for path in created} >= {".txt", ".json"}
        with generated_files() as created:
            preview = accepted(client.get("/files/view", headers=headers(identities[0]), params={"path": "office.docx", "preview": "true"}))
        assert preview.headers["content-type"].startswith("application/pdf"), preview.headers
        assert preview.content.startswith(b"%PDF-")
        assert any(path.endswith(".pdf") for path in created), created
        for path in created:
            denied(client.get("/files/read", headers=headers(identities[1]), params={"path": path}))

        with generated_files() as created:
            archive = accepted(client.post("/files/archive", headers=headers(identities[0]), json={"paths": ["same.txt", "office.docx"]}))
        assert archive.content.startswith(b"PK")
        assert any(path.endswith(".zip") for path in created)

        async def forbidden_helper(*args, **kwargs):
            raise AssertionError("Unauthorized Office input reached a helper")

        with patch.object(main, "run_worker", forbidden_helper):
            for endpoint in ("read", "view"):
                denied(client.get("/files/" + endpoint, headers=headers(identities[1]),
                                  params={"path": str(homes[0] / "office.docx"), "preview": "true"}))

        username, home = resolve_user(identities[0])
        fs = UserFS(username, home, public_read_roots=(str(public),))
        cleanup = client.portal.call(check_cleanup, main, fs, homes[0] / "same.txt", homes[0] / "office.docx")
    print(json.dumps({"result": "passed", "service_uid": os.geteuid(), "users": results,
                      "actual_libreoffice": True, "actual_extractor": True,
                      "public_os_writable_product_readonly": True,
                      "normal_temporary_cleanup": ["json", "txt", "pdf", "zip"],
                      "controlled_failure_cleanup": cleanup}), flush=True)


def launch():
    assert os.geteuid() == 0 and Path("/.dockerenv").is_file()
    assert os.environ.get("REQ002_DISPOSABLE_CONTAINER") == "1"
    token = uuid.uuid4().hex
    public = Path(tempfile.mkdtemp(prefix="req002-public-runtime-", dir="/opt"))
    temporary = Path(tempfile.mkdtemp(prefix="req002-service-runtime-", dir="/tmp"))
    public.chmod(0o777)
    (public / "manual.txt").write_text("public manual")
    (public / "manual.txt").chmod(0o666)
    os.chown(temporary, 1000, 1000)
    source_root = str(Path(__file__).resolve().parents[2])
    try:
        command = ["sudo", "-u", "user", "env", "USER=user", "HOME=/home/user",
                   "PYTHONDONTWRITEBYTECODE=1", "PYTHONPATH=" + source_root,
                   "REQ002_SOURCE_ROOT=" + source_root, "REQ002_PUBLIC_ROOT=" + str(public),
                   "TMPDIR=" + str(temporary), "OPEN_TERMINAL_API_KEY=" + KEY,
                   "OPEN_TERMINAL_MULTI_USER=true", "OPEN_TERMINAL_USER_PREFIX=rt_",
                   "OPEN_TERMINAL_ENABLE_NOTEBOOKS=false", "OPEN_TERMINAL_ENABLE_TERMINAL=true",
                   "OPEN_TERMINAL_LOG_DIR=" + str(temporary / "logs"),
                   "OPEN_TERMINAL_PUBLIC_READ_ROOTS=" + json.dumps([str(public)]),
                   "/usr/local/bin/python3-ot", str(Path(__file__).resolve()), "worker"]
        result = subprocess.run(command, cwd="/tmp", capture_output=True, text=True, timeout=240)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip(), flush=True)
    finally:
        shutil.rmtree(public)
        shutil.rmtree(temporary)


if __name__ == "__main__":
    {"launch": launch, "worker": worker}[sys.argv[1]]()
