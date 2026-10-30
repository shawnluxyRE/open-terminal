"""REQ-002 AC-006/007/009: public path-policy contracts on real Linux files.

Run in the disposable Docker test image. Only the surrounding filesystem is
isolated: symlinks, traversal and policy decisions use the actual public API.
Expected results describe access rights, not the resolver's implementation.
"""

import json
from types import SimpleNamespace

import pytest

from open_terminal.utils.file_access import (
    FileAccessDenied,
    FileAccessPolicy,
    PublicRootConfigurationError,
    parse_public_read_roots,
)


@pytest.fixture
def tree(tmp_path):
    private = tmp_path / "private"
    alice = private / "alice"
    bob = private / "bob"
    sibling = private / "alice-extra"
    public = tmp_path / "public"
    outside = tmp_path / "unpublished"
    protected = tmp_path / "service"
    for directory in (alice, bob, sibling, public, outside, protected):
        directory.mkdir(parents=True)
        (directory / "same.txt").write_text(directory.name, encoding="utf-8")
    (alice / "folder").mkdir()
    (bob / "folder").mkdir()
    (protected / "nested").mkdir()
    (alice / "own-file").symlink_to(alice / "same.txt")
    (alice / "own-dir").symlink_to(alice / "folder", target_is_directory=True)
    (alice / "bob-file").symlink_to(bob / "same.txt")
    (alice / "bob-dir").symlink_to(bob / "folder", target_is_directory=True)
    (alice / "public-file").symlink_to(public / "same.txt")
    (alice / "public-dir").symlink_to(public, target_is_directory=True)
    (alice / "outside-file").symlink_to(outside / "same.txt")
    (alice / "broken").symlink_to(alice / "missing.txt")
    (alice / "cycle-a").symlink_to(alice / "cycle-b")
    (alice / "cycle-b").symlink_to(alice / "cycle-a")
    (bob / "back-to-alice").symlink_to(alice / "same.txt")
    (public / "bob-file").symlink_to(bob / "same.txt")
    (public / "alice-file").symlink_to(alice / "same.txt")
    (public / "nested").mkdir()
    # Publicness is an administrator declaration; permissive OS modes cannot
    # make a directory public or grant write access through the file API.
    public.chmod(0o777)
    (public / "same.txt").chmod(0o666)
    outside.chmod(0o777)
    return SimpleNamespace(
        root=tmp_path, private=private, alice=alice, bob=bob, sibling=sibling,
        public=public, outside=outside, protected=protected,
        policy=FileAccessPolicy(str(alice), (str(public),)),
    )


@pytest.mark.parametrize("write", [False, True], ids=["read", "write"])
@pytest.mark.parametrize(
    "path,expected",
    [
        ("same.txt", "same.txt"),
        ("folder", "folder"),
        ("folder/../same.txt", "same.txt"),
        ("new/nested/file.txt", "new/nested/file.txt"),
        ("own-file", "same.txt"),
        ("own-dir/new.txt", "folder/new.txt"),
    ],
)
def test_own_paths_resolve_to_authorized_canonical_targets(tree, write, path, expected):
    requested = str(tree.alice / path)
    assert tree.policy.require(requested, write=write) == str(tree.alice / expected)
    assert tree.policy.allows(requested, write=write) is True


@pytest.mark.parametrize("relative", ["same.txt", "nested", "new/file.txt"])
def test_declared_public_paths_are_readable_but_never_writable(tree, relative):
    requested = str(tree.public / relative)
    assert tree.policy.require(requested) == requested
    assert tree.policy.allows(requested) is True
    with pytest.raises(FileAccessDenied, match="^Access denied$"):
        tree.policy.require(requested, write=True)
    assert tree.policy.allows(requested, write=True) is False
    assert (tree.public / "same.txt").read_text(encoding="utf-8") == "public"


@pytest.mark.parametrize("link,expected", [("public-file", "same.txt"), ("public-dir", "")])
def test_own_link_to_public_material_preserves_read_only_rights(tree, link, expected):
    requested = str(tree.alice / link)
    assert tree.policy.require(requested) == str(tree.public / expected)
    assert tree.policy.allows(requested) is True
    with pytest.raises(FileAccessDenied, match="^Access denied$"):
        tree.policy.require(requested, write=True)
    assert tree.policy.allows(requested, write=True) is False


