import pytest

from schemathesis.specs.openapi._jsonschema import _should_skip


@pytest.mark.parametrize(
    "value, expected",
    [
        ({"x-nullable": True}, False),
        ({"$ref": "foo"}, False),
        ({"items": True}, True),
        ({"items": {"type": "string"}}, True),
        ({"items": {"$ref": "foo"}}, False),
        ({"items": [{"type": "string"}]}, True),
        ({"items": [{"$ref": "foo"}]}, False),
        ({"properties": {"foo": {"type": "string"}}}, True),
        ({"properties": {"foo": True}}, True),
        ({"properties": {"foo": {"$ref": "bar"}}}, False),
    ],
)
def test_should_skip(value, expected):
    assert _should_skip(value) == expected
