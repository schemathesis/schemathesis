import pytest
import yaml

from src.schemathesis.utils import StringDatesYAMLLoader


@pytest.mark.parametrize(
    "value, expected",
    (
        ("'1': foo", {"1": "foo"}),
        ("1: foo", {"1": "foo"}),
        ("1: 1", {"1": 1}),
        ("on: off", {"on": False}),
    ),
    ids=["string-key-string-value", "int-key-string-value", "int-key-int-value", "bool-key-bool-value"],
)
def test_parse(value, expected):
    assert yaml.load(value, StringDatesYAMLLoader) == expected
