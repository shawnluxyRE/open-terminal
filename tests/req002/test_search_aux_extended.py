"""R4 regression checks for auxiliary authorization and recursive enumeration."""

import asyncio
import os
import shutil
import subprocess

import pytest
from fastapi import HTTPException

from open_terminal.utils import search
from open_terminal.utils.file_access import FileAccessDenied
from open_terminal.utils.file_compare import CompareRequest, run_comparison
from open_terminal.utils.fs import UserFS
from open_terminal.utils.skills import list_skill_resources
from test_file_http import denied, put


def scoped(api):
    return UserFS("root", str(api.homes["alice"]),
                  public_read_roots=(str(api.public),))


@pytest.mark.parametrize("helper", ["walk", "git", "rg"])
def test_search_helpers_authorize_explicit_root_before_external_tool(api, monkeypatch, helper):
    fs = scoped(api)
    calls = []

    def unexpected_run(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Forbidden root reached a search tool")

    monkeypatch.setattr(search.subprocess, "run", unexpected_run)
    with pytest.raises(FileAccessDenied, match="^Access denied$"):
        if helper == "walk":
            list(search.walk_entries(fs, str(api.homes["bob"])))
        elif helper == "git":
            list(search.search_entries(fs, str(api.homes["bob"])))
        else:
            with search.matching_files("needle", str(api.homes["bob"]), True, fs=fs):
                pytest.fail("Unauthorized search returned a result")
    assert calls == []


def test_git_stale_index_does_not_follow_authorized_directory_link(api):
    root = api.homes["alice"]
    put(root / "indexed/nested.txt", "old indexed content")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "--", "."], check=True, capture_output=True)
    shutil.rmtree(root / "indexed")
    put(root / "real/nested.txt", "live content")
    (root / "indexed").symlink_to(root / "real", target_is_directory=True)

    entries = dict(search.search_entries(scoped(api), str(root)))
    assert str(root / "real/nested.txt") in entries
    assert str(root / "indexed/nested.txt") not in entries


def test_fallback_walk_and_skill_resources_do_not_follow_directory_cycles(api):
    root = api.homes["alice"] / "skill"
    put(root / "data/own.txt", "own")
    (root / "data/cycle").symlink_to(root, target_is_directory=True)
    entries = dict(search.walk_entries(scoped(api), str(root)))
    assert entries[str(root / "data/own.txt")] == "file"
    assert not any("cycle/" in path for path in entries)
    assert asyncio.run(list_skill_resources(scoped(api), str(root))) == ["data/own.txt"]


@pytest.mark.parametrize("operation", ["compare", "archive"])
def test_all_explicit_inputs_are_authorized_before_missing_file_probe(api, operation):
    missing = str(api.homes["alice"] / "missing.txt")
    forbidden = str(api.homes["bob"] / "also-missing.txt")
    payload = ({"original": missing, "revised": forbidden} if operation == "compare"
               else {"paths": [missing, forbidden]})
    denied(api.request("POST", "/files/" + operation, json=payload))


def test_rg_content_candidates_are_authorized_before_scanning(api, monkeypatch, tmp_path):
    # Stand in for the executable protocol, not UserFS or authorization. Include
    # an unexpected forbidden candidate to verify the content phase filters it.
    root = api.homes["alice"]
    own = put(root / "own.txt", "needle own")
    secret = put(api.homes["bob"] / "private.txt", "needle private")
    commands = []

    def tool(command, *, stdout, stderr):
        commands.append(command)
        if "--files" in command:
            stdout.write(os.fsencode(own) + b"\0" + os.fsencode(secret) + b"\0")
        else:
            files = command[command.index("--") + 2:]
            assert files == [str(own)]
            stdout.write(os.fsencode(own) + b"\0")
        stdout.flush()
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(search.shutil, "which", lambda name: "/fixture/rg")
    monkeypatch.setattr(search.subprocess, "run", tool)
    with search.matching_files("needle", str(root), False, fs=scoped(api)) as matches:
        assert matches(str(own))
        assert not matches(str(secret))
    assert len(commands) == 2


@pytest.mark.parametrize("control", ["git-link", "git-pointer", "index-link", "config-include", "inline-worktree", "exclude-link", "ignore-link"])
def test_git_controls_cannot_read_another_users_repository(api, monkeypatch, control):
    root, foreign = api.homes["alice"], api.homes["bob"]
    put(root / "own.txt", "own")
    put(foreign / "foreign-index-name.txt", "private")
    for directory in (root, foreign):
        subprocess.run(["git", "init", "-q", str(directory)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(directory), "add", "."], check=True, capture_output=True)
    if control in ("git-link", "git-pointer"):
        shutil.rmtree(root / ".git")
        if control == "git-link":
            (root / ".git").symlink_to(foreign / ".git", target_is_directory=True)
        else:
            (root / ".git").write_text(f"gitdir: {foreign / '.git'}\n")
    elif control == "index-link":
        (root / ".git/index").unlink()
        (root / ".git/index").symlink_to(foreign / ".git/index")
    elif control == "config-include":
        with (root / ".git/config").open("a") as config:
            config.write(f'\n[include]\n\tpath = {foreign / ".git/config"}\n')
    elif control == "inline-worktree":
        with (root / ".git/config").open("a") as config:
            config.write(f'\n[core] worktree = {foreign}\n')
    elif control == "exclude-link":
        (root / ".git/info/exclude").unlink()
        (root / ".git/info/exclude").symlink_to(foreign / ".git/info/exclude")
    else:
        (root / ".gitignore").symlink_to(foreign / "foreign-index-name.txt")

    def forbidden_git(*args, **kwargs):
        raise AssertionError("Unsafe repository controls reached Git")

    monkeypatch.setattr(search.subprocess, "run", forbidden_git)
    entries = dict(search.search_entries(scoped(api), str(root)))
    assert entries[str(root / "own.txt")] == "file"
    assert not any("foreign-index-name" in path for path in entries)


