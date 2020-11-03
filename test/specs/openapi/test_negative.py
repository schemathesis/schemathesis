import pytest
from hypothesis import given, settings
from jsonschema import Draft4Validator

from schemathesis.specs.openapi.negative import negative_schema

OBJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "foo": {"type": "string"},
        "bar": {"type": "integer"},
        "baf": {"type": ["integer"]},
        "baz": {"type": ["array", "object"]},
        "bad": {},
    },
    "required": [
        "foo",
        "bar",
        "baf",
        "baz",
    ],
}
ARRAY_SCHEMA = {"type": "array", "items": OBJECT_SCHEMA}
EMPTY_OBJECT_SCHEMA = {
    "type": "object",
}
INTEGER_SCHEMA = {
    "type": "integer",
}


@pytest.mark.parametrize("schema", (OBJECT_SCHEMA, EMPTY_OBJECT_SCHEMA, ARRAY_SCHEMA, INTEGER_SCHEMA))
def test_negative(schema):
    validator = Draft4Validator(schema)

    @given(negative_schema(schema, parameter="query", custom_formats={}))
    @settings(max_examples=10)
    def test(instance):
        assert not validator.is_valid(instance)

    test()