def test_public_link_back_to_own_file_cannot_be_used_for_writes(tree):
    requested = str(tree.public / "alice-file")
    assert tree.policy.require(requested) == str(tree.alice / "same.txt")
    with pytest.raises(FileAccessDenied, match="^Access denied$"):
        tree.policy.require(requested, write=True)
    assert tree.policy.allows(requested, write=True) is False


@pytest.mark.parametrize("write", [False, True], ids=["read", "write"])
def test_link_chain_cannot_cross_other_private_space_and_return_home(tree, write):
    requested = tree.alice / "via-bob"
    requested.symlink_to(tree.bob / "back-to-alice")
    with pytest.raises(FileAccessDenied, match="^Access denied$"):
        tree.policy.require(str(requested), write=write)
    assert tree.policy.allows(str(requested), write=write) is False
    assert (tree.alice / "same.txt").read_text(encoding="utf-8") == "alice"


def test_link_chain_through_public_material_cannot_regain_write_access(tree):
    requested = tree.alice / "via-public"
    requested.symlink_to(tree.public / "alice-file")
    # Each read hop is allowed, but a public hop makes this route read-only
    # even though both the original path and final target are inside home.
    assert tree.policy.require(str(requested)) == str(tree.alice / "same.txt")
    assert tree.policy.allows(str(requested)) is True
    with pytest.raises(FileAccessDenied, match="^Access denied$"):
        tree.policy.require(str(requested), write=True)
    assert tree.policy.allows(str(requested), write=True) is False
    assert (tree.alice / "same.txt").read_text(encoding="utf-8") == "alice"


@pytest.mark.parametrize("write", [False, True], ids=["read", "write"])
@pytest.mark.parametrize(
    "case",
    [
        "other-user", "parent", "home-root", "filesystem-root",
        "sibling-prefix", "dotdot", "foreign-link-back", "private-link",
        "private-directory-link", "public-link-to-private", "outside-link",
        "unpublished-world-readable", "symlink-before-dotdot", "service",
    ],
)
def test_private_and_unpublished_paths_are_denied_without_disclosure(tree, case, write):
    paths = {
        "other-user": str(tree.bob / "same.txt"),
        "parent": str(tree.private),
        "home-root": "/home",
        "filesystem-root": "/",
        "sibling-prefix": str(tree.sibling / "same.txt"),
        "dotdot": str(tree.alice / "../bob/same.txt"),
        "foreign-link-back": str(tree.bob / "back-to-alice"),
        "private-link": str(tree.alice / "bob-file"),
        "private-directory-link": str(tree.alice / "bob-dir/new.txt"),
        "public-link-to-private": str(tree.public / "bob-file"),
        "outside-link": str(tree.alice / "outside-file"),
        "unpublished-world-readable": str(tree.outside / "same.txt"),
        # Resolves to bob/same.txt; normalizing '..' before following the link
        # would incorrectly select alice/same.txt.
        "symlink-before-dotdot": str(tree.alice / "bob-dir/../same.txt"),
        "service": str(tree.protected / "same.txt"),
    }
    requested = paths[case]
    with pytest.raises(FileAccessDenied) as caught:
        tree.policy.require(requested, write=write)
    assert str(caught.value) == "Access denied"
    assert tree.policy.allows(requested, write=write) is False
    assert (tree.bob / "same.txt").read_text(encoding="utf-8") == "bob"
    assert (tree.alice / "same.txt").read_text(encoding="utf-8") == "alice"


@pytest.mark.parametrize(
    "case", ["relative", "empty", "nul", "broken", "cycle", "non-directory"]
)
def test_invalid_or_unresolvable_paths_fail_closed(tree, case):
    requested = {
        "relative": "same.txt",
        "empty": "",
        "nul": str(tree.alice / "invalid\x00.txt"),
        "broken": str(tree.alice / "broken"),
        "cycle": str(tree.alice / "cycle-a"),
        "non-directory": str(tree.alice / "same.txt/child"),
    }[case]
    with pytest.raises(FileAccessDenied, match="^Access denied$"):
        tree.policy.require(requested)
    assert tree.policy.allows(requested) is False


