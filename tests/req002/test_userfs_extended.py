"""Additional R4 checks for mutation semantics and response cleanup."""

import asyncio
import errno
import os
import threading

import pytest

from open_terminal.utils.file_access import FileAccessDenied
from open_terminal.utils.fs import TemporaryFileResponse, UserFS


def scoped(api):
    return UserFS("root", str(api.homes["alice"]),
                  public_read_roots=(str(api.public),))


@pytest.mark.parametrize("operation", ["remove", "move"])
def test_link_to_home_root_cannot_remove_or_relocate_home(api, operation):
    home = api.homes["alice"]
    marker = home / "keep.txt"
    marker.write_text("keep")
    link = home / "root-link"
    link.symlink_to(home, target_is_directory=True)
    args = (str(link), str(home / "moved")) if operation == "move" else (str(link),)
    with pytest.raises(FileAccessDenied, match="^Access denied$"):
        asyncio.run(getattr(scoped(api), operation)(*args))
    assert link.is_symlink()
    assert marker.read_text() == "keep"


def test_recursive_remove_unlinks_private_children_without_following_them(api):
    source = api.homes["alice"] / "owned"
    source.mkdir()
    secret = api.homes["bob"] / "secret.txt"
    secret.write_text("Bob unchanged")
    (source / "private-dir").symlink_to(api.homes["bob"], target_is_directory=True)
    (source / "private-file").symlink_to(secret)
    (source / "own.txt").write_text("Alice")
    asyncio.run(scoped(api).remove(str(source)))
    assert not source.exists()
    assert secret.read_text() == "Bob unchanged"


def test_cross_device_move_fallback_preserves_private_child_links(api, monkeypatch):
    """Inject only EXDEV; the real shutil copy/remove fallback performs I/O."""
    source = api.homes["alice"] / "source"
    source.mkdir()
    destination = api.homes["alice"] / "destination"
    secret = api.homes["bob"] / "secret.txt"
    secret.write_text("Bob unchanged")
    (source / "private-dir").symlink_to(api.homes["bob"], target_is_directory=True)
    (source / "private-file").symlink_to(secret)
    (source / "own.txt").write_text("Alice")
    original = os.rename

    def cross_device(src, dst, *args, **kwargs):
        if src == str(source) and dst == str(destination):
            raise OSError(errno.EXDEV, "Cross-device link")
        return original(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "rename", cross_device)
    asyncio.run(scoped(api).move(str(source), str(destination)))
    assert not source.exists()
    assert (destination / "own.txt").read_text() == "Alice"
    assert (destination / "private-dir").is_symlink()
    assert (destination / "private-file").is_symlink()
    assert os.readlink(destination / "private-file") == str(secret)
    assert secret.read_text() == "Bob unchanged"


def test_move_relative_link_does_not_chown_or_chmod_new_external_target(api):
    home = api.homes["alice"]
    nested = home / "nested"
    nested.mkdir()
    own = home / "private.txt"
    own.write_text("own")
    # At the original location ../private.txt is private to Alice. Moving the
    # link preserves its literal value; ownership fixups must not follow it.
    link = nested / "relative"
    link.symlink_to("../private.txt")
    destination = home / "moved-relative"
    outside = home.parent / "private.txt"
    assert not outside.exists()
    try:
        outside.write_text("external")
        outside.chmod(0o640)
        before = outside.stat()
        asyncio.run(scoped(api).move(str(link), str(destination)))
        after = outside.stat()
        assert destination.is_symlink()
        assert os.readlink(destination) == "../private.txt"
        assert (after.st_uid, after.st_gid, after.st_mode) == (before.st_uid, before.st_gid, before.st_mode)
        assert outside.read_text() == "external"
    finally:
        outside.unlink()


def test_move_to_missing_parent_leaves_source_and_directory_tree_unchanged(api):
    home = api.homes["alice"]
    source = home / "source.txt"
    source.write_text("keep")
    with pytest.raises(FileNotFoundError):
        asyncio.run(scoped(api).move(str(source), str(home / "missing/target.txt")))
    assert source.read_text() == "keep"
    assert not (home / "missing").exists()


@pytest.mark.parametrize("operation", ["remove", "move"])
@pytest.mark.parametrize("suffix", ["/", "/.", "/nested/.."])
def test_directory_mutations_keep_dot_parent_and_trailing_separator_semantics(api, operation, suffix):
    home = api.homes["alice"]
    source = home / "source"
    (source / "nested").mkdir(parents=True)
    (source / "own.txt").write_text("owned")
    entry = str(source) + suffix
    if operation == "remove":
        asyncio.run(scoped(api).remove(entry))
    else:
        destination = home / "destination"
        asyncio.run(scoped(api).move(entry, str(destination)))
        assert (destination / "own.txt").read_text() == "owned"
    assert not source.exists()


