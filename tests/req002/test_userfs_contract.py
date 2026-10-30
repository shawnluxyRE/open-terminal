"""REQ-002: UserFS metadata, mutating path semantics and search backends."""

import asyncio

import pytest

from open_terminal.utils.file_access import FileAccessDenied
from open_terminal.utils.fs import UserFS


def scoped(api):
    return UserFS(username="root", home=str(api.homes["alice"]),
                  public_read_roots=(str(api.public),))


def test_new_userfs_entry_authorizes_read_and_write_separately(api):
    fs = scoped(api)
    target = api.public / "manual.txt"
    target.write_text("manual")
    assert fs.require_path(str(target)) == str(target)
    with pytest.raises(FileAccessDenied, match="^Access denied$"):
        fs.require_path(str(target), write=True)


def test_single_user_entry_keeps_existing_external_file_access(tmp_path):
    fs = UserFS(home=str(tmp_path / "home"))
    target = tmp_path / "external.txt"
    target.write_text("single")
    assert fs.require_path(str(target), write=True) == str(target)
    assert asyncio.run(fs.read_text(str(target))) == "single"


def test_public_metadata_never_reports_write_permission(api):
    fs = scoped(api)
    target = api.public / "manual.txt"
    target.write_text("manual")
    target.chmod(0o666)
    info = asyncio.run(fs.stat(str(target)))
    assert info["size"] == 6
    assert info["writable"] is False
    assert asyncio.run(fs.is_writable(str(target))) is False


@pytest.mark.parametrize("operation", ["read", "read_text", "exists", "isfile", "isdir", "stat"])
def test_direct_userfs_metadata_and_read_reject_private_links(api, operation):
    secret = api.homes["bob"] / "private.txt"
    secret.write_text("private")
    link = api.homes["alice"] / "linked.txt"
    link.symlink_to(secret)
    with pytest.raises(PermissionError, match="^Access denied$"):
        asyncio.run(getattr(scoped(api), operation)(str(link)))
    assert secret.read_text() == "private"


def test_walk_excludes_forbidden_names_and_directory_links(api):
    fs = scoped(api)
    root = api.homes["alice"]
    (root / "own.txt").write_text("own")
    secret = api.homes["bob"] / "private.txt"
    secret.write_text("private")
    (root / "forbidden-file").symlink_to(secret)
    (root / "forbidden-dir").symlink_to(api.homes["bob"], target_is_directory=True)
    rows = asyncio.run(fs.walk(str(root)))
    assert rows == [(str(root), [], ["own.txt"])]


@pytest.mark.parametrize("operation", ["write", "write_bytes", "mkdir", "remove"])
def test_direct_mutations_reject_public_paths_before_side_effects(api, operation):
    fs = scoped(api)
    path = api.public / "manual.txt"
    path.write_text("manual")
    target = api.public / "new-dir" if operation == "mkdir" else path
    args = ("changed",) if operation == "write" else (b"changed",) if operation == "write_bytes" else ()
    with pytest.raises(PermissionError, match="^Access denied$"):
        asyncio.run(getattr(fs, operation)(str(target), *args))
    assert path.read_text() == "manual"
    assert not (api.public / "new-dir").exists()


def test_move_does_not_implicitly_retarget_an_existing_directory(api):
    fs = scoped(api)
    source = api.homes["alice"] / "source.txt"
    source.write_text("source")
    destination = api.homes["alice"] / "existing"
    destination.mkdir()
    with pytest.raises(FileExistsError):
        asyncio.run(fs.move(str(source), str(destination)))
    assert source.read_text() == "source"
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("operation", ["remove", "move"])
def test_mutating_own_directory_link_changes_the_link_not_its_target(api, operation):
    fs = scoped(api)
    target = api.homes["alice"] / "target"
    target.mkdir()
    (target / "keep.txt").write_text("keep")
    link = api.homes["alice"] / "link"
    link.symlink_to(target, target_is_directory=True)
    if operation == "remove":
        asyncio.run(fs.remove(str(link)))
    else:
        destination = api.homes["alice"] / "renamed-link"
        asyncio.run(fs.move(str(link), str(destination)))
        assert destination.is_symlink()
    assert not link.is_symlink()
    assert (target / "keep.txt").read_text() == "keep"


@pytest.mark.parametrize("endpoint", ["search", "matches"])
def test_search_without_git_or_ripgrep_still_filters_links(api, monkeypatch, endpoint):
    from open_terminal.utils import search

    # Isolate unavailable optional executables, not search candidates or policy.
    original_run = search.subprocess.run

    def no_git(command, *args, **kwargs):
        if command[0] == "git":
            raise FileNotFoundError("git unavailable in this fixture")
        return original_run(command, *args, **kwargs)

    original_which = search.shutil.which
    monkeypatch.setattr(search.subprocess, "run", no_git)
    monkeypatch.setattr(search.shutil, "which", lambda name: None if name == "rg" else original_which(name))
    root = api.homes["alice"]
    (root / "needle-own.txt").write_text("needle own")
    secret = api.homes["bob"] / "secret.txt"
    secret.write_text("needle private")
    (root / "needle-forbidden.txt").symlink_to(secret)
    response = api.request("GET", "/files/" + endpoint, params={"query": "needle", "path": str(root)})
    assert response.status_code == 200, response.text
    assert "needle-own.txt" in response.text
    assert "forbidden" not in response.text
    assert "needle private" not in response.text
