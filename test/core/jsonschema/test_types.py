import pytest

from schemathesis.core.jsonschema.types import ANY_TYPE, as_object_schema, get_type, to_json_type_name


@pytest.mark.parametrize(
    "schema,expected",
    [
        (True, ANY_TYPE),
        (False, ANY_TYPE),
        ({}, ANY_TYPE),
        ({"type": "string"}, ["string"]),
        ({"type": ["string", "null"]}, ["null", "string"]),
        ({"type": ["integer", "integer"]}, ["integer"]),
    ],
)
def test_get_type(schema, expected):
    assert get_type(schema) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "null"),
        (True, "boolean"),
        ({}, "object"),
        ([], "array"),
        (1, "integer"),
        (1.5, "number"),
        ("s", "string"),
        (b"bytes", "bytes"),
        (type("Foo", (), {})(), "Foo"),
    ],
)
def test_to_json_type_name(value, expected):
    assert to_json_type_name(value) == expected


@pytest.mark.parametrize(
    ("schema", "expected"),
    [(True, {}), (False, {"not": {}}), ({"type": "string"}, {"type": "string"})],
    ids=["true", "false", "object"],
)
def test_as_object_schema(schema, expected):
    assert as_object_schema(schema) == expected
