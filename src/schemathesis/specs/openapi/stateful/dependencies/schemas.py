from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from schemathesis.core.jsonschema import ALL_KEYWORDS
from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY, bundle
from schemathesis.core.jsonschema.types import JsonSchema, JsonSchemaObject, get_type
from schemathesis.core.transforms import encode_pointer
from schemathesis.specs.openapi.adapter.parameters import resource_name_from_ref
from schemathesis.specs.openapi.adapter.references import maybe_resolve
from schemathesis.specs.openapi.stateful.dependencies import naming

if TYPE_CHECKING:
    from schemathesis.core.compat import RefResolver

ROOT_POINTER = "/"
SCHEMA_KEYS = frozenset(
    {
        "propertyNames",
        "contains",
        "if",
        "items",
        "oneOf",
        "anyOf",
        "additionalProperties",
        "then",
        "else",
        "not",
        "additionalItems",
        "allOf",
    }
)
SCHEMA_OBJECT_KEYS = frozenset({"dependencies", "properties", "patternProperties"})


def resolve_all_refs(schema: JsonSchemaObject) -> dict[str, Any]:
    if not schema:
        return schema
    bundled = schema.get(BUNDLE_STORAGE_KEY, {})

    resolved_cache: dict[str, dict[str, Any]] = {}

    def resolve(ref: str) -> dict[str, Any]:
        # All references here are bundled, therefore it is safe to avoid full reference resolving
        if ref in resolved_cache:
            return resolved_cache[ref]
        key = ref.split("/")[-1]
        # No clone needed, as it will be cloned inside `merged`
        result = resolve_all_refs_inner(bundled[key], resolve=resolve)
        resolved_cache[ref] = result
        return result

    return resolve_all_refs_inner(schema, resolve=resolve)


