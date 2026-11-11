from __future__ import annotations

import pytest

from schemathesis.core.text import to_pascal_case, to_snake_case


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("user_id", "UserId"),
        ("api_key", "ApiKey"),
        ("sip_trunk", "SipTrunk"),
        ("full-path", "FullPath"),
        ("user-id", "UserId"),
        ("api-key", "ApiKey"),
        ("sipTrunk", "SipTrunk"),
        ("userId", "UserId"),
        ("accountHolder", "AccountHolder"),
        ("UserProfile", "UserProfile"),
        ("SipTrunk", "SipTrunk"),
        ("API", "API"),
        ("userID", "UserID"),
        ("project", "Project"),
        ("user", "User"),
        ("", ""),
    ],
    ids=[
        "snake",
        "snake-two",
        "snake-three",
        "kebab",
        "kebab-two",
        "kebab-three",
        "camel",
        "camel-two",
        "camel-three",
        "pascal",
        "pascal-two",
        "all-caps",
        "camel-acronym",
        "single",
        "single-two",
        "empty",
    ],
)
def test_to_pascal_case(text, expected):
    assert to_pascal_case(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("fullPath", "full_path"), ("ProjectID", "project_i_d"), ("name", "name")],
    ids=["camel", "acronym", "single"],
)
def test_to_snake_case(text, expected):
    assert to_snake_case(text) == expected
