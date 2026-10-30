"""REQ-002 AC-005/006/007/009: real HTTP file behavior and isolation.

The ``api`` fixture isolates account provisioning and administrator settings.
Requests still execute the production router, UserFS, OS I/O and serialization.
All data lives in the disposable test container; no deployment volume is used.
"""

import io
import subprocess
import zipfile

import pytest


def put(path, content="needle private-bob\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def denied(response):
    summary = {
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
    }
    assert response.status_code == 403, summary
    assert response.json() == {"detail": "Access denied"}


def test_two_users_manage_same_names_and_share_files_across_chats(api):
    for user, content in (("alice", "needle alice"), ("bob", "needle bob")):
        response = api.request("POST", "/files/upload", user=user,
                               params={"directory": "project"},
                               files={"file": ("same.txt", content.encode(), "text/plain")})
        assert response.status_code == 200, response.text
        response = api.request("POST", "/files/replace", user=user, json={
            "path": "project/same.txt",
            "replacements": [{"target": "needle", "replacement": "edited"}],
        })
        assert response.status_code == 200, response.text
        response = api.request("GET", "/files/read", user=user, session="second-chat",
                               params={"path": "project/same.txt"})
        assert response.status_code == 200, response.text
        assert response.json()["content"] == content.replace("needle", "edited")
        response = api.request("GET", "/files/grep", user=user,
                               params={"path": "project", "query": "edited"})
        assert response.status_code == 200, response.text
        assert [match["content"] for match in response.json()["matches"]] == ["edited " + user]
    response = api.request("DELETE", "/files/delete", params={"path": "project"})
    assert response.status_code == 200, response.text
    assert not (api.homes["alice"] / "project").exists()
    assert (api.homes["bob"] / "project/same.txt").read_text() == "edited bob"


@pytest.mark.parametrize("alias", ["/home/user", "/home/usr"])
def test_default_home_alias_resolves_to_request_user(api, alias):
    for user in ("alice", "bob"):
        response = api.request("POST", "/files/write", user=user,
                               json={"path": alias + "/alias.txt", "content": user})
        assert response.status_code == 200, response.text
        assert (api.homes[user] / "alias.txt").read_text() == user
        response = api.request("GET", "/files/read", user=user,
                               params={"path": alias + "/alias.txt"})
        assert response.status_code == 200, response.text
        assert response.json()["content"] == user


@pytest.mark.parametrize("form", ["absolute", "parent", "default-alias", "symlink"])
@pytest.mark.parametrize("endpoint", ["read", "display", "view", "grep"])
def test_private_file_read_forms_are_denied_without_contents(api, form, endpoint):
    target = put(api.homes["bob"] / "private.txt")
    if form == "absolute":
        path = str(target)
    elif form == "parent":
        path = "../" + api.homes["bob"].name + "/private.txt"
    elif form == "default-alias":
        path = "/home/user/../" + api.homes["bob"].name + "/private.txt"
    else:
        (api.homes["alice"] / "linked.txt").symlink_to(target)
        path = "linked.txt"
    params = {"path": path}
    if endpoint == "grep":
        params["query"] = "needle"
    denied(api.request("GET", "/files/" + endpoint, params=params))
    assert target.read_text() == "needle private-bob\n"


@pytest.mark.parametrize("endpoint", ["list", "grep", "glob", "search", "matches"])
@pytest.mark.parametrize("scope", ["other-home", "parent", "outside-home"])
def test_directory_and_search_start_authorization(api, endpoint, scope):
    target = {"other-home": api.homes["bob"], "parent": api.homes["alice"].parent,
              "outside-home": api.external}[scope]
    put(api.homes["bob"] / "private.txt")
    put(api.external / "private.txt")
    params = {"directory" if endpoint == "list" else "path": str(target)}
    if endpoint == "glob":
        params["pattern"] = "*"
    elif endpoint != "list":
        params["query"] = "private"
    denied(api.request("GET", "/files/" + endpoint, params=params))


@pytest.mark.parametrize("exists", [True, False])
def test_denied_file_existence_is_not_disclosed(api, exists):
    target = api.homes["bob"] / "undisclosed.txt"
    if exists:
        put(target)
    denied(api.request("GET", "/files/display", params={"path": str(target)}))


@pytest.mark.parametrize("endpoint", ["list", "grep", "glob", "search", "matches"])
@pytest.mark.parametrize("git_repository", [False, True])
def test_recursive_results_hide_forbidden_links_and_their_metadata(api, endpoint, git_repository):
    root = api.homes["alice"]
    put(root / "needle-own.txt", "needle own\n")
    secret = put(api.homes["bob"] / "needle-secret.txt")
    (root / "needle-forbidden-file.txt").symlink_to(secret)
    (root / "needle-forbidden-dir").symlink_to(api.homes["bob"], target_is_directory=True)
    if git_repository:
        subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "add", "--", "."], check=True, capture_output=True)
    params = {"directory" if endpoint == "list" else "path": str(root)}
    if endpoint == "glob":
        params["pattern"] = "*"
    elif endpoint != "list":
        params["query"] = "needle"
    response = api.request("GET", "/files/" + endpoint, params=params)
    assert response.status_code == 200, response.text
    assert "needle-own.txt" in response.text
    assert "needle-forbidden" not in response.text
    assert "needle-secret" not in response.text
    assert "private-bob" not in response.text
    assert str(api.homes["bob"]) not in response.text


