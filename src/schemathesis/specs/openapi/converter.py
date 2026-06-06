from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeGuard, overload

from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY, REFERENCE_TO_BUNDLE_PREFIX
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.transforms import deepclone
from schemathesis.specs.openapi.patterns import (
    is_valid_python_regex,
    normalize_regex,
    pattern_length_bounds,
    update_quantifier,
)


@overload
def to_json_schema(
    schema: dict[str, Any],
    nullable_keyword: str,
    is_response_schema: bool = False,
    update_quantifiers: bool = True,
    clone: bool = True,
    upgrade_legacy_exclusive_bounds: bool = False,
    convert_prefix_items: bool = True,
    convert_if_then_else: bool = True,
    name_to_uri: dict[str, str] | None = None,
    merge_ref_siblings: bool = True,
) -> dict[str, Any]: ...  # pragma: no cover


@overload
def to_json_schema(
    schema: bool,
    nullable_keyword: str,
    is_response_schema: bool = False,
    update_quantifiers: bool = True,
    clone: bool = True,
    upgrade_legacy_exclusive_bounds: bool = False,
    convert_prefix_items: bool = True,
    convert_if_then_else: bool = True,
    name_to_uri: dict[str, str] | None = None,
    merge_ref_siblings: bool = True,
) -> bool: ...  # pragma: no cover


def to_json_schema(
    schema: dict[str, Any] | bool,
    nullable_keyword: str,
    is_response_schema: bool = False,
    update_quantifiers: bool = True,
    clone: bool = True,
    upgrade_legacy_exclusive_bounds: bool = False,
    convert_prefix_items: bool = True,
    convert_if_then_else: bool = True,
    name_to_uri: dict[str, str] | None = None,
    merge_ref_siblings: bool = True,
) -> dict[str, Any] | bool:
    if isinstance(schema, bool):
        return schema
    if clone:
        schema = deepclone(schema)
    return _to_json_schema(
        schema,
        nullable_keyword=nullable_keyword,
        is_response_schema=is_response_schema,
        update_quantifiers=update_quantifiers,
        upgrade_legacy_exclusive_bounds=upgrade_legacy_exclusive_bounds,
        convert_prefix_items=convert_prefix_items,
        convert_if_then_else=convert_if_then_else,
        name_to_uri=name_to_uri,
        merge_ref_siblings=merge_ref_siblings,
    )