def test_normal_personal_files_never_become_public_to_another_policy(tree):
    bob_policy = FileAccessPolicy(str(tree.bob), (str(tree.public),))
    assert bob_policy.require(str(tree.bob / "same.txt")) == str(tree.bob / "same.txt")
    assert bob_policy.require(str(tree.public / "same.txt")) == str(tree.public / "same.txt")
    with pytest.raises(FileAccessDenied, match="^Access denied$"):
        bob_policy.require(str(tree.alice / "same.txt"))
    # Even a declared public directory's link is evaluated for this requester.
    with pytest.raises(FileAccessDenied, match="^Access denied$"):
        bob_policy.require(str(tree.public / "alice-file"))


@pytest.mark.parametrize("write", [False, True], ids=["read", "write"])
@pytest.mark.parametrize("scope", ["own", "public", "other-user"])
def test_enumeration_predicate_can_be_called_without_require(tree, scope, write):
    root = {"own": tree.alice, "public": tree.public, "other-user": tree.bob}[scope]
    expected = scope == "own" or (scope == "public" and not write)
    assert tree.policy.allows(str(root / "same.txt"), write=write) is expected


def test_empty_public_configuration_exposes_no_external_material(tree):
    policy = FileAccessPolicy(str(tree.alice))
    with pytest.raises(FileAccessDenied, match="^Access denied$"):
        policy.require(str(tree.public / "same.txt"))
    assert policy.allows(str(tree.public / "same.txt")) is False


def parse_roots(tree, value):
    return parse_public_read_roots(
        value,
        private_roots=(str(tree.private),),
        protected_roots=(str(tree.protected),),
    )


def test_public_root_configuration_accepts_empty_list(tree):
    assert parse_roots(tree, "[]") == ()


def test_public_root_configuration_preserves_order_and_deduplicates(tree):
    second = tree.root / "another-public"
    second.mkdir()
    value = json.dumps([str(second), str(tree.public), str(second / "."), str(tree.public)])
    assert parse_roots(tree, value) == (str(second), str(tree.public))


@pytest.mark.parametrize(
    "value",
    ["", " ", "not-json", "null", "{}", '"/public"', "0", "true", "[1]", "[null]", '[""]', '["relative"]'],
)
def test_public_root_configuration_rejects_invalid_shape_and_values(tree, value):
    with pytest.raises(PublicRootConfigurationError) as caught:
        parse_roots(tree, value)
    assert str(tree.root) not in str(caught.value)


@pytest.mark.parametrize(
    "case",
    [
        "root", "private-root", "private-child", "private-ancestor",
        "protected-root", "protected-child", "missing", "file", "symlink",
        "symlink-ancestor",
    ],
)
def test_public_root_configuration_rejects_unsafe_declarations(tree, case):
    public_alias = tree.root / "public-alias"
    public_alias.symlink_to(tree.public, target_is_directory=True)
    candidates = {
        "root": "/",
        "private-root": str(tree.private),
        "private-child": str(tree.alice),
        "private-ancestor": str(tree.root),
        "protected-root": str(tree.protected),
        "protected-child": str(tree.protected / "nested"),
        "missing": str(tree.root / "missing"),
        "file": str(tree.public / "same.txt"),
        "symlink": str(public_alias),
        "symlink-ancestor": str(public_alias / "nested"),
    }
    # A valid first item must not turn a malformed declaration into a partial
    # usable allowlist; the entire administrator setting is rejected.
    with pytest.raises(PublicRootConfigurationError) as caught:
        parse_roots(tree, json.dumps([str(tree.public), candidates[case]]))
    assert str(tree.root) not in str(caught.value)


def test_public_roots_cannot_contain_a_protected_descendant(tree):
    protected_child = tree.public / "service-cache"
    protected_child.mkdir()
    with pytest.raises(PublicRootConfigurationError):
        parse_public_read_roots(
            json.dumps([str(tree.public)]),
            private_roots=(str(tree.private),),
            protected_roots=(str(protected_child),),
        )


def test_path_component_prefix_is_not_a_private_root_overlap(tree):
    distinct = tree.root / "private-docs"
    distinct.mkdir()
    assert parse_roots(tree, json.dumps([str(distinct)])) == (str(distinct),)
