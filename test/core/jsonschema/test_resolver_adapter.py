from __future__ import annotations

import json

import jsonschema_rs
import pytest
from flask import Flask, Response

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


@pytest.mark.parametrize("directory", ["schemas", "my schemas"], ids=["plain", "percent-encoded"])
def test_build_registry_uses_file_retrieval_for_relative_references(tmp_path, directory):
    (tmp_path / directory).mkdir()
    root = tmp_path / directory / "root.json"
    defs = tmp_path / directory / "defs.json"

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
        (
            "http://example.com/schemas/root.json",
            "//other.com/defs.json#/$defs/name",
            "http://other.com/defs.json#/$defs/name",
        ),
        (
            "file:///tmp/root.json",
            "//example.com/defs.json#/$defs/name",
            "https://example.com/defs.json#/$defs/name",
        ),
        (IN_MEMORY_BASE_URI, "//example.com/defs.json#/$defs/name", "https://example.com/defs.json#/$defs/name"),
    ],
)
def test_resolve_reference_uri(base_uri, reference, expected):
    assert resolve_reference_uri(base_uri, reference) == expected


def test_in_memory_schema_keeps_a_urn_location():
    assert make_root_resolver({}, location="urn:example:root").base_uri == "urn:example:root"


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("#/definitions/missing", "^Pointer '/definitions/missing' does not exist\n"),
        # The OS spells this one: "byte" on POSIX, "character" on Windows.
        ("file\x00.json", "^embedded null (byte|character)\n"),
    ],
    ids=["missing-pointer", "null-byte"],
)
def test_unresolvable_reference_error_message(reference, message):
    with pytest.raises(RefResolutionError, match=message) as exc:
        resolve_reference(make_root_resolver({"type": "object"}), reference)
    assert exc.value.__notes__ == [reference]


def test_reference_without_leading_slash_resolves_as_a_pointer():
    resolver = make_root_resolver({"definitions": {"A": {"type": "string"}}})
    assert resolve_reference(resolver, "#definitions/A")[1] == {"type": "string"}


LEGACY_ANCHOR = {"a": {"id": "#foo", "type": "string"}}


@pytest.mark.parametrize(
    ("definitions", "location"),
    [(LEGACY_ANCHOR, None), ({**LEGACY_ANCHOR, "b": {"$ref": "other.json"}}, "root.json")],
    ids=["in-memory", "file"],
)
def test_legacy_id_anchor_resolves_through_the_registry(tmp_path, definitions, location):
    (tmp_path / "other.json").write_text(json.dumps({"type": "integer"}))
    uri = None if location is None else (tmp_path / location).as_uri()
    resolver = make_root_resolver({"definitions": definitions}, location=uri, draft=jsonschema_rs.Draft4)
    target_resolver, target = resolve_reference(resolver, "#foo")
    assert (target, resolve_reference(target_resolver, "#")[1]) == (
        {"id": "#foo", "type": "string"},
        {"id": "#foo", "type": "string"},
    )


NAMED_DOCUMENTS = {
    "definitions": {
        "Text": {
            "$id": "https://example.invalid/text.json",
            "properties": {"other": {"$ref": "https://example.invalid/other.json"}},
        },
        "Other": {"$id": "https://example.invalid/other.json", "type": "string"},
    }
}


def test_named_subschema_references_another_named_subschema():
    text_resolver, _ = resolve_reference(make_root_resolver(NAMED_DOCUMENTS), "https://example.invalid/text.json")
    assert resolve_reference(text_resolver, "https://example.invalid/other.json")[1] == {
        "$id": "https://example.invalid/other.json",
        "type": "string",
    }


def test_missing_fragment_in_named_subschema_names_the_full_target():
    reference = "https://example.invalid/text.json#/missing"
    with pytest.raises(RefResolutionError) as exc:
        resolve_reference(make_root_resolver(NAMED_DOCUMENTS), reference)
    assert (str(exc.value), exc.value.__notes__) == ("Pointer '/missing' does not exist", [reference])


@pytest.mark.parametrize(
    ("content_type", "body"),
    [("text/html", b"<p>Not found</p>"), ("application/json", b"\n  <!DOCTYPE html><html></html>")],
    ids=["content-type", "body"],
)
def test_remote_html_document_is_a_resolution_error(app_runner, content_type, body):
    app = Flask(__name__)

    @app.route("/schema.json")
    def schema():
        return Response(body, content_type=content_type)

    url = app_runner.openapi_url(app, path="/schema.json")
    with pytest.raises(RefResolutionError, match="^Expected YAML/JSON, got HTML"):
        resolve_reference(make_root_resolver({}), url)


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
        (
            {
                "properties": {"x": {"$ref": "#/$defs/a"}},
                "$defs": {"a": {"properties": {"y": {"$ref": "#/$defs/b"}}}, "b": {"type": "string"}},
            },
            None,
        ),
        (
            {
                "properties": {"x": {"$ref": "#/$defs/b", "items": {"$ref": "#/$defs/missing"}}},
                "$defs": {"b": {"type": "string"}},
            },
            "#/$defs/missing",
        ),
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
        "reference-chain",
        "broken-beside-reference",
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


def test_find_unresolvable_reference_memoizes_every_reference_in_a_cycle():
    memo = {}
    schema = {
        "$defs": {
            "a": {"properties": {"b": {"$ref": "#/$defs/b"}}},
            "b": {"properties": {"c": {"$ref": "#/$defs/c"}}},
            "c": {"properties": {"a": {"$ref": "#/$defs/a"}}},
        }
    }

    assert _find(schema, memo) is None
    assert memo == {
        "urn:schemathesis:root#/$defs/a": None,
        "urn:schemathesis:root#/$defs/b": None,
        "urn:schemathesis:root#/$defs/c": None,
    }


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_unreachable_remote_reference_is_a_resolution_error(scheme):
    resolver = make_root_resolver({})
    with pytest.raises(RefResolutionError):
        resolve_reference(resolver, f"{scheme}://127.0.0.1:1/schema.json#/definitions/User")
