import re

import pytest

from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY
from schemathesis.core.transforms import deepclone
from schemathesis.specs.openapi.negative.mutations import (
    _materialize_targets,
    _propagate_required_path,
    compute_mutation_targets,
)


def test_propagation_writes_required_at_each_ancestor_inline():
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "object", "properties": {"b": {"type": "string"}}},
        },
    }
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    leaf_target = next(s for s in targets if s.schema is schema["properties"]["a"]["properties"]["b"])
    _propagate_required_path(leaf_target.path)
    assert schema["required"] == ["a"]
    assert schema["properties"]["a"]["required"] == ["b"]


def test_propagation_writes_required_through_bundled_refs():
    schema = {
        "$ref": "#/x-bundled/outer",
        BUNDLE_STORAGE_KEY: {
            "outer": {
                "type": "object",
                "properties": {"inner": {"$ref": "#/x-bundled/inner"}},
            },
            "inner": {
                "type": "object",
                "properties": {"leaf": {"type": "string", "pattern": "^x$"}},
            },
        },
    }
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    leaf_target = next(s for s in targets if s.schema is schema[BUNDLE_STORAGE_KEY]["inner"]["properties"]["leaf"])
    _propagate_required_path(leaf_target.path)
    assert schema[BUNDLE_STORAGE_KEY]["outer"]["required"] == ["inner"]
    assert schema[BUNDLE_STORAGE_KEY]["inner"]["required"] == ["leaf"]


def test_propagation_is_idempotent():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    a_target = next(s for s in targets if s.schema is schema["properties"]["a"])
    before = deepclone(schema)
    _propagate_required_path(a_target.path)
    _propagate_required_path(a_target.path)
    assert schema == before


def test_propagation_collapses_oneof_to_chosen_branch():
    schema = {
        "oneOf": [
            {"type": "string"},
            {"type": "object", "properties": {"x": {"type": "integer"}}},
        ],
    }
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    target = next(s for s in targets if s.schema is schema["oneOf"][1]["properties"]["x"])
    _propagate_required_path(target.path)
    assert schema["oneOf"] == [{"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}]


def test_propagation_handles_array_items_min():
    schema = {"type": "array", "items": {"type": "object", "properties": {"x": {"type": "string"}}}}
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    target = next(s for s in targets if s.schema is schema["items"]["properties"]["x"])
    _propagate_required_path(target.path)
    assert schema["minItems"] >= 1
    assert schema["items"]["required"] == ["x"]


def test_propagation_synthesizes_property_for_additional_properties():
    additional = {"type": "string", "pattern": "^a$"}
    schema = {"type": "object", "additionalProperties": additional}
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    target = next(s for s in targets if s.schema is additional)
    _propagate_required_path(target.path)
    assert schema == {
        "type": "object",
        "additionalProperties": additional,
        "properties": {"k": additional},
        "required": ["k"],
    }


def test_propagation_synthesizes_unique_property_when_default_taken():
    # Existing `k` and `k0` mean the synthesizer has to keep searching; the
    # generated property must not clobber an existing slot.
    additional = {"type": "string", "pattern": "^a$"}
    schema = {
        "type": "object",
        "properties": {"k": {"type": "string"}, "k0": {"type": "string"}},
        "additionalProperties": additional,
    }
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    target = next(s for s in targets if s.schema is additional)
    _propagate_required_path(target.path)
    assert schema["properties"] == {"k": {"type": "string"}, "k0": {"type": "string"}, "k1": additional}
    assert schema["required"] == ["k1"]


def test_propagation_synthesizes_pattern_property_for_simple_pattern():
    pattern_schema = {"type": "string", "pattern": "^a$"}
    schema = {"type": "object", "patternProperties": {"^[a-z]+$": pattern_schema}}
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    target = next(s for s in targets if s.schema is pattern_schema)
    _propagate_required_path(target.path)
    assert schema == {
        "type": "object",
        "patternProperties": {"^[a-z]+$": pattern_schema},
        "properties": {"x": pattern_schema},
        "required": ["x"],
    }


def test_propagation_skips_pattern_property_when_no_matching_name_can_be_synthesized():
    # Anchored numeric-with-quantifier patterns can't be turned into a literal
    # property name; the parent stays bare rather than receiving a placeholder
    # that wouldn't match the pattern at runtime.
    pattern_schema = {"type": "string", "pattern": "^a$"}
    schema = {"type": "object", "patternProperties": {r"^\d{4}-\d{2}-\d{2}$": pattern_schema}}
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    target = next(s for s in targets if s.schema is pattern_schema)
    _propagate_required_path(target.path)
    assert schema == {"type": "object", "patternProperties": {r"^\d{4}-\d{2}-\d{2}$": pattern_schema}}


@pytest.mark.parametrize(
    "pattern",
    [
        "^[a-z]+$",
        "^[A-Z]+$",
        "^[a-zA-Z]+$",
        r"^\d+$",
        r"^\w+$",
        "^foo$",
        "^x-",
        "^[a-z][a-z0-9]*$",
        "^[A-Z][a-z]+$",
        "^[a-z]{1,5}$",
        "^_[a-z]+$",
        "^[a-z]+_[a-z]+$",
    ],
)
def test_propagation_synthesizes_property_name_that_matches_pattern(pattern):
    # The synthesized property name must satisfy the patternProperties regex,
    # otherwise the validator wouldn't apply the sub-schema to it at runtime.
    pattern_schema = {"type": "string", "pattern": "^a$"}
    schema = {"type": "object", "patternProperties": {pattern: pattern_schema}}
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    target = next(s for s in targets if s.schema is pattern_schema)
    _propagate_required_path(target.path)
    properties = schema.get("properties")
    assert properties, f"expected a synthesized property for pattern {pattern!r}"
    [(name, _)] = properties.items()
    assert re.search(pattern, name) is not None, f"synthesized name {name!r} does not match pattern {pattern!r}"
    assert schema["required"] == [name]


@pytest.mark.parametrize(
    "pattern",
    [
        # Five exact digits — no candidate is 5 digits long.
        r"^\d{5}$",
        # Invalid regex — synthesizer must skip rather than crash.
        r"^[a-z",
    ],
)
def test_propagation_skips_when_no_candidate_satisfies_pattern(pattern):
    pattern_schema = {"type": "string", "pattern": "^a$"}
    schema = {"type": "object", "patternProperties": {pattern: pattern_schema}}
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    target = next(s for s in targets if s.schema is pattern_schema)
    _propagate_required_path(target.path)
    assert "properties" not in schema
    assert "required" not in schema


def test_propagation_allof_branch_keeps_siblings():
    # `allOf` branches stay conjoined — propagation must not collapse the list.
    schema = {"allOf": [{"type": "object", "properties": {"x": {"type": "string"}}}, {"type": "object"}]}
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    target = next(s for s in targets if s.schema is schema["allOf"][0]["properties"]["x"])
    _propagate_required_path(target.path)
    assert schema == {
        "allOf": [
            {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
            {"type": "object"},
        ],
    }
