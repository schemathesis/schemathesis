from hypothesis import given
from jsonschema import Draft4Validator

from schemathesis.specs.openapi.negative import negative_schema

EXAMPLE = {
    "type": "object",
    "properties": {
        "foo": {"type": "string"},
        "bar": {"type": "integer"},
        "baf": {"type": ["integer"]},
        "baz": {"type": ["array", "object"]},
    },
    "required": [
        "foo",
        "bar",
        "baf",
        "baz",
    ],
}


def test_negative():
    validator = Draft4Validator(EXAMPLE)

    @given(negative_schema(EXAMPLE))
    def test(instance):
        assert not validator.is_valid(instance)

    test()
