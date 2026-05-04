from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY
from schemathesis.specs.openapi.negative.mutations import (
    PathStep,
    WalkStep,
    _materialize_targets,
    compute_mutation_targets,
)


def test_materialize_inline_schema_yields_resolved_parents():
    schema = {"type": "object", "properties": {"x": {"type": "string", "pattern": "^a$"}}}
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    by_walk = {d.walk: target for d, target in zip(descriptors, targets, strict=True)}
    leaf = by_walk[(WalkStep("properties", "x"),)]
    assert leaf.path == (PathStep(parent=schema, keyword="properties", selector="x"),)
    assert leaf.schema is schema["properties"]["x"]


def test_materialize_bundled_ref_yields_target_dict_as_schema():
    schema = {
        "$ref": "#/x-bundled/inner",
        BUNDLE_STORAGE_KEY: {"inner": {"type": "object", "properties": {"x": {"type": "string"}}}},
    }
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    by_walk = {d.walk: target for d, target in zip(descriptors, targets, strict=True)}
    inner_target = by_walk[(WalkStep("$ref", "inner"),)]
    assert inner_target.schema is schema[BUNDLE_STORAGE_KEY]["inner"]


def test_materialize_dag_yields_distinct_targets_pointing_to_same_dict():
    schema = {
        "type": "object",
        "properties": {
            "inner": {"$ref": "#/x-bundled/shared"},
            "alt_inner": {"$ref": "#/x-bundled/shared"},
        },
        BUNDLE_STORAGE_KEY: {
            "shared": {"type": "object", "properties": {"leaf": {"type": "string"}}},
        },
    }
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    leaf_targets = [
        s for d, s in zip(descriptors, targets, strict=True) if d.walk and d.walk[-1] == WalkStep("properties", "leaf")
    ]
    assert len(leaf_targets) == 2
    assert leaf_targets[0].schema is leaf_targets[1].schema
    assert leaf_targets[0].path != leaf_targets[1].path


def test_materialize_skips_descriptors_when_replay_schema_diverges():
    # When error-feedback adjustments transform the schema between strategy build
    # and case generation, a recorded `properties X` walk may land on a parent that
    # no longer has `properties`. Skip the descriptor instead of crashing.
    descriptors_schema = {
        "type": "object",
        "properties": {
            "outer": {
                "type": "object",
                "properties": {"leaf": {"type": "string", "pattern": "^a$"}},
            }
        },
    }
    descriptors = compute_mutation_targets(descriptors_schema)
    leaf_walk = (WalkStep("properties", "outer"), WalkStep("properties", "leaf"))
    assert any(d.walk == leaf_walk for d in descriptors)

    # A divergent replay target where `properties.outer` lacks `properties`.
    replay_schema = {
        "type": "object",
        "properties": {"outer": {"type": "object"}},
    }
    targets = _materialize_targets(replay_schema, descriptors)
    target_walks = [tuple((step.keyword, step.selector) for step in s.path) for s in targets]
    # Root and `properties.outer` survive; the deeper `properties.leaf` is dropped.
    assert ("properties", "outer") in [w[-1] for w in target_walks if w]
    assert all(("properties", "leaf") != w[-1] for w in target_walks if w)


def test_materialize_ref_with_siblings_keeps_wrapper_not_target():
    # OpenAPI 3.1 sibling-bearing `{$ref, minLength}` must materialize to the
    # wrapper itself so the sibling can be mutated; an unconditional post-walk
    # dereference would silently swap in the referenced target and lose the
    # sibling constraint.
    schema = {
        "$ref": "#/x-bundled/inner",
        "minLength": 3,
        BUNDLE_STORAGE_KEY: {"inner": {"type": "string"}},
    }
    descriptors = compute_mutation_targets(schema)
    targets = _materialize_targets(schema, descriptors)
    by_walk = {d.walk: target for d, target in zip(descriptors, targets, strict=True)}
    wrapper_target = by_walk[()]
    assert wrapper_target.schema is schema  # not the dereferenced inner


def test_materialize_skips_descriptors_with_missing_bundle_target():
    # A descriptor referencing a bundled target absent from the replay schema's
    # bundle map should be dropped, not raise KeyError.
    descriptors_schema = {
        "$ref": "#/x-bundled/inner",
        BUNDLE_STORAGE_KEY: {"inner": {"type": "object", "properties": {"x": {"type": "string"}}}},
    }
    descriptors = compute_mutation_targets(descriptors_schema)
    assert descriptors
    replay_schema = {"$ref": "#/x-bundled/inner", BUNDLE_STORAGE_KEY: {}}
    # Every descriptor's first hop is `$ref inner` — none survive, but no crash.
    assert _materialize_targets(replay_schema, descriptors) == []
