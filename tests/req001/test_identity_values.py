"""REQ-001: the upstream identifier is an opaque, case-sensitive value.

These are contract examples, independent of username hashing/allocation. A
system account's name is a valid upstream ID; provisioning must give that ID
its own managed account instead of resolving the literal system account.
"""

import pytest

from open_terminal.utils.identity import InvalidUserIdentity, validate_user_id


@pytest.mark.parametrize(
    "value",
    [
        "a",
        "0",
        "root",
        "service",
        "ABCD",
        "abcd",
        "12345678-abcd-4321-abcd-123456789abc",
        "a.b:c@d-_",
        "a" * 128,
    ],
)
def test_supported_identity_is_returned_verbatim(value):
    """AC-001/002: short IDs and punctuation never require normalization."""
    assert validate_user_id(value) == value


def test_case_distinct_ids_remain_distinct():
    """Case folding would merge two upstream identities and is forbidden."""
    assert validate_user_id("Alice") != validate_user_id("alice")


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        " ",
        " alice",
        "alice ",
        "alice bob",
        "\talice",
        "alice\n",
        "alice\r\nBob",
        "alice\x00",
        "alice\x7f",
        "\u7528\u6237",
        "caf\u00e9",
        "a\u200bb",
        "../alice",
        "alice/bob",
        "alice\\bob",
        "alice,bob",
        "-alice",
        "_alice",
        ".alice",
        ":alice",
        "@alice",
        "a" * 129,
        1,
        False,
        b"alice",
        ["alice"],
        {"id": "alice"},
    ],
)
def test_unsupported_identity_has_a_domain_error(value):
    """AC-002/003: reject input, without trim, fallback, or coercion."""
    with pytest.raises(InvalidUserIdentity):
        validate_user_id(value)