@pytest.mark.parametrize("operation", ["write", "replace", "mkdir", "delete", "upload",
                                        "move-source", "move-destination"])
@pytest.mark.parametrize("scope", ["other-user", "public"])
def test_mutations_require_write_access_for_every_path(api, operation, scope):
    root = api.homes["bob"] if scope == "other-user" else api.public
    target = put(root / "untouched.txt", "original\n")
    own = put(api.homes["alice"] / "source.txt", "own\n")
    if operation == "write":
        response = api.request("POST", "/files/write", json={"path": str(target), "content": "changed"})
    elif operation == "replace":
        response = api.request("POST", "/files/replace", json={"path": str(target), "replacements": [
            {"target": "original", "replacement": "changed"}]})
    elif operation == "mkdir":
        response = api.request("POST", "/files/mkdir", json={"path": str(root / "created")})
    elif operation == "delete":
        response = api.request("DELETE", "/files/delete", params={"path": str(target)})
    elif operation == "upload":
        response = api.request("POST", "/files/upload", params={"directory": str(root)},
                               files={"file": ("untouched.txt", b"changed", "text/plain")})
    else:
        source, destination = ((target, api.homes["alice"] / "taken.txt")
                               if operation == "move-source" else (own, root / "moved.txt"))
        response = api.request("POST", "/files/move", json={
            "source": str(source), "destination": str(destination)})
    denied(response)
    assert target.read_text() == "original\n"
    assert own.read_text() == "own\n"
    assert not (root / "created").exists()
    assert not (root / "moved.txt").exists()
    assert not (api.homes["alice"] / "taken.txt").exists()


@pytest.mark.parametrize("operation", ["write", "upload", "mkdir"])
def test_new_targets_below_private_directory_link_are_rejected_before_creation(api, operation):
    (api.homes["alice"] / "linked").symlink_to(api.homes["bob"], target_is_directory=True)
    if operation == "write":
        response = api.request("POST", "/files/write", json={"path": "linked/new/deep.txt", "content": "bad"})
    elif operation == "upload":
        response = api.request("POST", "/files/upload", params={"directory": "linked/new"},
                               files={"file": ("deep.txt", b"bad", "text/plain")})
    else:
        response = api.request("POST", "/files/mkdir", json={"path": "linked/new/deep"})
    denied(response)
    assert not (api.homes["bob"] / "new").exists()


def test_public_read_access_does_not_depend_on_os_write_permissions(api):
    path = put(api.public / "manual.txt", "public manual\n")
    path.chmod(0o666)
    api.public.chmod(0o777)
    for user in ("alice", "bob"):
        response = api.request("GET", "/files/read", user=user, params={"path": str(path)})
        assert response.status_code == 200, response.text
        assert response.json()["content"] == "public manual\n"
        response = api.request("GET", "/files/list", user=user, params={"directory": str(api.public)})
        assert response.status_code == 200, response.text
        assert response.json()["writable"] is False
        entries = response.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["name"] == "manual.txt"
        assert entries[0]["size"] == len(b"public manual\n")
        assert entries[0]["writable"] is False