def _to_json_schema(
    schema: JsonSchema,
    *,
    nullable_keyword: str,
    is_response_schema: bool = False,
    update_quantifiers: bool = True,
    upgrade_legacy_exclusive_bounds: bool = False,
    convert_prefix_items: bool = True,
    convert_if_then_else: bool = True,
    name_to_uri: dict[str, str] | None = None,
    merge_ref_siblings: bool = True,
    bundle: dict[str, Any] | None = None,
) -> JsonSchema:
    if not isinstance(schema, dict):
        return schema if isinstance(schema, bool) else {}
    if bundle is None:
        nested_bundle = schema.get(BUNDLE_STORAGE_KEY)
        if isinstance(nested_bundle, dict):
            bundle = nested_bundle

    # OpenAPI 3.0 / Swagger 2.0: keys alongside `$ref` are ignored. Drop them so generation and
    # validation observe the same shape; otherwise a sibling like `type: string` next to a `$ref`
    # to an object schema produces strings the validator rejects.
    if not merge_ref_siblings and "$ref" in schema:
        nullable = schema.get(nullable_keyword)
        for key in list(schema):
            if key != "$ref" and key != BUNDLE_STORAGE_KEY:
                del schema[key]
        if nullable:
            schema[nullable_keyword] = nullable

    if upgrade_legacy_exclusive_bounds:
        rewrite_legacy_exclusive_bounds(schema)

    if schema.get(nullable_keyword):
        del schema[nullable_keyword]
        bundled = schema.pop(BUNDLE_STORAGE_KEY, None)
        schema = {"anyOf": [schema, {"type": "null"}]}
        if bundled:
            schema[BUNDLE_STORAGE_KEY] = bundled
    schema_type = schema.get("type")
    if schema_type == "file":
        schema["type"] = "string"
        schema["format"] = "binary"

    # Handle unsupported regex patterns - try translation first, remove if that fails
    pattern = schema.get("pattern")
    if pattern is not None:
        if not is_valid_python_regex(pattern):
            # Pattern is invalid Python regex - try to translate PCRE constructs
            translated = normalize_regex(pattern)
            if translated is not None:
                schema["pattern"] = translated
            else:
                del schema["pattern"]
        elif pattern.startswith(r"\A") or pattern.endswith(r"\Z"):
            # Pattern uses Python-specific anchors that need Rust translation for jsonschema-rs
            translated = normalize_regex(pattern)
            if translated is not None:
                schema["pattern"] = translated
    if update_quantifiers:
        update_pattern_in_schema(schema)
    # Sometimes `required` is incorrectly has a boolean value
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, subschema in properties.items():
            if not isinstance(subschema, dict):
                continue
            is_required = subschema.get("required")
            if is_required is True:
                required = schema.setdefault("required", [])
                if name not in required:
                    required.append(name)
                del subschema["required"]
            elif is_required is False:
                if "required" in schema and name in schema["required"]:
                    schema["required"].remove(name)
                del subschema["required"]

    if schema_type == "object":
        if is_response_schema:
            # Write-only properties should not occur in responses
            rewrite_properties(schema, is_write_only)
        else:
            # Read-only properties should not occur in requests
            rewrite_properties(schema, is_read_only)

    ensure_required_properties(schema)

    # Convert prefixItems -> items[array] (the Draft 4/7 tuple form).
    # Skipped when the consumer needs `prefixItems` to stay intact (e.g. for Draft 2020-12 validators).
    if convert_prefix_items and "prefixItems" in schema:
        prefix_items = schema.pop("prefixItems")
        if "items" in schema:
            # When both prefixItems and items exist, items becomes additionalItems
            schema["additionalItems"] = schema.pop("items")
        schema["items"] = prefix_items

    # Convert `if`/`then`/`else` to anyOf so coverage's anyOf machinery handles the conditional.
    # Skipped when the consumer needs the originals to stay intact (e.g. for Draft 2020-12 validators).
    if convert_if_then_else:
        _rewrite_if_then_else(schema)

    if schema_type == "array":
        _rewrite_allof_of_contains_consts(schema)

    if not is_response_schema:
        _pin_discriminator_property(schema, name_to_uri, bundle)

    for keyword, value in schema.items():
        if keyword in IN_VALUE and isinstance(value, dict):
            schema[keyword] = _to_json_schema(
                value,
                nullable_keyword=nullable_keyword,
                is_response_schema=is_response_schema,
                update_quantifiers=update_quantifiers,
                upgrade_legacy_exclusive_bounds=upgrade_legacy_exclusive_bounds,
                convert_prefix_items=convert_prefix_items,
                convert_if_then_else=convert_if_then_else,
                name_to_uri=name_to_uri,
                merge_ref_siblings=merge_ref_siblings,
                bundle=bundle,
            )
        elif keyword in IN_ITEM and isinstance(value, list):
            for idx, subschema in enumerate(value):
                value[idx] = _to_json_schema(
                    subschema,
                    nullable_keyword=nullable_keyword,
                    is_response_schema=is_response_schema,
                    update_quantifiers=update_quantifiers,
                    upgrade_legacy_exclusive_bounds=upgrade_legacy_exclusive_bounds,
                    convert_prefix_items=convert_prefix_items,
                    convert_if_then_else=convert_if_then_else,
                    name_to_uri=name_to_uri,
                    merge_ref_siblings=merge_ref_siblings,
                    bundle=bundle,
                )
        elif keyword in IN_CHILD and isinstance(value, dict):
            for name, subschema in value.items():
                value[name] = _to_json_schema(
                    subschema,
                    nullable_keyword=nullable_keyword,
                    is_response_schema=is_response_schema,
                    update_quantifiers=update_quantifiers,
                    upgrade_legacy_exclusive_bounds=upgrade_legacy_exclusive_bounds,
                    convert_prefix_items=convert_prefix_items,
                    convert_if_then_else=convert_if_then_else,
                    name_to_uri=name_to_uri,
                    merge_ref_siblings=merge_ref_siblings,
                    bundle=bundle,
                )

    # A property forbidden inside an `allOf` branch (read/write-only rewrite produces `{"not": {}}`)
    # must also be removed from the parent's `required`, otherwise the schema is unsatisfiable.
    required = schema.get("required")
    if isinstance(required, list) and required:
        forbidden = _forbidden_in_allof_branches(schema)
        if forbidden:
            new_required = [name for name in required if name not in forbidden]
            if new_required:
                schema["required"] = new_required
            else:
                schema.pop("required", None)

    return schema