def test_normal_git_search_retains_ignore_rules_and_ignores_external_git_environment(api, monkeypatch):
    root = api.homes["alice"]
    put(root / "own.txt", "own")
    put(root / "ignored.txt", "ignored")
    put(root / ".gitignore", "ignored.txt\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    monkeypatch.setenv("GIT_DIR", str(api.homes["bob"] / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(api.homes["bob"] / "index"))
    entries = dict(search.search_entries(scoped(api), str(root)))
    assert entries == {str(root / "own.txt"): "file"}


def test_nested_git_ignore_symlink_never_reads_foreign_ignore_content(api):
    root = api.homes["alice"]
    put(root / "nested/own.txt", "own")
    ignore = put(api.homes["bob"] / "secret-ignore.txt", "own.txt\n")
    (root / "nested/.gitignore").symlink_to(ignore)
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    entries = dict(search.search_entries(scoped(api), str(root)))
    assert str(root / "nested/own.txt") in entries
    assert str(root / "nested/.gitignore") not in entries


def test_comparison_os_permission_denial_has_the_same_fixed_response(api):
    root = api.homes["alice"]
    root.chmod(0o755)
    denied_file = put(root / "unreadable.txt", "private content")
    denied_file.chmod(0)
    readable = put(root / "readable.txt", "readable")
    readable.chmod(0o644)
    # The actual worker runs as an unprivileged existing account. Authorization
    # remains real and allows both paths; OS permissions cause the denial.
    fs = UserFS("nobody", str(root))
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(run_comparison(None, CompareRequest(
            original=str(denied_file), revised=str(readable)), fs))
    assert rejected.value.status_code == 403
    assert rejected.value.detail == "Access denied"


def test_real_rg_search_reads_owned_and_public_files_but_skips_private_links(api):
    assert shutil.which("rg"), "This runtime regression requires real ripgrep"
    root = api.homes["alice"]
    own = put(root / "own.txt", "needle own")
    secret = put(api.homes["bob"] / "secret.txt", "needle private")
    public = put(api.public / "manual.txt", "needle public")
    (root / "private-file").symlink_to(secret)
    (root / "private-directory").symlink_to(api.homes["bob"], target_is_directory=True)
    with search.matching_files("needle", str(root), True, fs=scoped(api)) as matches:
        assert matches is not None
        assert matches(str(own))
        assert not matches(str(secret))
        assert not matches(str(root / "private-file"))
    with search.matching_files("needle", str(api.public), True, fs=scoped(api)) as matches:
        assert matches is not None
        assert matches(str(public))
    response = api.request("GET", "/files/matches", params={"query": "needle"})
    assert response.status_code == 200
    assert [item["path"] for item in response.json()["results"]] == [str(own)]


@pytest.mark.parametrize("ignore_name", [".gitignore", ".ignore", ".rgignore"])
def test_rg_never_reads_foreign_nested_ignore_controls(api, monkeypatch, ignore_name):
    assert shutil.which("rg"), "This runtime regression requires real ripgrep"
    root = api.homes["alice"]
    own = put(root / "nested/own.txt", "needle own")
    foreign = put(api.homes["bob"] / "ignore-rules.txt", "own.txt\n")
    (root / "nested" / ignore_name).symlink_to(foreign)

    def forbidden_tool(*args, **kwargs):
        raise AssertionError("Unauthorized ignore controls reached rg")

    monkeypatch.setattr(search.subprocess, "run", forbidden_tool)
    with search.matching_files("needle", str(root), True, fs=scoped(api)) as matches:
        assert matches is None
    # Returning None selects the caller's existing authorized content walker.
    response = api.request("GET", "/files/matches", params={"query": "needle"})
    assert response.status_code == 200
    assert [item["path"] for item in response.json()["results"]] == [str(own)]


def test_real_rg_disables_foreign_git_controls_without_losing_owned_content(api):
    assert shutil.which("rg"), "This runtime regression requires real ripgrep"
    root, foreign = api.homes["alice"], api.homes["bob"]
    own = put(root / "own.txt", "needle own")
    subprocess.run(["git", "init", "-q", str(foreign)], check=True, capture_output=True)
    put(foreign / ".git/info/exclude", "own.txt\n")
    (root / ".git").symlink_to(foreign / ".git", target_is_directory=True)
    with search.matching_files("needle", str(root), True, fs=scoped(api)) as matches:
        assert matches is not None
        assert matches(str(own))
