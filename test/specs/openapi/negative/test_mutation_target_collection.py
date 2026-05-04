from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY
from schemathesis.core.transforms import deepclone
from schemathesis.specs.openapi.negative.mutations import (
    MAX_WALK_DEPTH,
    WalkStep,
    compute_mutation_targets,
)


def test_inline_object_yields_root_plus_property_targets():
    schema = {"type": "object", "properties": {"a": {"type": "string", "pattern": "^a$"}}}
    descriptors = compute_mutation_targets(schema)
    walks = [d.walk for d in descriptors]
    assert () in walks  # root target
    assert (WalkStep("properties", "a"),) in walks


def test_nested_object_yields_one_descriptor_per_level():
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "object", "properties": {"b": {"type": "integer", "minimum": 0}}},
        },
    }
    descriptors = compute_mutation_targets(schema)
    walks = {d.walk for d in descriptors}
    assert () in walks
    assert (WalkStep("properties", "a"),) in walks
    assert (WalkStep("properties", "a"), WalkStep("properties", "b")) in walks


def test_bundled_ref_followed_via_x_bundled():
    schema = {
        "$ref": "#/x-bundled/inner",
        BUNDLE_STORAGE_KEY: {
            "inner": {"type": "object", "properties": {"x": {"type": "string", "pattern": "^x$"}}},
        },
    }
    descriptors = compute_mutation_targets(schema)
    walks = {d.walk for d in descriptors}
    assert (WalkStep("$ref", "inner"),) in walks
    assert (WalkStep("$ref", "inner"), WalkStep("properties", "x")) in walks


def test_dag_shared_target_yields_two_descriptors():
    schema = {
        "type": "object",
        "required": ["inner", "alt_inner"],
        "properties": {
            "inner": {"$ref": "#/x-bundled/shared"},
            "alt_inner": {"$ref": "#/x-bundled/shared"},
        },
        BUNDLE_STORAGE_KEY: {
            "shared": {"type": "object", "properties": {"leaf": {"type": "string"}}},
        },
    }
    descriptors = compute_mutation_targets(schema)
    walks = {d.walk for d in descriptors}
    assert (WalkStep("properties", "inner"), WalkStep("$ref", "shared")) in walks
    assert (WalkStep("properties", "alt_inner"), WalkStep("$ref", "shared")) in walks
    assert (WalkStep("properties", "inner"), WalkStep("$ref", "shared"), WalkStep("properties", "leaf")) in walks
    assert (WalkStep("properties", "alt_inner"), WalkStep("$ref", "shared"), WalkStep("properties", "leaf")) in walks


def test_oneof_branches_each_become_a_descriptor():
    schema = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    descriptors = compute_mutation_targets(schema)
    walks = {d.walk for d in descriptors}
    assert (WalkStep("oneOf", 0),) in walks
    assert (WalkStep("oneOf", 1),) in walks


def test_compute_does_not_mutate_input():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string", "pattern": "^a$"}},
    }
    snapshot = deepclone(schema)
    compute_mutation_targets(schema)
    assert schema == snapshot


def test_external_ref_yields_no_descriptor():
    # External `$ref` wrappers have no mutable content — operators can't negate
    # `$ref` itself, so emitting a descriptor here would waste a primary slot.
    schema = {"$ref": "https://example.com/Schema.json"}
    assert compute_mutation_targets(schema) == ()


def test_ref_with_sibling_constraints_yields_wrapper_descriptor():
    # OpenAPI 3.1 / JSON Schema 2019-09+ allow `$ref` with sibling keywords; the
    # wrapper's own constraints must be mutable, otherwise schemas like
    # `{"$ref": ..., "minLength": 3}` regress to ignoring the sibling.
    schema = {
        "$ref": "#/x-bundled/inner",
        "minLength": 3,
        BUNDLE_STORAGE_KEY: {"inner": {"type": "string"}},
    }
    descriptors = compute_mutation_targets(schema)
    walks = {d.walk for d in descriptors}
    assert () in walks


def test_cycle_protection_via_ancestor_stack():
    # Cycle terminates the walk; the wrapper at the cycle point yields no
    # descriptor (only `$ref`, nothing mutable). The reachable target above the
    # cycle still yields its own descriptors.
    schema = {
        "$ref": "#/x-bundled/node",
        BUNDLE_STORAGE_KEY: {
            "node": {"type": "object", "properties": {"child": {"$ref": "#/x-bundled/node"}}},
        },
    }
    descriptors = compute_mutation_targets(schema)
    walks = {d.walk for d in descriptors}
    assert (WalkStep("$ref", "node"),) in walks
    # The cyclic descent does NOT emit a descriptor at the wrapper.
    assert (WalkStep("$ref", "node"), WalkStep("properties", "child")) not in walks


def test_walk_terminates_at_max_depth():
    # Adversarial input: a chain longer than MAX_WALK_DEPTH must terminate cleanly without
    # RecursionError, and walks must never reach a depth exceeding the cap.
    schema: dict = {"type": "object"}
    leaf = schema
    chain_length = MAX_WALK_DEPTH + 5
    for _ in range(chain_length):
        child: dict = {"type": "object"}
        leaf["properties"] = {"x": child}
        leaf = child
    descriptors = compute_mutation_targets(schema)
    deepest = max(len(d.walk) for d in descriptors)
    assert deepest <= MAX_WALK_DEPTH, f"walk reached depth {deepest}, exceeding cap {MAX_WALK_DEPTH}"