def _forbidden_in_allof_branches(schema: dict[str, Any]) -> set[str]:
    forbidden: set[str] = set()
    for branch in schema.get("allOf") or []:
        if not isinstance(branch, dict):
            continue
        for name, subschema in (branch.get("properties") or {}).items():
            if subschema == {"not": {}}:
                forbidden.add(name)
        forbidden.update(_forbidden_in_allof_branches(branch))
    return forbidden


def _pin_discriminator_property(
    schema: dict[str, Any],
    name_to_uri: dict[str, str] | None,
    bundle: dict[str, Any] | None = None,
) -> None:
    """Pin the discriminator property to its expected value in each oneOf/anyOf branch.

    When a schema has a `discriminator`, each branch in oneOf/anyOf is wrapped in
    `allOf` with an `enum` constraint on the discriminator property. This ensures
    each branch generates its correct discriminator value.
    """
    discriminator = schema.get("discriminator")
    if not isinstance(discriminator, dict):
        return
    property_name = discriminator.get("propertyName")
    if not property_name:
        return
    explicit_mapping: dict[str, str] = discriminator.get("mapping") or {}
    ref_to_value = {ref: value for value, ref in explicit_mapping.items()}

    for keyword in ("anyOf", "oneOf"):
        items = schema.get(keyword)
        if not isinstance(items, list):
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            ref = item.get("$ref")
            if not isinstance(ref, str):
                continue
            # Resolve bundled ref (e.g. "#/x-bundled/schema1") back to original URI for schema name extraction
            resolved_ref = ref
            if name_to_uri and ref.startswith(f"{REFERENCE_TO_BUNDLE_PREFIX}/"):
                bundled_name = ref[len(REFERENCE_TO_BUNDLE_PREFIX) + 1 :]
                original_uri = name_to_uri.get(bundled_name, "")
                if "#" in original_uri:
                    resolved_ref = "#" + original_uri.split("#", 1)[1]
            # Without an explicit mapping, prefer the branch's own const/enum so the literal
            # tag (`"function"`) wins over the schema name (`FunctionTool`).
            disc_value = ref_to_value.get(resolved_ref) or _branch_discriminator_value(ref, property_name, bundle)
            if disc_value is None:
                # Fall back to schema name -- unless the target is itself polymorphic,
                # in which case the real discriminator values live on its inner branches
                # and pinning here would force a value none of them accept.
                if _branch_is_polymorphic(ref, bundle):
                    continue
                disc_value = resolved_ref.rstrip("/").rsplit("/", 1)[-1]
            if not disc_value:
                continue
            # `enum` is used instead of `const` so the pin is recognized under Draft 4
            # (used by OpenAPI 2.0 / 3.0); Draft 4 silently ignores `const`.
            items[idx] = {"allOf": [item, {"properties": {property_name: {"enum": [disc_value]}}}]}


