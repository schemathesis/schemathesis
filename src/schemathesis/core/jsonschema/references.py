from __future__ import annotations

from schemathesis.core.jsonschema.keywords import ALL_KEYWORDS
from schemathesis.core.jsonschema.types import JsonSchema, JsonSchemaObject, get_type


def sanitize(schema: JsonSchema) -> set[str]:
    """Remove optional parts of the schema that contain references.

    It covers only the most popular cases, as removing all optional parts is complicated.
    We might fall back to filtering out invalid cases in the future.
    """
    if isinstance(schema, bool):
        return set()

    stack = [schema]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            # Optional properties
            if "properties" in current:
                properties = current["properties"]
                required = current.get("required", [])
                for name, value in list(properties.items()):
                    if isinstance(value, dict):
                        if name not in required and _has_references(value):
                            del properties[name]
                        elif _find_single_reference_combinators(value):
                            properties.pop(name, None)
                        else:
                            stack.append(value)

            # Optional items
            if "items" in current:
                _sanitize_items(current)
            # Not required additional properties
            if "additionalProperties" in current:
                _sanitize_additional_properties(current)
            for k in _find_single_reference_combinators(current):
                del current[k]

    remaining: set[str] = set()
    _collect_all_references(schema, remaining)
    return remaining


def _collect_all_references(schema: JsonSchema | list[JsonSchema], remaining: set[str]) -> None:
    """Recursively collect all $ref present in the schema."""
    if isinstance(schema, dict):
        reference = schema.get("$ref")
        if isinstance(reference, str):
            remaining.add(reference)
        for value in schema.values():
            _collect_all_references(value, remaining)
    elif isinstance(schema, list):
        for item in schema:
            _collect_all_references(item, remaining)


def _has_references_in_items(items: list[JsonSchema]) -> bool:
    return any("$ref" in item for item in items if isinstance(item, dict))


def _has_references(schema: JsonSchemaObject) -> bool:
    if "$ref" in schema:
        return True
    items = schema.get("items")
    return (isinstance(items, dict) and "$ref" in items) or isinstance(items, list) and _has_references_in_items(items)


def _is_optional_schema(schema: JsonSchema) -> bool:
    # Whether this schema could be dropped from a list of schemas
    if isinstance(schema, bool):
        return True
    type_ = get_type(schema)
    if type_ == ["object"]:
        # Empty object is valid for this schema -> could be dropped
        return schema.get("required", []) == [] and schema.get("minProperties", 0) == 0
    # Has at least one keyword -> should not be removed
    return not any(k in ALL_KEYWORDS for k in schema)


def _find_single_reference_combinators(schema: JsonSchemaObject) -> list[str]:
    # Schema example:
    # {
    #     "type": "object",
    #     "properties": {
    #         "parent": {
    #             "allOf": [{"$ref": "#/components/schemas/User"}]
    #         }
    #     }
    # }
    found = []
    for keyword in ("allOf", "oneOf", "anyOf"):
        combinator = schema.get(keyword)
        if combinator is not None:
            optionals = [subschema for subschema in combinator if not _is_optional_schema(subschema)]
            # NOTE: The first schema is not bool, hence it is safe to pass it to `_has_references`
            if len(optionals) == 1 and _has_references(optionals[0]):
                found.append(keyword)
    return found


def _sanitize_items(schema: JsonSchemaObject) -> None:
    items = schema["items"]
    min_items = schema.get("minItems", 0)
    if not min_items:
        if isinstance(items, dict) and ("$ref" in items or _find_single_reference_combinators(items)):
            _convert_to_empty_array(schema)
        if isinstance(items, list) and _has_references_in_items(items):
            _convert_to_empty_array(schema)


def _convert_to_empty_array(schema: JsonSchemaObject) -> None:
    del schema["items"]
    schema["maxItems"] = 0


def _sanitize_additional_properties(schema: JsonSchemaObject) -> None:
    additional_properties = schema["additionalProperties"]
    if isinstance(additional_properties, dict) and "$ref" in additional_properties:
        schema["additionalProperties"] = False
