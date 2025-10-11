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