def _branch_discriminator_value(ref: str, property_name: str, bundle: dict[str, Any] | None) -> str | None:
    if bundle is None or not ref.startswith(f"{REFERENCE_TO_BUNDLE_PREFIX}/"):
        return None
    bundled = bundle.get(ref[len(REFERENCE_TO_BUNDLE_PREFIX) + 1 :])
    properties = bundled.get("properties") if isinstance(bundled, dict) else None
    sub = properties.get(property_name) if isinstance(properties, dict) else None
    if not isinstance(sub, dict):
        return None
    const = sub.get("const")
    if isinstance(const, str):
        return const
    enum = sub.get("enum")
    if isinstance(enum, list) and len(enum) == 1 and isinstance(enum[0], str):
        return enum[0]
    return None


def _branch_is_polymorphic(ref: str, bundle: dict[str, Any] | None) -> bool:
    if bundle is None or not ref.startswith(f"{REFERENCE_TO_BUNDLE_PREFIX}/"):
        return False
    bundled = bundle.get(ref[len(REFERENCE_TO_BUNDLE_PREFIX) + 1 :])
    if not isinstance(bundled, dict):
        return False
    return "oneOf" in bundled or "anyOf" in bundled


def _rewrite_allof_of_contains_consts(schema: dict[str, Any]) -> None:
    # `allOf: [{contains: {const: A}}, {contains: {const: B}}, ...]` is rewritten so the
    # required consts become a positional `items` prefix, forcing them into the array up front
    # instead of relying on filtering to satisfy every `contains`.
    all_of = schema.get("allOf")
    if not isinstance(all_of, list) or len(all_of) < 2:
        return
    if isinstance(schema.get("items"), list):
        return
    consts = []
    keep = []
    for entry in all_of:
        if (
            isinstance(entry, dict)
            and len(entry) == 1
            and isinstance(entry.get("contains"), dict)
            and entry["contains"].keys() == {"const"}
        ):
            consts.append({"const": entry["contains"]["const"]})
        else:
            keep.append(entry)
    if len(consts) < 2:
        return
    original_items = schema.get("items")
    if isinstance(original_items, dict):
        schema["additionalItems"] = original_items
    schema["items"] = consts
    if keep:
        schema["allOf"] = keep
    else:
        schema.pop("allOf", None)
    min_items = schema.get("minItems")
    if not isinstance(min_items, int) or min_items < len(consts):
        schema["minItems"] = len(consts)


def _rewrite_if_then_else(schema: dict[str, Any]) -> None:
    # Flatten `if`/`then`/`else` into `anyOf` branches so coverage's anyOf machinery exercises both paths.
    if "if" not in schema:
        return
    if_sub = schema.pop("if")
    then_sub = schema.pop("then", None)
    else_sub = schema.pop("else", None)

    # Bare `if` with no `then`/`else` is a JSON Schema tautology; drop without adding constraints.
    if then_sub is None and else_sub is None:
        return

    if then_sub is not None:
        then_branch: Any = {"allOf": [if_sub, then_sub]}
    else:
        then_branch = if_sub

    if else_sub is not None:
        else_branch: Any = {"allOf": [{"not": if_sub}, else_sub]}
    else:
        else_branch = {"not": if_sub}

    new_anyof = [then_branch, else_branch]

    # Compose with existing `anyOf`/`allOf` so author-declared constraints are preserved.
    if "anyOf" in schema:
        existing_anyof = schema.pop("anyOf")
        existing_allof = schema.setdefault("allOf", [])
        existing_allof.append({"anyOf": existing_anyof})
        existing_allof.append({"anyOf": new_anyof})
    elif "allOf" in schema:
        schema["allOf"].append({"anyOf": new_anyof})
    else:
        schema["anyOf"] = new_anyof