@pytest.mark.parametrize("operation", ["remove", "move"])
def test_directory_link_with_trailing_separator_changes_link_not_target(api, operation):
    home = api.homes["alice"]
    target = home / "target"
    target.mkdir()
    (target / "keep.txt").write_text("keep")
    link = home / "link"
    link.symlink_to(target, target_is_directory=True)
    entry = str(link) + "/"
    if operation == "remove":
        asyncio.run(scoped(api).remove(entry))
    else:
        destination = home / "moved-link"
        asyncio.run(scoped(api).move(entry, str(destination)))
        assert destination.is_symlink()
    assert not link.is_symlink()
    assert (target / "keep.txt").read_text() == "keep"


@pytest.mark.parametrize("side", ["source", "destination"])
@pytest.mark.parametrize("scope", ["other", "public"])
def test_move_authorizes_both_ends_before_mutating_either(api, side, scope):
    own = api.homes["alice"] / "own.txt"
    own.write_text("own")
    other_root = api.homes["bob"] if scope == "other" else api.public
    other = other_root / "original.txt"
    other.write_text("other")
    source = other if side == "source" else own
    destination = api.homes["alice"] / "moved.txt" if side == "source" else other_root / "moved.txt"
    with pytest.raises(FileAccessDenied, match="^Access denied$"):
        asyncio.run(scoped(api).move(str(source), str(destination)))
    assert own.read_text() == "own"
    assert other.read_text() == "other"
    assert not destination.exists()


def test_public_entry_linked_to_private_directory_still_reports_read_only(api):
    home = api.homes["alice"]
    own = home / "own.txt"
    own.write_text("own")
    link = api.public / "alice"
    link.symlink_to(home, target_is_directory=True)
    fs = scoped(api)
    assert asyncio.run(fs.stat(str(link / "own.txt")))["writable"] is False
    assert asyncio.run(fs.listdir(str(link)))[0]["writable"] is False
    with pytest.raises(FileAccessDenied, match="^Access denied$"):
        asyncio.run(fs.write(str(link / "own.txt"), "changed"))
    assert own.read_text() == "own"


def test_walk_does_not_recurse_into_authorized_directory_symlinks(api):
    home = api.homes["alice"]
    target = home / "target"
    target.mkdir()
    (target / "own.txt").write_text("own")
    (home / "link").symlink_to(target, target_is_directory=True)
    rows = asyncio.run(scoped(api).walk(str(home)))
    assert [row[0] for row in rows] == [str(home), str(target)]


@pytest.mark.parametrize("ending", ["success", "send-error", "cancel"])
def test_temporary_response_removes_artifact_after_all_response_endings(tmp_path, ending):
    path = tmp_path / "derived.json"
    path.write_bytes(b'{"content":"fixture"}')
    response = TemporaryFileResponse(str(path), media_type="application/json")
    messages = []

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body":
            if ending == "send-error":
                raise OSError("client disconnected")
            if ending == "cancel":
                raise asyncio.CancelledError()

    async def receive():
        return {"type": "http.disconnect"}

    async def run():
        # ASGI 2.4 reports disconnects through send(). Earlier versions race
        # file sending against receive(), so an immediate disconnect would
        # prevent this test from reaching either the bytes or injected error.
        await response({"type": "http", "method": "GET", "headers": [],
                        "asgi": {"spec_version": "2.4"}}, receive, send)

    if ending == "success":
        asyncio.run(run())
        assert b"".join(item.get("body", b"") for item in messages) == b'{"content":"fixture"}'
    else:
        with pytest.raises(OSError if ending == "send-error" else asyncio.CancelledError):
            asyncio.run(run())
    assert not path.exists()


def test_temporary_response_cleans_up_legacy_asgi_disconnect(tmp_path):
    path = tmp_path / "disconnect.zip"
    path.write_bytes(b"fixture")
    response = TemporaryFileResponse(str(path))

    async def send(message):
        await asyncio.sleep(0)

    async def receive():
        return {"type": "http.disconnect"}

    asyncio.run(response({"type": "http", "method": "GET", "headers": [],
                          "asgi": {"spec_version": "2.0"}}, receive, send))
    assert not path.exists()


def test_cancelled_text_generation_waits_for_worker_and_removes_result(tmp_path, monkeypatch):
    from open_terminal.utils import fs

    source = tmp_path / "source.txt"
    source.write_text("content")
    started, finish = threading.Event(), threading.Event()
    outputs = []
    original = fs._build_text_file_response

    def builder(*args):
        started.set()
        assert finish.wait(10)
        response = original(*args)
        outputs.append(response.path)
        return response

    monkeypatch.setattr(fs, "_build_text_file_response", builder)

    async def run():
        task = asyncio.create_task(fs.text_file_response(str(source), "source.txt"))
        assert await asyncio.to_thread(started.wait, 10)
        task.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert len(outputs) == 1
    assert not os.path.exists(outputs[0])


def test_single_user_can_move_and_remove_its_configured_home(tmp_path):
    home = tmp_path / "single"
    home.mkdir()
    (home / "own.txt").write_text("own")
    fs = UserFS(home=str(home))
    moved = tmp_path / "moved"
    asyncio.run(fs.move(str(home), str(moved)))
    assert (moved / "own.txt").read_text() == "own"
    asyncio.run(fs.remove(str(moved)))
    assert not moved.exists()