@pytest.mark.parametrize("operation", ["delete", "move"])
def test_workspace_root_cannot_be_removed_or_moved(api, operation):
    marker = put(api.homes["alice"] / "keep.txt", "keep")
    if operation == "delete":
        response = api.request("DELETE", "/files/delete", params={"path": str(api.homes["alice"])})
    else:
        response = api.request("POST", "/files/move", json={
            "source": str(api.homes["alice"]), "destination": str(api.homes["alice"] / "nested")})
    denied(response)
    assert marker.read_text() == "keep"


def test_normal_move_and_raw_download_preserve_bytes(api):
    data = b"\x00\xff\x01\x02"
    response = api.request("POST", "/files/upload", params={"directory": "."},
                           files={"file": ("binary.dat", data, "application/octet-stream")})
    assert response.status_code == 200, response.text
    response = api.request("POST", "/files/move", json={"source": "binary.dat", "destination": "renamed.dat"})
    assert response.status_code == 200, response.text
    response = api.request("GET", "/files/view", params={"path": "renamed.dat"})
    assert response.status_code == 200, response.text
    assert response.content == data
    assert not (api.homes["alice"] / "binary.dat").exists()


def test_private_file_outside_home_is_not_implicitly_public(api):
    path = put(api.external / "world-readable.txt", "external private\n")
    path.chmod(0o644)
    denied(api.request("GET", "/files/read", params={"path": str(path)}))


def test_own_link_to_public_material_is_readable_but_not_writable(api):
    public = put(api.public / "manual.txt", "public original")
    (api.homes["alice"] / "manual.txt").symlink_to(public)
    response = api.request("GET", "/files/read", params={"path": "manual.txt"})
    assert response.status_code == 200, response.text
    assert response.json()["content"] == "public original"
    denied(api.request("POST", "/files/write", json={"path": "manual.txt", "content": "changed"}))
    assert public.read_text() == "public original"


@pytest.mark.parametrize("operation", ["read", "write"])
def test_parent_segment_after_private_link_does_not_erase_authorization(api, operation):
    own = put(api.homes["alice"] / "same.txt", "Alice original")
    other = put(api.homes["bob"] / "same.txt", "Bob original")
    (api.homes["bob"] / "subdir").mkdir()
    (api.homes["alice"] / "linked").symlink_to(api.homes["bob"] / "subdir", target_is_directory=True)
    path = "linked/../same.txt"
    if operation == "read":
        response = api.request("GET", "/files/read", params={"path": path})
    else:
        response = api.request("POST", "/files/write", json={"path": path, "content": "changed"})
    assert own.read_text() == "Alice original"
    assert other.read_text() == "Bob original"
    denied(response)


@pytest.mark.parametrize("scope", ["other-user", "public"])
def test_upload_checks_existing_filename_link_in_authorized_directory(api, scope):
    root = api.homes["bob"] if scope == "other-user" else api.public
    target = put(root / "target.txt", "target original")
    link = api.homes["alice"] / "upload.txt"
    link.symlink_to(target)
    response = api.request("POST", "/files/upload", params={"directory": str(api.homes["alice"])},
                           files={"file": ("upload.txt", b"changed", "text/plain")})
    assert target.read_text() == "target original"
    assert link.is_symlink()
    denied(response)


def test_authorized_archive_filters_links_and_keeps_own_contents(api):
    root = api.homes["alice"] / "bundle"
    put(root / "own.txt", "own contents")
    secret = put(api.homes["bob"] / "secret.txt")
    (root / "forbidden.txt").symlink_to(secret)
    response = api.request("POST", "/files/archive", json={"paths": ["bundle"]})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["bundle/own.txt"]
        assert archive.read("bundle/own.txt") == b"own contents"