def rewrite_legacy_exclusive_bounds(schema: dict[str, Any]) -> None:
    for exclusive_key, bound_key in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
        exclusive = schema.get(exclusive_key)
        if not isinstance(exclusive, bool):
            continue
        if not exclusive:
            schema.pop(exclusive_key, None)
            continue

        bound = schema.get(bound_key)
        if isinstance(bound, bool) or not isinstance(bound, (int, float)):
            # `exclusive* = true` without a numeric bound can't be represented in modern drafts.
            schema.pop(exclusive_key, None)
            continue
        schema[exclusive_key] = bound
        schema.pop(bound_key, None)


def normalize_for_canonicalize(schema: JsonSchema) -> JsonSchema:
    # Rewrite legacy draft constructs `canonicalize` (Draft 2020-12) rejects (boolean exclusive bounds ->
    # numeric, tuple `items: [..]` -> `prefixItems`); copy-on-write returns unchanged input as-is.
    if not isinstance(schema, dict):
        return schema
    result = schema
    for key, value in schema.items():
        if isinstance(value, dict):
            upgraded = normalize_for_canonicalize(value)
            if upgraded is not value:
                if result is schema:
                    result = dict(schema)
                result[key] = upgraded
        elif isinstance(value, list):
            items = [normalize_for_canonicalize(item) if isinstance(item, dict) else item for item in value]
            if any(a is not b for a, b in zip(items, value, strict=True)):
                if result is schema:
                    result = dict(schema)
                result[key] = items
    if isinstance(result.get("items"), list):
        if result is schema:
            result = dict(schema)
        result["prefixItems"] = result.pop("items")
        if "additionalItems" in result:
            result["items"] = result.pop("additionalItems")
    if isinstance(result.get("exclusiveMinimum"), bool) or isinstance(result.get("exclusiveMaximum"), bool):
        if result is schema:
            result = dict(schema)
        rewrite_legacy_exclusive_bounds(result)
    return result


def ensure_required_properties(schema: dict[str, Any]) -> None:
    if schema.get("additionalProperties") is not False:
        return

    required = schema.get("required")
    if not required or not isinstance(required, list):
        return

    properties = schema.setdefault("properties", {})

    # Add missing required properties as empty schemas
    for name in required:
        if name not in properties:
            properties[name] = {}


IN_VALUE = frozenset(
    (
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    )
)
IN_ITEM = frozenset(
    (
        "allOf",
        "anyOf",
        "oneOf",
        "prefixItems",
    )
)
IN_CHILD = frozenset(
    (
        "$defs",
        "definitions",
        "dependentSchemas",
        "patternProperties",
        "properties",
        BUNDLE_STORAGE_KEY,
    )
)


def update_pattern_in_schema(schema: dict[str, Any]) -> None:
    pattern = schema.get("pattern")
    min_length = schema.get("minLength")
    max_length = schema.get("maxLength")
    if pattern and (min_length or max_length):
        new_pattern = update_quantifier(pattern, min_length, max_length)
        if new_pattern != pattern:
            # Pop a bound only if the rewrite encodes it; rewrites with unbounded slots can't absorb `maxLength`.
            new_min, new_max = pattern_length_bounds(new_pattern)
            schema["pattern"] = new_pattern
            if min_length is not None and new_min >= min_length:
                schema.pop("minLength", None)
            if max_length is not None and new_max is not None and new_max <= max_length:
                schema.pop("maxLength", None)


def rewrite_properties(schema: dict[str, Any], predicate: Callable[[dict[str, Any]], bool]) -> None:
    required = schema.get("required", [])
    for name, subschema in list(schema.get("properties", {}).items()):
        if predicate(subschema):
            if name in required:
                required.remove(name)
            schema["properties"][name] = {"not": {}}
    if not schema.get("required"):
        schema.pop("required", None)
    if not schema.get("properties"):
        schema.pop("properties", None)


def is_write_only(schema: object) -> TypeGuard[dict[str, Any]]:
    if not isinstance(schema, dict):
        return False
    return schema.get("writeOnly", False) or schema.get("x-writeOnly", False)


def is_read_only(schema: object) -> TypeGuard[dict[str, Any]]:
    if not isinstance(schema, dict):
        return False
    return schema.get("readOnly", False)