def resolve_all_refs_inner(schema: JsonSchema, *, resolve: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    from hypothesis_jsonschema._canonicalise import merged

    if schema is True:
        return {}
    if schema is False:
        return {"not": {}}
    if not schema:
        return schema

    reference = schema.get("$ref")
    if reference is not None:
        resolved = resolve(reference)
        if len(schema) == 1 or (len(schema) == 2 and BUNDLE_STORAGE_KEY in schema):
            return resolved
        del schema["$ref"]
        schema.pop(BUNDLE_STORAGE_KEY, None)
        schema.pop("example", None)
        return merged([resolve_all_refs_inner(schema, resolve=resolve), resolved])

    for key, value in schema.items():
        if key in SCHEMA_KEYS:
            if isinstance(value, list):
                schema[key] = [resolve_all_refs_inner(v, resolve=resolve) if isinstance(v, dict) else v for v in value]
            elif isinstance(value, dict):
                schema[key] = resolve_all_refs_inner(value, resolve=resolve)
        if key in SCHEMA_OBJECT_KEYS:
            schema[key] = {
                k: resolve_all_refs_inner(v, resolve=resolve) if isinstance(v, dict) else v for k, v in value.items()
            }
    return schema


def canonicalize(schema: dict[str, Any], resolver: RefResolver) -> Mapping[str, Any]:
    """Transform the input schema into its canonical-ish form."""
    from hypothesis_jsonschema._canonicalise import canonicalish

    # Canonicalisation in `hypothesis_jsonschema` requires all references to be resovable and non-recursive
    # On the Schemathesis side bundling solves this problem
    bundled = bundle(schema, resolver, inline_recursive=True).schema
    canonicalized = canonicalish(bundled)
    resolved = resolve_all_refs(canonicalized)
    resolved.pop(BUNDLE_STORAGE_KEY, None)
    if "allOf" in resolved or "anyOf" in resolved or "oneOf" in resolved:
        return canonicalish(resolved)
    return resolved


def try_unwrap_composition(schema: Mapping[str, Any], resolver: RefResolver) -> Mapping[str, Any]:
    """Unwrap oneOf/anyOf if we can safely extract a single schema."""
    keys = ("anyOf", "oneOf")
    composition_key = None
    for key in keys:
        if key in schema:
            composition_key = key
            break

    if composition_key is None:
        return schema

    alternatives = schema[composition_key]

    if not isinstance(alternatives, list):
        return schema

    # Filter to interesting alternatives
    interesting = _filter_composition_alternatives(alternatives, resolver)

    # If no interesting alternatives, return original
    if not interesting:
        return schema

    # If exactly one interesting alternative, unwrap it
    if len(interesting) == 1:
        return interesting[0]

    # Pick the first one
    # TODO: Support multiple alternatives
    return interesting[0]


def try_unwrap_all_of(schema: Mapping[str, Any]) -> Mapping[str, Any]:
    alternatives = schema.get("allOf")
    if not isinstance(alternatives, list):
        return schema

    interesting = []

    for subschema in alternatives:
        if isinstance(subschema, dict) and _is_interesting_schema(subschema):
            interesting.append(subschema)

    if len(interesting) == 1:
        return interesting[0]
    return schema


def _filter_composition_alternatives(alternatives: list[dict], resolver: RefResolver) -> list[dict]:
    """Filter oneOf/anyOf alternatives to keep only interesting schemas."""
    interesting = []

    for alt_schema in alternatives:
        _, resolved = maybe_resolve(alt_schema, resolver, "")

        if _is_interesting_schema(resolved):
            # Keep original (with $ref)
            interesting.append(alt_schema)

    return interesting


def _is_interesting_schema(schema: Mapping[str, Any]) -> bool:
    """Check if a schema represents interesting structured data."""
    # Has $ref - definitely interesting (references a named schema)
    if "$ref" in schema:
        return True

    ty = schema.get("type")

    # Primitives are not interesting
    if ty in {"string", "number", "integer", "boolean", "null"}:
        return False

    # Arrays - check items
    if ty == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            return False
        # Recursively check if items are interesting
        return _is_interesting_schema(items)

    # allOf/anyOf/oneOf - interesting (composition)
    if any(key in schema for key in ["allOf", "anyOf", "oneOf"]):
        return True

    # Objects (or untyped) - check if they have any keywords
    return bool(set(schema).intersection(ALL_KEYWORDS))


@dataclass
class UnwrappedSchema:
    """Result of wrapper pattern detection."""

    pointer: str
    schema: Mapping[str, Any]
    ref: str | None

    __slots__ = ("pointer", "schema", "ref")


def unwrap_schema(
    schema: Mapping[str, Any], path: str, parent_ref: str | None, resolver: RefResolver
) -> UnwrappedSchema:
    # Array at root
    if schema.get("type") == "array":
        return UnwrappedSchema(pointer="/", schema=schema, ref=None)

    properties = schema.get("properties", {})

    # HAL _embedded (Spring-specific)
    hal_field = _detect_hal_embedded(schema)
    if hal_field:
        embedded_schema = properties["_embedded"]
        _, resolved_embedded = maybe_resolve(embedded_schema, resolver, "")
        resource_schema = resolved_embedded.get("properties", {}).get(hal_field, {})
        _, resolved_resource = maybe_resolve(resource_schema, resolver, "")

        return UnwrappedSchema(
            pointer=f"/_embedded/{encode_pointer(hal_field)}", schema=resolved_resource, ref=resource_schema.get("$ref")
        )

    # Pagination wrapper
    array_field = _is_pagination_wrapper(schema=schema, path=path, parent_ref=parent_ref, resolver=resolver)
    if array_field:
        array_schema = properties[array_field]
        _, resolved = maybe_resolve(array_schema, resolver, "")
        pointer = f"/{encode_pointer(array_field)}"

        uses_parent_ref = False
        # Try to unwrap one more time
        if resolved.get("type") == "array" or "items" in resolved:
            nested_items = resolved.get("items")
            if isinstance(nested_items, dict):
                _, resolved_items = maybe_resolve(nested_items, resolver, "")
                external_tag = _detect_externally_tagged_pattern(resolved_items, path, parent_ref)
                if external_tag:
                    external_tag_, uses_parent_ref = external_tag
                    nested_properties = resolved_items["properties"][external_tag_]
                    _, resolved = maybe_resolve(nested_properties, resolver, "")
                    pointer += f"/{encode_pointer(external_tag_)}"

        ref = parent_ref if uses_parent_ref else array_schema.get("$ref")
        return UnwrappedSchema(pointer=pointer, schema=resolved, ref=array_schema.get("$ref"))

    # External tag
    external_tag = _detect_externally_tagged_pattern(schema, path, parent_ref)
    if external_tag:
        external_tag_, uses_parent_ref = external_tag
        tagged_schema = properties[external_tag_]
        _, resolved_tagged = maybe_resolve(tagged_schema, resolver, "")

        resolved = try_unwrap_all_of(resolved_tagged)
        ref = (
            parent_ref
            if uses_parent_ref
            else resolved.get("$ref") or resolved_tagged.get("$ref") or tagged_schema.get("$ref")
        )

        _, resolved = maybe_resolve(resolved, resolver, "")
        return UnwrappedSchema(pointer=f"/{encode_pointer(external_tag_)}", schema=resolved, ref=ref)

    # No wrapper - single object at root
    return UnwrappedSchema(pointer="/", schema=schema, ref=schema.get("$ref"))


def _detect_hal_embedded(schema: Mapping[str, Any]) -> str | None:
    """Detect HAL _embedded pattern.

    Spring Data REST uses: {_embedded: {users: [...]}}
    """
    properties = schema.get("properties", {})
    embedded = properties.get("_embedded")

    if not isinstance(embedded, dict):
        return None

    embedded_properties = embedded.get("properties", {})

    # Find array properties in _embedded
    for name, subschema in embedded_properties.items():
        if isinstance(subschema, dict) and subschema.get("type") == "array":
            # Found array in _embedded
            return name

    return None


def _is_pagination_wrapper(
    schema: Mapping[str, Any], path: str, parent_ref: str | None, resolver: RefResolver
) -> str | None:
    """Detect if schema is a pagination wrapper."""
    properties = schema.get("properties", {})

    if not properties:
        return None

    metadata_fields = frozenset(["links", "errors"])

    # Find array properties
    arrays = []
    for name, subschema in properties.items():
        if name in metadata_fields:
            continue
        if isinstance(subschema, dict):
            _, subschema = maybe_resolve(subschema, resolver, "")
            if subschema.get("type") == "array":
                arrays.append(name)

    # Must have exactly one array property
    if len(arrays) != 1:
        return None

    array_field = arrays[0]

    # Check if array field name matches common patterns
    common_data_fields = {"data", "items", "results", "value", "content", "elements", "records", "list"}

    if parent_ref:
        resource_name = resource_name_from_ref(parent_ref)
        resource_name = naming.strip_affixes(resource_name, ["get", "create", "list", "delete"], ["response"])
        common_data_fields.add(resource_name.lower())

    if array_field.lower() not in common_data_fields:
        # Check if field name matches resource-specific pattern
        # Example: path="/items/runner-groups" -> resource="RunnerGroup" -> "runner_groups"
        resource_name_from_path = naming.from_path(path)
        if resource_name_from_path is None:
            return None

        candidate = naming.to_plural(naming.to_snake_case(resource_name_from_path))
        if array_field.lower() != candidate:
            # Field name doesn't match resource pattern
            return None

    # Check for pagination metadata indicators
    others = [p for p in properties if p != array_field]

    pagination_indicators = {
        "count",
        "total",
        "totalcount",
        "total_count",
        "totalelements",
        "total_elements",
        "page",
        "pagenumber",
        "page_number",
        "currentpage",
        "current_page",
        "next",
        "previous",
        "prev",
        "nextpage",
        "prevpage",
        "nextpageurl",
        "prevpageurl",
        "next_page_url",
        "prev_page_url",
        "next_page_token",
        "nextpagetoken",
        "cursor",
        "nextcursor",
        "next_cursor",
        "nextlink",
        "next_link",
        "endcursor",
        "hasmore",
        "has_more",
        "hasnextpage",
        "haspreviouspage",
        "pagesize",
        "page_size",
        "perpage",
        "per_page",
        "limit",
        "size",
        "pageinfo",
        "page_info",
        "pagination",
        "links",
        "meta",
    }

    # Check if any other property looks like pagination metadata
    has_pagination_metadata = any(
        prop.lower().replace("_", "").replace("-", "") in pagination_indicators for prop in others
    )

    # Either there is pagination metadata or the wrapper has just items + some other field which is likely an unrecognized metadata
    if has_pagination_metadata or len(properties) <= 2:
        return array_field

    return None


def _detect_externally_tagged_pattern(
    schema: Mapping[str, Any], path: str, parent_ref: str | None
) -> tuple[str, bool] | None:
    """Detect externally tagged resource pattern.

    Pattern: {ResourceName: [...]} or {resourceName: [...]}

    Examples:
        - GET /merchants -> {"Merchants": [...]}
        - GET /users -> {"Users": [...]} or {"users": [...]}

    """
    properties = schema.get("properties", {})

    if not properties:
        return None

    resource_name = naming.from_path(path)

    if not resource_name:
        return None

    # For example, for `DataRequest`:
    possible_names = {
        # `datarequest`
        resource_name.lower(),
        # `datarequests`
        naming.to_plural(resource_name.lower()),
        # `data_request`
        naming.to_snake_case(resource_name),
    }
    parent_names = set()
    if parent_ref is not None:
        maybe_resource_name = resource_name_from_ref(parent_ref)
        parent_names.add(naming.to_plural(maybe_resource_name.lower()))
        parent_names.add(naming.to_snake_case(maybe_resource_name))
        possible_names = possible_names.union(parent_names)

    for name, subschema in properties.items():
        if name.lower() not in possible_names:
            continue

        if isinstance(subschema, dict) and "object" in get_type(subschema):
            return name, name.lower() in parent_names

    return None
