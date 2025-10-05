import pytest

from schemathesis.specs.openapi.stateful.dependencies import naming


@pytest.mark.parametrize(
    ["word", "expected"],
    [
        ("parties", "party"),
        ("glasses", "glass"),
        ("boxes", "box"),
        ("cars", "car"),
        ("sheep", "sheep"),
    ],
)
def test_to_singular(word, expected):
    assert naming.to_singular(word) == expected


@pytest.mark.parametrize(
    ["word", "expected"],
    [
        ("party", "parties"),
        ("class", "classes"),
        ("bus", "buses"),
        ("car", "cars"),
    ],
)
def test_to_plural(word, expected):
    assert naming.to_plural(word) == expected


@pytest.mark.parametrize(
    ["name", "prefixes", "suffixes", "expected"],
    [
        pytest.param("UserResponse", ["get"], ["response"], "User", id="suffix-only"),
        pytest.param("GetUser", ["get"], ["response"], "User", id="prefix-only"),
        pytest.param("GetUserResponse", ["get"], ["response"], "User", id="prefix-then-suffix-bug"),
        pytest.param("GETUserRESPONSE", ["get"], ["response"], "User", id="preserves-case"),
        pytest.param("User", ["get"], ["response"], "User", id="no-match"),
        pytest.param("CreateUser", ["get"], ["response"], "CreateUser", id="different-prefix"),
        pytest.param("", ["get"], ["response"], "", id="empty-string"),
        pytest.param("get", ["get"], [], "", id="becomes-empty"),
        pytest.param("GetUserResponse", [], [], "GetUserResponse", id="no-affixes"),
        pytest.param("GetUser", ["list", "get", "create"], ["response"], "User", id="first-prefix-wins"),
        pytest.param("UserResponse", ["get"], ["data", "response"], "User", id="first-suffix-wins"),
        pytest.param("  GetUser  ", ["get"], [], "User", id="strips-whitespace"),
        pytest.param("GetGetResponse", ["get"], ["response"], "Get", id="suffix-on-original"),
    ],
)
def test_strip_affixes(name, prefixes, suffixes, expected):
    assert naming.strip_affixes(name, prefixes, suffixes) == expected
