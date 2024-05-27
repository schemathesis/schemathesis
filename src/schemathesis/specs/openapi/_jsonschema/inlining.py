from __future__ import annotations

import re

from ....internal.copy import fast_deepcopy, merge_into
from ....internal.result import Err, Ok, Result
from .errors import InfiniteRecursionError
from .iteration import iter_subschemas
from .keys import _key_for_reference
from .types import MovedSchemas, ObjectSchema, Schema


def inline_recursive_references(referenced_schemas: MovedSchemas, recursive: set[str]) -> None:
    keys = {_key_for_reference(ref)[0] for ref in recursive}
    originals = {key: fast_deepcopy(value) if key in keys else value for key, value in referenced_schemas.items()}
    for reference in recursive:
        # TODO. iterating only recursive schemas themselves could be not enough - what if some other schema contains a recursive ref???
        key, _ = _key_for_reference(reference)
        _inline_recursive_references(referenced_schemas[key], originals, recursive, [key])


def _inline_recursive_references(
    schema: ObjectSchema, referenced_schemas: MovedSchemas, recursive: set[str], path: list[str]
) -> None:
    """Inline all recursive references in the given item."""
    reference = schema.get("$ref")
    if isinstance(reference, str):
        # TODO: There could be less traversal if we know where refs are located within `refrenced_item`.
        #       Just copy the value and directly jump to the next ref in it, or iterate over them
        if reference in recursive:
            schema.clear()
            key, _ = _key_for_reference(reference)
            if path.count(key) < 3:
                referenced_item = referenced_schemas[key]
                # Extend with a deep copy as the tree should grow with owned data
                merge_into(schema, referenced_item)
                path.append(key)
                _inline_recursive_references(schema, referenced_schemas, recursive, path)
                path.pop()
        return
    for subschema in iter_subschemas(schema):
        _inline_recursive_references(subschema, referenced_schemas, recursive, path)


DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_INLININGS = 100


def unrecurse(
    referenced_schemas: MovedSchemas,
    recursive: set[str],
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_inlinings: int = DEFAULT_MAX_INLININGS,
) -> None:
    """Transform all schemas containing recursive references into non-recursive ones.

    Transformation is done by inlining the referenced schema into the schema that references it up to the
    given limits. When the limit is reached (either `max_depth` or `max_inlinings`), all optional subschemas
    that lead to the recursive reference are removed from the schema. If all such subschemas are required,
    which means infinite recursion, an error is raised.
    """
    pass


def on_reached_limit(schema: ObjectSchema, recursive: set[str]) -> ObjectSchema:
    """Remove all optional subschemas that lead to recursive references."""
    result = _on_reached_limit(schema, recursive)
    if isinstance(result, Ok):
        return result.ok()
    raise result.err()


def _on_reached_limit(schema: ObjectSchema, recursive: set[str]) -> Result[ObjectSchema, InfiniteRecursionError]:
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference in recursive or not schema:
        return Ok({})
    new: ObjectSchema = {}
    remove_keywords: list[str] = []
    for key, value in schema.items():
        if key == "additionalProperties" and isinstance(value, dict):
            result = _on_additional_properties_reached_limit(
                new, value, schema.get("minProperties", 0), schema.get("properties", {}), recursive
            )
        elif key == "items":
            result = _on_items_reached_limit(new, value, schema.get("minItems", 0), remove_keywords, recursive)
        elif key == "properties":
            required = schema.get("required", [])
            result = _on_properties_reached_limit(new, value, required, remove_keywords, recursive)
        elif key == "anyOf":
            result = _on_any_of_reached_limit(new, value, recursive)
        elif key == "patternProperties":
            result = _on_pattern_properties_reached_limit(
                new, value, schema.get("properties", {}), schema.get("required", []), remove_keywords, recursive
            )
        elif key == "propertyNames":
            result = _on_property_names_reached_limit(
                new, value, schema.get("minProperties", 0), remove_keywords, recursive
            )
        elif key in ("contains", "if", "then", "else", "not"):
            result = _on_schema_reached_limit(new, value, key, recursive, allow_modification=key != "not")
        elif key in ("allOf", "oneOf", "additionalItems") and isinstance(value, list):
            result = _on_list_of_schemas_reached_limit(new, value, key, recursive)
        else:
            continue
        if isinstance(result, Err):
            return result
    if not new and not remove_keywords:
        return Ok(schema)
    for key, value in schema.items():
        if key not in remove_keywords and key not in new:
            new[key] = value
    return Ok(new)


