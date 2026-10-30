"""REQ-002 public enumeration remains specific to each requesting account."""

import shutil
import subprocess

import pytest

from test_file_http import denied, put


@pytest.mark.parametrize("endpoint", ["list", "grep", "glob", "search", "matches"])
@pytest.mark.parametrize("git_repository", [False, True])
def test_public_search_links_are_filtered_for_each_request_user(api, endpoint, git_repository):
    public = api.public
    put(public / "needle-shared.txt", "needle shared")
    for user in ("alice", "bob"):
        private = put(api.homes[user] / "personal.txt", "needle private " + user)
        (public / ("needle-" + user + ".txt")).symlink_to(private)
    if git_repository:
        subprocess.run(["git", "init", "-q", str(public)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(public), "add", "."], check=True, capture_output=True)

    params = {"directory" if endpoint == "list" else "path": str(public)}
    if endpoint == "glob":
        params["pattern"] = "needle*"
    elif endpoint != "list":
        params["query"] = "needle"

    for user, other in (("alice", "bob"), ("bob", "alice")):
        response = api.request("GET", "/files/" + endpoint, user=user, params=params)
        assert response.status_code == 200, response.text
        assert "needle-shared.txt" in response.text
        assert "needle-" + user + ".txt" in response.text
        assert "needle-" + other + ".txt" not in response.text
        assert "needle private " + other not in response.text
        assert str(api.homes[other]) not in response.text
        if endpoint == "list":
            assert response.json()["writable"] is False
            assert all(not entry["writable"] for entry in response.json()["entries"])


def test_public_skill_resources_are_authorized_per_requesting_user(api):
    shared = api.public / "shared-skill"
    put(shared / "SKILL.md", "---\nname: shared\ndescription: Shared fixture\n---\nShared body\n")
    put(shared / "common.txt", "shared reference")
    for user in ("alice", "bob"):
        private = put(api.homes[user] / "personal.txt", "private " + user)
        (shared / (user + ".txt")).symlink_to(private)
        skill_root = api.homes[user] / ".agents/skills"
        skill_root.mkdir(parents=True)
        (skill_root / "shared").symlink_to(shared, target_is_directory=True)

    for user, other in (("alice", "bob"), ("bob", "alice")):
        listing = api.request("GET", "/skills", user=user)
        assert listing.status_code == 200, listing.text
        assert [entry["name"] for entry in listing.json()] == ["shared"]
        response = api.request("GET", "/skills/read", user=user, params={"name": "shared"})
        assert response.status_code == 200, response.text
        assert response.json()["content"] == "Shared body"
        assert set(response.json()["resources"]) == {"common.txt", user + ".txt"}
        source = api.homes[user] / ".agents/skills/shared"
        own = api.request("GET", "/files/read", user=user, params={"path": str(source / (user + ".txt"))})
        assert own.status_code == 200 and own.json()["content"] == "private " + user
        denied(api.request("GET", "/files/read", user=user, params={"path": str(source / (other + ".txt"))}))
        denied(api.request("POST", "/files/write", user=user,
                           json={"path": str(source / (user + ".txt")), "content": "changed"}))
        assert (api.homes[user] / "personal.txt").read_text() == "private " + user


@pytest.mark.parametrize("use_ripgrep", [True, False])
def test_owned_git_content_search_keeps_ranking_pagination_and_ignore_rules(api, monkeypatch, use_ripgrep):
    from open_terminal.utils import search

    assert shutil.which("rg"), "The real-rg branch requires the disposable test image"
    if not use_ripgrep:
        original_which = search.shutil.which
        monkeypatch.setattr(search.shutil, "which", lambda name: None if name == "rg" else original_which(name))
    root = api.homes["alice"] / "project"
    put(root / "needle", "exact name")
    put(root / "needle-prefix.txt", "prefix name")
    put(root / "xneedle.txt", "substring name")
    put(root / "content.txt", "\U0001f600 needle payload\n")
    put(root / "needle-ignored.txt", "needle ignored")
    put(root / ".needle-hidden.txt", "needle hidden")
    put(root / ".gitignore", "needle-ignored.txt\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)

    pages = []
    for offset in (0, 2):
        response = api.request("GET", "/files/matches", params={
            "path": str(root), "query": "needle", "offset": offset, "limit": 2,
        })
        assert response.status_code == 200, response.text
        pages.append(response.json())
    assert pages[0]["next_offset"] == 2
    assert pages[1]["next_offset"] is None
    results = pages[0]["results"] + pages[1]["results"]
    assert [item["name"] for item in results] == ["needle", "needle-prefix.txt", "xneedle.txt", "content.txt"]
    assert results[-1]["name_match"] is False
    assert results[-1]["content_matches"] == [{"line": 1, "column": 4, "text": "\U0001f600 needle payload"}]
    hidden = api.request("GET", "/files/matches", params={
        "path": str(root), "query": "needle", "show_hidden": True,
    })
    assert hidden.status_code == 200, hidden.text
    names = {item["name"] for item in hidden.json()["results"]}
    assert ".needle-hidden.txt" in names
    assert "needle-ignored.txt" not in names


def test_owned_skill_root_and_manifest_links_keep_normal_discovery(api):
    home = api.homes["alice"]
    library = home / "skill-library"
    put(home / "manifest.md", "---\nname: linked\ndescription: Linked fixture\n---\nLinked body\n")
    put(home / "resource.txt", "owned reference")
    put(library / "linked/nested/example.txt", "nested example")
    (library / "linked/SKILL.md").symlink_to(home / "manifest.md")
    (library / "linked/reference.txt").symlink_to(home / "resource.txt")
    (home / ".agents").mkdir()
    (home / ".agents/skills").symlink_to(library, target_is_directory=True)

    listing = api.request("GET", "/skills")
    assert listing.status_code == 200, listing.text
    assert [item["name"] for item in listing.json()] == ["linked"]
    response = api.request("GET", "/skills/read", params={"name": "linked"})
    assert response.status_code == 200, response.text
    assert response.json()["content"] == "Linked body"
    assert response.json()["resources"] == ["nested/example.txt", "reference.txt"]
    resource = api.request("GET", "/files/read", params={
        "path": str(home / ".agents/skills/linked/reference.txt"),
    })
    assert resource.status_code == 200 and resource.json()["content"] == "owned reference"
