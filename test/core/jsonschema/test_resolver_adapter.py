from __future__ import annotations

import json

import jsonschema_rs
import pytest

from schemathesis.core.compat import RefResolutionError
from schemathesis.core.jsonschema.resolver import (
    IN_MEMORY_BASE_URI,
    build_registry,
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

    registry = build_registry(root_schema, location=root.as_uri())
    resolver = registry.resolver(root.as_uri())

    assert isinstance(registry, jsonschema_rs.Registry)

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