def _on_additional_properties_reached_limit(
    new: ObjectSchema,
    schema: ObjectSchema,
    min_properties: int,
    properties: dict[str, Schema],
    recursive: set[str],
) -> Result[None, InfiniteRecursionError]:
    if schema.get("$ref") in recursive:
        if min_properties > len(properties):
            return Err(InfiniteRecursionError("Infinite recursion in additionalProperties"))
        new["additionalProperties"] = False
    else:
        result = _on_reached_limit(schema, recursive)
        if isinstance(result, Err):
            if min_properties > len(properties):
                return Err(InfiniteRecursionError("Infinite recursion in additionalProperties"))
            new["additionalProperties"] = False
        else:
            new_subschema = result.ok()
            if new_subschema is not schema:
                new["additionalProperties"] = new_subschema
    return Ok(None)


def _on_items_reached_limit(
    new: ObjectSchema,
    schema: ObjectSchema | list[Schema],
    min_items: int,
    remove_keywords: list[str],
    recursive: set[str],
) -> Result[None, InfiniteRecursionError]:
    if isinstance(schema, dict):
        if schema.get("$ref") in recursive:
            if min_items > 0:
                return Err(InfiniteRecursionError("Infinite recursion in items"))
            new["maxItems"] = 0
            remove_keywords.append("items")
        else:
            result = _on_reached_limit(schema, recursive)
            if isinstance(result, Err):
                if min_items > 0:
                    return Err(InfiniteRecursionError("Infinite recursion in items"))
                new["maxItems"] = 0
                remove_keywords.append("items")
            else:
                new_subschema = result.ok()
                if new_subschema is not schema:
                    new["items"] = new_subschema
    elif isinstance(schema, list):
        for idx, subschema in enumerate(schema):
            if isinstance(subschema, dict):
                if subschema.get("$ref") in recursive:
                    if min_items > idx:
                        return Err(InfiniteRecursionError("Infinite recursion in items"))
                    new["maxItems"] = idx
                    if idx == 0:
                        remove_keywords.append("items")
                    else:
                        new["items"] = schema[:idx]
                    break
    return Ok(None)


def _on_property_names_reached_limit(
    new: ObjectSchema, schema: ObjectSchema, min_properties: int, remove_keywords: list[str], recursive: set[str]
) -> Result[None, InfiniteRecursionError]:
    def forbid() -> None:
        new["maxProperties"] = 0
        remove_keywords.append("propertyNames")
        return None

    if schema.get("$ref") in recursive:
        if min_properties > 0:
            return Err(InfiniteRecursionError("Infinite recursion in propertyNames"))
        forbid()
    else:
        result = _on_reached_limit(schema, recursive)
        if isinstance(result, Err):
            if min_properties > 0:
                return Err(InfiniteRecursionError("Infinite recursion in propertyNames"))
            forbid()
        else:
            new_subschema = result.ok()
            if new_subschema is not schema:
                if new_subschema:
                    new["propertyNames"] = new_subschema
                else:
                    forbid()

    return Ok(None)


def _on_properties_reached_limit(
    new: ObjectSchema, schema: ObjectSchema, required: list[str], remove_keywords: list[str], recursive: set[str]
) -> Result[None, InfiniteRecursionError]:
    removal = []
    replacement = {}
    for subkey, subschema in schema.items():
        if isinstance(subschema, dict):
            if subschema.get("$ref") in recursive:
                if subkey in required:
                    return Err(InfiniteRecursionError(f"Infinite recursion in the required property: {subkey}"))
                # New schema should not have this property
                removal.append(subkey)
            else:
                result = _on_reached_limit(subschema, recursive)
                if isinstance(result, Err):
                    if subkey in required:
                        return result
                    removal.append(subkey)
                else:
                    new_subschema = result.ok()
                    if new_subschema is not subschema:
                        replacement[subkey] = new_subschema
    if removal or replacement:
        properties = {}
        for key, subschema in schema.items():
            if key in replacement:
                properties[key] = replacement[key]
            elif key not in removal:
                properties[key] = subschema
        if properties:
            new["properties"] = properties
        else:
            remove_keywords.append("properties")
    return Ok(None)


