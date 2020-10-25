import jsonschema
from hypothesis import given

from schemathesis.specs.openapi.negative import negative_schema

EXAMPLE = {
    "type": "object",
    "properties": {
        "foo": {"type": "string"},
        "bar": {"type": "integer"},
        "baz": {"type": "array"},
    },
    "required": ["foo", "bar", "baz"],
}


def test_negative():
    validator = jsonschema.validators.validator_for(EXAMPLE)(EXAMPLE)

    @given(negative_schema(EXAMPLE))
    def test(instance):
        assert not validator.is_valid(instance)

    test()
