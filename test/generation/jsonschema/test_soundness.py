import jsonschema_rs
import pytest
from hypothesis import HealthCheck, given, settings

from schemathesis.generation.jsonschema import StrategyContext
from schemathesis.generation.jsonschema.strategy import from_schema

# One schema per generation path; every generated value must validate against the original schema.
SCHEMAS = [
    # numeric: bounds + multipleOf grid
    {"type": "integer", "minimum": 3, "maximum": 30, "multipleOf": 7},
    {"type": "number", "exclusiveMinimum": 0, "maximum": 1, "multipleOf": 0.25},
    # array: contains / minContains, uniqueItems, tuple prefix + closed tail
    {"type": "array", "contains": {"type": "integer", "minimum": 5}, "minContains": 2, "maxItems": 6},
    {"type": "array", "items": {"type": "boolean"}, "uniqueItems": True},
    {"type": "array", "prefixItems": [{"const": 1}, {"type": "string"}], "items": False},
    # object: required + dependentRequired + dependentSchemas + propertyNames + patternProperties
    {
        "type": "object",
        "required": ["a"],
        "properties": {"a": {"type": "integer"}, "b": {"type": "string"}},
        "dependentRequired": {"a": ["b"]},
        "additionalProperties": False,
    },
    {
        "type": "object",
        "dependentSchemas": {"a": {"required": ["b"]}},
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    },
    {"type": "object", "propertyNames": {"pattern": "^x"}, "additionalProperties": {"type": "integer"}},
    {"type": "object", "patternProperties": {"^n-": {"type": "integer"}}, "additionalProperties": False},
    # allOf residual intersect can't collapse to one leaf
    {"allOf": [{"type": "number"}, {"not": {"multipleOf": 2}}], "maximum": 100},
    # combinators
    {"oneOf": [{"type": "integer", "minimum": 10}, {"type": "string", "minLength": 3}]},
    {"anyOf": [{"const": "x"}, {"type": "integer", "maximum": 0}]},
    # string: secondary and negated patterns
    {"type": "string", "allOf": [{"pattern": "^a"}, {"pattern": "b$"}]},
    {"type": "string", "not": {"pattern": "^a"}},
]


@pytest.mark.parametrize("schema", SCHEMAS, ids=str)
def test_generated_values_validate(schema):
    canonical = jsonschema_rs.canonicalize(schema, inline_budget=0)
    strategy = from_schema(canonical, StrategyContext())
    validator = jsonschema_rs.validator_for(schema)

    @given(strategy)
    @settings(suppress_health_check=list(HealthCheck), deadline=None)
    def check(value):
        assert validator.is_valid(value), f"{schema} produced invalid {value!r}"

    check()
