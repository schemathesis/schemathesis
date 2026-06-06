import jsonschema_rs
import pytest
from hypothesis import HealthCheck, given, settings

from schemathesis.config._generation import GenerationConfig
from schemathesis.core.errors import InvalidRegexPattern, InvalidSchema
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation.value import GeneratedValue
from schemathesis.specs.openapi._hypothesis import make_negative_strategy, make_positive_strategy


def _positive(schema):
    return make_positive_strategy(
        schema, "op", ParameterLocation.BODY, "application/json", GenerationConfig(), jsonschema_rs.Draft202012Validator
    )


def _negative(schema):
    return make_negative_strategy(
        schema, "op", ParameterLocation.BODY, "application/json", GenerationConfig(), jsonschema_rs.Draft202012Validator
    )


def test_meta_invalid_schema_surfaces_validation_error():
    # Meta-validation failure carries a location -> structured `ValidationError`.
    with pytest.raises(jsonschema_rs.ValidationError):
        _positive({"type": "int"}).example()


def test_malformed_schema_surfaces_invalid_schema():
    # Non-meta-validation canonicalize failures must become a clean `InvalidSchema`, not a raw error.
    with pytest.raises(InvalidSchema):
        _positive(42).example()


def test_invalid_pattern_surfaces_invalid_regex_pattern():
    with pytest.raises(InvalidRegexPattern):
        _positive({"pattern": "["}).example()

SCHEMAS = [
    {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "name": {"type": "string", "minLength": 1}},
        "required": ["id"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {"node": {"$ref": "#/x-bundled/Node"}},
        "required": ["node"],
        "x-bundled": {
            "Node": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "next": {"$ref": "#/x-bundled/Node"}},
                "required": ["name"],
                "additionalProperties": False,
            }
        },
    },
    {"type": "object", "properties": {"email": {"type": "string", "format": "email"}}, "required": ["email"]},
]


@pytest.mark.parametrize("schema", SCHEMAS, ids=str)
def test_positive_strategy_uses_in_tree_generator(schema):
    strategy = make_positive_strategy(
        schema, "op", ParameterLocation.BODY, "application/json", GenerationConfig(), jsonschema_rs.Draft202012Validator
    )
    validator = jsonschema_rs.validator_for(schema)

    @given(strategy)
    @settings(max_examples=30, suppress_health_check=list(HealthCheck), deadline=None)
    def check(value):
        assert validator.is_valid(value), f"{schema} produced invalid {value!r}"

    check()


def test_negative_strategy_handles_recursive_refs():
    # Negative generation must resolve recursive `#/x-bundled/` refs without erroring.
    schema = SCHEMAS[1]
    validator = jsonschema_rs.validator_for(schema)

    @given(_negative(schema))
    @settings(max_examples=30, suppress_health_check=list(HealthCheck), deadline=None)
    def check(generated):
        assert isinstance(generated, GeneratedValue)
        value = generated.value
        # Syntax-fuzzing emits raw bytes; those are invalid by construction.
        if isinstance(value, bytes):
            return
        assert not validator.is_valid(value), f"{schema} produced valid {value!r}"

    check()
