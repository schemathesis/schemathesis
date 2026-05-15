"""Value-level violators for the negative-fuzzing value channel.

Used when schema-level `not:` wrappers don't reliably produce violations (e.g. format).
"""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

from schemathesis.core.error_feedback.store import ParameterPath
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY
from schemathesis.core.jsonschema.bundler import REFERENCE_TO_BUNDLE_PREFIX
from schemathesis.core.jsonschema.types import JsonSchemaObject, JsonValue

ValueChannelKeyword: TypeAlias = Literal[
    "format:uuid",
    "format:email",
    "format:date-time",
    "format:date",
    "pattern",
    "minLength",
    "maxLength",
    "enum",
    "minimum",
    "maximum",
    "multipleOf",
    "required",
]


def violate_uuid(original: str) -> str:
    # If already UUID-shaped, corrupt in place ('g' is not hex) so it still looks like a UUID.
    # Otherwise use a hardcoded near-miss — nothing useful to corrupt in an arbitrary string.
    if len(original) == 36 and original.count("-") == 4:
        return "g" + original[1:]
    return "g8fadcc5-ce2b-2f6f-a0cd-faaa313ba470"


def violate_email(_: str) -> str:
    return "useratexample.com"


def violate_date_time(_: str) -> str:
    return "2024-01-01T12:99:00Z"


def violate_date(_: str) -> str:
    return "2024-13-01"


def violate_pattern(original: str, pattern: str) -> str:
    """Append a character outside common pattern classes. Naive but effective for typical patterns."""
    return original + "*"


def violate_min_length(original: str, min_length: int) -> str:
    return original[: max(0, min_length - 1)]


# Specs often use `Integer.MAX_VALUE` as a "no real limit" sentinel; expanding
# past this cap would OOM. Above the cap we skip and the next draw picks again.
_NEGATIVE_MAX_LENGTH_CAP = 64 * 1024


def violate_max_length(original: str, max_length: int) -> str:
    if max_length > _NEGATIVE_MAX_LENGTH_CAP:
        return original
    return original + "*" * (max_length - len(original) + 1)


def violate_minimum(original: float | int, minimum: float | int) -> float | int:
    return minimum - 1


def violate_maximum(original: float | int, maximum: float | int) -> float | int:
    return maximum + 1


def violate_enum(original: JsonValue, enum: list[JsonValue]) -> str:
    candidate = "__not_in_enum__"
    while candidate in enum:
        candidate += "_"
    return candidate


def violate_multiple_of(original: float | int, multiple_of: float | int) -> float | int:
    return original + 1


def violate_required(body: dict[str, JsonValue], required: list[str]) -> dict[str, JsonValue]:
    if not required:
        return body
    out = dict(body)
    out.pop(required[0], None)
    return out


