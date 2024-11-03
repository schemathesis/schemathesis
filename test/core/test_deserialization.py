import pytest

from schemathesis.core.deserialization import deserialize_yaml


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("'1': foo", {"1": "foo"}),
        ("1: foo", {"1": "foo"}),
        ("1: 1", {"1": 1}),
        ("on: off", {"on": False}),
    ],
    ids=["string-key-string-value", "int-key-string-value", "int-key-int-value", "bool-key-bool-value"],
)
def test_deserialize_yaml(value, expected):
    assert deserialize_yaml(value) == expected
