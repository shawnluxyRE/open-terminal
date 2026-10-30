"""REQ-002 path-order edge cases through real HTTP and public configuration."""

import json

import pytest

from open_terminal.utils.file_access import (
    PublicRootConfigurationError,
    parse_public_read_roots,
)


def file_request(api, operation, path):
    if operation == "read":
        return api.request("GET", "/files/read", params={"path": path})
    return api.request("POST", "/files/write", json={"path": path, "content": "updated"})


@pytest.mark.parametrize("operation", ["read", "write"])
def test_relative_directory_link_is_followed_before_parent_component(api, operation):
    home = api.homes["alice"]
    (home / "nested").mkdir()
    (home / "deep/inside").mkdir(parents=True)
    lexical_file = home / "nested/marker.txt"
    target_file = home / "deep/marker.txt"
    lexical_file.write_text("lexical decoy")
    target_file.write_text("resolved file")
    (home / "nested/link").symlink_to("../deep/inside", target_is_directory=True)

    response = file_request(api, operation, "nested/link/../marker.txt")

    assert response.status_code == 200, response.text
    if operation == "read":
        assert response.json()["content"] == "resolved file"
    assert target_file.read_text() == ("resolved file" if operation == "read" else "updated")
    assert lexical_file.read_text() == "lexical decoy"


@pytest.mark.parametrize("operation", ["read", "write"])
@pytest.mark.parametrize("entry", ["missing/../marker.txt", "dangling-link"])
def test_unresolvable_intermediate_cannot_collapse_to_an_existing_file(api, operation, entry):
    home = api.homes["alice"]
    marker = home / "marker.txt"
    marker.write_text("unchanged")
    (home / "dangling-link").symlink_to("missing/../marker.txt")

    response = file_request(api, operation, entry)

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "Access denied"}
    assert marker.read_text() == "unchanged"
    assert not (home / "missing").exists()


def test_public_root_parent_component_cannot_hide_a_symlink_ancestor(tmp_path):
    public = tmp_path / "reference"
    public.mkdir()
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "alias").symlink_to(nested, target_is_directory=True)
    declaration = str(tmp_path / "alias") + "/../reference"

    with pytest.raises(PublicRootConfigurationError, match="^Invalid public read roots$"):
        parse_public_read_roots(json.dumps([declaration]))