def collect_value_targets(
    body: JsonValue,
    schema: JsonSchemaObject,
    path: ParameterPath = (),
    bundle: JsonSchemaObject | None = None,
    schema_pointer: str = "",
) -> list[tuple[ParameterPath, str, JsonValue, str, JsonSchemaObject]]:
    """Emit `(path, schema_pointer, value, keyword, schema_at_path)` for every constraint-bearing leaf.

    `path` tracks the body location (with concrete keys/indices, possibly random
    for `additionalProperties`); `schema_pointer` tracks the schema-keyword chain
    so error messages stay stable across runs. Walks body and schema in lockstep,
    resolving `$ref` against the schema's `x-bundled` map.
    """
    if bundle is None:
        bundle_candidate = schema.get(BUNDLE_STORAGE_KEY)
        bundle = bundle_candidate if isinstance(bundle_candidate, dict) else {}

    targets: list[tuple[ParameterPath, str, JsonValue, str, JsonSchemaObject]] = []

    # Resolve $ref hops transparently.
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith(REFERENCE_TO_BUNDLE_PREFIX):
        target_name = ref.rsplit("/", 1)[-1]
        target = bundle.get(target_name) if isinstance(bundle, dict) else None
        if isinstance(target, dict):
            return collect_value_targets(body, target, path, bundle, schema_pointer + f"/$ref/{target_name}")
        return targets

    if isinstance(body, dict):
        properties_schema = schema.get("properties", {})
        if isinstance(properties_schema, dict):
            for name, prop_schema in properties_schema.items():
                # Boolean property schemas (`{"x": true}` / `false`) are valid in
                # JSON Schema / OpenAPI 3.1 but expose no constraint-bearing leaves;
                # skip them rather than calling `.get` on a `bool`.
                if name in body and isinstance(prop_schema, dict):
                    targets.extend(
                        collect_value_targets(
                            body[name], prop_schema, path + (name,), bundle, schema_pointer + f"/properties/{name}"
                        )
                    )
        # Walk additionalProperties for keys NOT covered by `properties`.
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            covered = set(properties_schema) if isinstance(properties_schema, dict) else set()
            for name, value in body.items():
                if name in covered:
                    continue
                targets.extend(
                    collect_value_targets(
                        value, additional, path + (name,), bundle, schema_pointer + "/additionalProperties"
                    )
                )
        if isinstance(schema.get("required"), list) and schema["required"]:
            targets.append((path, schema_pointer, body, "required", schema))
    elif isinstance(body, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(body):
                targets.extend(collect_value_targets(item, items, path + (index,), bundle, schema_pointer + "/items"))
    elif isinstance(body, str):
        for keyword in ("pattern", "minLength", "maxLength", "enum"):
            if keyword in schema:
                targets.append((path, schema_pointer, body, keyword, schema))
        format_value = schema.get("format")
        if isinstance(format_value, str) and format_value in ("uuid", "email", "date-time", "date"):
            targets.append((path, schema_pointer, body, f"format:{format_value}", schema))
    elif isinstance(body, (int, float)) and not isinstance(body, bool):
        for keyword in ("minimum", "maximum", "multipleOf", "enum"):
            if keyword in schema:
                targets.append((path, schema_pointer, body, keyword, schema))
    return targets


def apply_value_channel(
    body: JsonValue,
    target_path: ParameterPath,
    keyword: ValueChannelKeyword,
    schema_at_path: JsonSchemaObject,
) -> tuple[JsonValue, JsonValue | None, JsonValue]:
    """Replace the value at `target_path` in `body` according to `keyword`.

    Returns (new_body, original_value, new_value). `body` is mutated in place AND returned.
    `original_value` is `None` for the `required` keyword when the dropped key was absent.
    """
    if keyword == "required":
        # `target_path` points at the dict whose `required` constraint should be violated,
        # not a leaf — drop one of its required keys.
        target_dict: JsonValue = body
        for segment in target_path:
            target_dict = target_dict[segment]  # type: ignore[index]  # JsonValue includes dict/list at this point
        assert isinstance(target_dict, dict)
        required = schema_at_path["required"]
        if not required:
            return body, target_dict, target_dict
        dropped_key = required[0]
        original_value = target_dict.get(dropped_key)
        target_dict.pop(dropped_key, None)
        return body, original_value, target_dict

    if not target_path:
        new_value = _apply_violator(body, keyword, schema_at_path)
        return new_value, body, new_value

    parent: JsonValue = body
    for segment in target_path[:-1]:
        parent = parent[segment]  # type: ignore[index]  # JsonValue includes dict/list at this point
    leaf_key = target_path[-1]
    assert isinstance(parent, (dict, list))
    original = parent[leaf_key]  # type: ignore[index]  # JsonValue includes dict/list at this point
    new_value = _apply_violator(original, keyword, schema_at_path)
    parent[leaf_key] = new_value  # type: ignore[index]  # JsonValue includes dict/list at this point
    return body, original, new_value


def _apply_violator(original: Any, keyword: ValueChannelKeyword, schema_at_path: JsonSchemaObject) -> JsonValue:
    match keyword:
        case "format:uuid":
            return violate_uuid(original)
        case "format:email":
            return violate_email(original)
        case "format:date-time":
            return violate_date_time(original)
        case "format:date":
            return violate_date(original)
        case "pattern":
            return violate_pattern(original, schema_at_path["pattern"])
        case "minLength":
            return violate_min_length(original, schema_at_path["minLength"])
        case "maxLength":
            return violate_max_length(original, schema_at_path["maxLength"])
        case "minimum":
            return violate_minimum(original, schema_at_path["minimum"])
        case "maximum":
            return violate_maximum(original, schema_at_path["maximum"])
        case "enum":
            return violate_enum(original, schema_at_path["enum"])
        case _:
            return violate_multiple_of(original, schema_at_path["multipleOf"])