def _on_pattern_properties_reached_limit(
    new: ObjectSchema,
    schema: ObjectSchema,
    pattern_properties: dict[str, Schema],
    required: list[str],
    remove_keywords: list[str],
    recursive: set[str],
) -> Result[None, InfiniteRecursionError]:
    removal = []
    replacement = {}
    for pattern, subschema in schema.items():
        if isinstance(subschema, dict):
            if subschema.get("$ref") in recursive:
                if any(re.match(pattern, entry) for entry in required):
                    return Err(InfiniteRecursionError(f"Infinite recursion in {pattern}"))
                # This pattern should be removed from `patternProperties`
                removal.append(pattern)
            else:
                result = _on_reached_limit(subschema, recursive)
                if isinstance(result, Err):
                    if any(re.match(pattern, entry) for entry in required):
                        return result
                    removal.append(pattern)
                else:
                    new_subschema = result.ok()
                    if new_subschema is not subschema:
                        replacement[pattern] = new_subschema
    if removal or replacement:
        pattern_properties = {}
        for pattern, subschema in schema.items():
            if pattern in replacement:
                pattern_properties[pattern] = replacement[pattern]
            elif pattern not in removal:
                pattern_properties[pattern] = subschema
        if pattern_properties:
            new["patternProperties"] = pattern_properties
        else:
            remove_keywords.append("patternProperties")
    return Ok(None)


def _on_any_of_reached_limit(
    new: ObjectSchema, schema: list[Schema], recursive: set[str]
) -> Result[None, InfiniteRecursionError]:
    removal: list[int] = []
    replacement: dict[int, Schema] = {}
    for idx, subschema in enumerate(schema):
        if isinstance(subschema, dict):
            if subschema.get("$ref") in recursive:
                removal.append(idx)
            else:
                result = _on_reached_limit(subschema, recursive)
                if isinstance(result, Err):
                    removal.append(idx)
                else:
                    new_subschema = result.ok()
                    if new_subschema is not subschema:
                        replacement[idx] = new_subschema
    if len(removal) == len(schema):
        return Err(InfiniteRecursionError("Infinite recursion in anyOf"))
    if removal or replacement:
        items = []
        for idx, subschema in enumerate(schema):
            if idx in replacement:
                items.append(replacement[idx])
            elif idx not in removal:
                items.append(subschema)
        new["anyOf"] = items
    return Ok(None)


def _on_schema_reached_limit(
    new: ObjectSchema, schema: ObjectSchema, key: str, recursive: set[str], allow_modification: bool = True
) -> Result[None, InfiniteRecursionError]:
    if schema.get("$ref") in recursive:
        return Err(InfiniteRecursionError(f"Infinite recursion in {key}"))
    result = _on_reached_limit(schema, recursive)
    if isinstance(result, Err):
        return result
    new_subschema = result.ok()
    if new_subschema is not schema:
        if allow_modification:
            new[key] = new_subschema
        else:
            # `not` can't be modified
            return Err(InfiniteRecursionError(f"Infinite recursion in {key}"))
    return Ok(None)


def _on_list_of_schemas_reached_limit(
    new: ObjectSchema, schema: list[ObjectSchema], key: str, recursive: set[str]
) -> Result[None, InfiniteRecursionError]:
    replacement = {}
    for idx, subschema in enumerate(schema):
        if isinstance(subschema, dict):
            if subschema.get("$ref") in recursive:
                return Err(InfiniteRecursionError(f"Infinite recursion in {key}"))
            else:
                result = _on_reached_limit(subschema, recursive)
                if isinstance(result, Err):
                    return result
                new_subschema = result.ok()
                if new_subschema is not subschema:
                    replacement[idx] = new_subschema
    if replacement:
        items = []
        for idx, subschema in enumerate(schema):
            if idx in replacement:
                items.append(replacement[idx])
            else:
                items.append(subschema)
        new[key] = items
    return Ok(None)
