from __future__ import annotations

import json

import jsonschema_rs
import pytest

from schemathesis.core.errors import RefResolutionError
from schemathesis.core.jsonschema.resolver import (
    IN_MEMORY_BASE_URI,
    build_registry,
    find_unresolvable_reference,
    load_file,
    make_root_resolver,
    resolve_reference,
    resolve_reference_uri,
)


def test_build_registry_and_root_resolver_for_in_memory_schema():
    schema = {"$defs": {"value": {"type": "string"}}, "$ref": "#/$defs/value"}

    registry = build_registry(schema)
    resolver = make_root_resolver(schema)

    assert isinstance(registry, jsonschema_rs.Registry)
    assert resolver.base_uri == IN_MEMORY_BASE_URI

    next_resolver, resolved = resolve_reference(resolver, "")
    assert resolved["$ref"] == "#/$defs/value"
    assert next_resolver.base_uri == IN_MEMORY_BASE_URI


def test_build_registry_uses_file_retrieval_for_relative_references(tmp_path):
    root = tmp_path / "root.json"
    defs = tmp_path / "defs.json"

    root_schema = {"$ref": "defs.json#/$defs/name"}
    defs_schema = {"$defs": {"name": {"type": "string"}}}

    root.write_text(json.dumps(root_schema))
    defs.write_text(json.dumps(defs_schema))

    resolver = make_root_resolver(root_schema, location=root.as_uri())

    next_resolver, resolved_root = resolve_reference(resolver, "")
    next_resolver, resolved_target = resolve_reference(next_resolver, resolved_root["$ref"])

    assert resolved_target == {"type": "string"}
    assert next_resolver.base_uri == defs.as_uri()


def test_load_file_reads_yaml_document(tmp_path):
    schema_file = tmp_path / "schema.yaml"
    schema_file.write_text("$defs:\n  value:\n    type: string\n")

    document = load_file(str(schema_file))

    assert document == {"$defs": {"value": {"type": "string"}}}


@pytest.mark.parametrize(
    ("base_uri", "reference", "expected"),
    [
        (IN_MEMORY_BASE_URI, "", IN_MEMORY_BASE_URI),
        ("file:///tmp/root.json#/paths/test", "#/$defs/value", "file:///tmp/root.json#/$defs/value"),
        ("file:///tmp/root.json", "defs.json#/$defs/name", "file:///tmp/defs.json#/$defs/name"),
        (
            "https://example.com/schemas/root.json#/properties/value",
            "../defs.json#/$defs/name",
            "https://example.com/defs.json#/$defs/name",
        ),
    ],
)
def test_resolve_reference_uri(base_uri, reference, expected):
    assert resolve_reference_uri(base_uri, reference) == expected


def test_resolve_reference_translates_missing_references_to_ref_resolution_error():
    resolver = make_root_resolver({"type": "object"})

    with pytest.raises(RefResolutionError) as exc:
        resolve_reference(resolver, "https://example.com/missing.json")

    assert exc.value.__notes__ == ["https://example.com/missing.json"]


def test_resolve_reference_to_file_path_with_uri_reserved_characters(tmp_path):
    # Split-file OpenAPI layouts mirror path templates; refs like 'paths/{id}/op.yaml' must resolve.
    target_dir = tmp_path / "paths" / "{id}"
    target_dir.mkdir(parents=True)
    target_file = target_dir / "op.yaml"
    target_file.write_text("$defs:\n  value:\n    type: string\n")

    root_file = tmp_path / "root.yaml"
    root_schema = {"$ref": "paths/{id}/op.yaml#/$defs/value"}
    root_file.write_text(json.dumps(root_schema))

    resolver = make_root_resolver(root_schema, location=root_file.as_uri())
    _, resolved = resolve_reference(resolver, root_schema["$ref"])

    assert resolved == {"type": "string"}


def _find(schema, memo=None):
    document = schema if isinstance(schema, dict) else {}
    return find_unresolvable_reference(schema, make_root_resolver(document), memo if memo is not None else {})


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"properties": {"x": {"$ref": "#/$defs/missing"}}}, "#/$defs/missing"),
        ({"anyOf": [{"type": "string"}, {"$ref": "#/$defs/missing"}]}, "#/$defs/missing"),
        ({"allOf": [{"anyOf": [{"$ref": "#/$defs/missing"}]}]}, "#/$defs/missing"),
        ({"$defs": {"a": {"type": "string"}}, "properties": {"x": {"$ref": "   "}}}, None),
        ({"$defs": {"node": {"properties": {"next": {"$ref": "#/$defs/node"}}}}}, None),
        ({"anyOf": []}, None),
        ({"enum": ["a", 1, None]}, None),
        ({"allOf": [{"type": "string"}, {"type": "object"}]}, None),
        (True, None),
        (False, None),
    ],
    ids=[
        "broken-target",
        "inside-list",
        "inside-nested-list",
        "blank-reference",
        "recursive-reference",
        "empty-list",
        "scalar-items",
        "resolvable-items",
        "boolean-true",
        "boolean-false",
    ],
)
def test_find_unresolvable_reference(schema, expected):
    assert _find(schema) == expected


def test_find_unresolvable_reference_reuses_memoized_failure():
    # The second lookup must reuse the memo rather than walk the shared component again.
    document = {"$defs": {"broken": {"$ref": "#/$defs/gone"}}}
    resolver = make_root_resolver(document)
    memo = {}

    assert find_unresolvable_reference({"$ref": "#/$defs/broken"}, resolver, memo) == "#/$defs/gone"
    assert find_unresolvable_reference({"$ref": "#/$defs/broken"}, resolver, memo) == "#/$defs/gone"


def test_find_unresolvable_reference_reuses_memoized_success():
    memo = {}
    schema = {
        "$defs": {"ok": {"type": "string"}},
        "properties": {"x": {"$ref": "#/$defs/ok"}, "y": {"$ref": "#/$defs/ok"}},
    }

    assert _find(schema, memo) is None
    assert len(memo) == 1
