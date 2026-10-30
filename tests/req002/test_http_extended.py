"""R4 HTTP error ordering and chat cwd regression checks."""
import pytest
from test_file_http import denied, put


def test_cwd_uses_authorized_directory_and_remains_separate_per_chat(api):
    root = api.homes["alice"]
    folder = root / "project"
    folder.mkdir()
    put(folder / "own.txt", "own")
    response = api.request("POST", "/files/cwd", json={"path": str(folder)})
    assert response.status_code == 200 and response.json()["cwd"] == str(folder)
    assert api.request("GET", "/files/read", params={"path": "own.txt"}).json()["content"] == "own"
    assert api.request("GET", "/files/cwd", session="second").json()["cwd"] == str(root)
    denied(api.request("POST", "/files/cwd", json={"path": str(api.homes["bob"])}))
    for path in (root / "missing", folder / "own.txt"):
        assert api.request("POST", "/files/cwd", json={"path": str(path)}).status_code == 404
    assert api.request("GET", "/files/cwd").json()["cwd"] == str(folder)


@pytest.mark.parametrize("endpoint", ["read", "view", "grep"])
def test_authorized_missing_files_preserve_not_found(api, endpoint):
    response = api.request("GET", "/files/" + endpoint, params={"path": "missing.txt", "query": "anything"})
    assert response.status_code == 404


def test_move_checks_both_permissions_before_missing_source(api):
    denied(api.request("POST", "/files/move", json={
        "source": "missing.txt", "destination": str(api.homes["bob"] / "new.txt"),
    }))
    assert not (api.homes["bob"] / "new.txt").exists()


def test_public_link_to_home_is_readonly_in_http_listing(api):
    put(api.homes["alice"] / "own.txt", "own")
    link = api.public / "alice"
    link.symlink_to(api.homes["alice"], target_is_directory=True)
    response = api.request("GET", "/files/list", params={"directory": str(link)})
    assert response.status_code == 200
    assert response.json()["writable"] is False
    assert all(not entry["writable"] for entry in response.json()["entries"])
    denied(api.request("POST", "/files/write", json={"path": str(link / "own.txt"), "content": "bad"}))
    assert (api.homes["alice"] / "own.txt").read_text() == "own"
