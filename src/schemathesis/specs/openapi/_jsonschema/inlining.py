from ....internal.copy import fast_deepcopy, merge_into
from ....internal.result import Ok, Result, Err
from .errors import InfiniteRecursionError
from .iteration import iter_subschemas
from .keys import _key_for_reference
from .types import MovedSchemas, ObjectSchema


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
    new = {}
    skip_keywords = []
    for key, value in schema.items():
        if key == "additionalProperties" and isinstance(value, dict):
            result = _on_additional_properties_reached_limit(
                new, value, schema.get("minProperties", 0), schema.get("properties", {}), recursive
            )
            if isinstance(result, Err):
                return result
        elif key == "items":
            result = _on_items_reached_limit(new, value, schema.get("minItems", 0), skip_keywords, recursive)
            if isinstance(result, Err):
                return result
        elif key == "properties":
            required = schema.get("required", [])
            result = _on_properties_reached_limit(new, value, required, recursive)
            if isinstance(result, Err):
                return result
        elif key == "anyOf":
            result = _on_any_of_reached_limit(new, value, recursive)
            if isinstance(result, Err):
                return result
    if not new:
        return Ok(schema)
    for key, value in schema.items():
        if key not in skip_keywords and key not in new:
            new[key] = value
    return Ok(new)


def _on_additional_properties_reached_limit(
    parent: ObjectSchema,
    value: ObjectSchema,
    min_properties: int,
    properties: dict[str, ObjectSchema],
    recursive: set[str],
) -> Result[None, InfiniteRecursionError]:
    if value.get("$ref") in recursive:
        if min_properties > len(properties):
            return Err(InfiniteRecursionError("Infinite recursion in additionalProperties"))
        parent["additionalProperties"] = False
    else:
        result = _on_reached_limit(value, recursive)
        if isinstance(result, Err):
            parent["additionalProperties"] = False
        else:
            new_subschema = result.ok()
            if new_subschema is not value:
                parent["additionalProperties"] = new_subschema
    return Ok(None)


def _on_items_reached_limit(
    parent: ObjectSchema, schema: ObjectSchema, min_items: int, skip_keywords: list[str], recursive: set[str]
) -> Result[None, InfiniteRecursionError]:
    if isinstance(schema, dict):
        if schema.get("$ref") in recursive:
            if min_items > 0:
                return Err(InfiniteRecursionError("Infinite recursion in items"))
            parent["maxItems"] = 0
            skip_keywords.append("items")
        else:
            result = _on_reached_limit(schema, recursive)
            if isinstance(result, Err):
                if min_items > 0:
                    return Err(InfiniteRecursionError("Infinite recursion in items"))
                parent["maxItems"] = 0
                skip_keywords.append("items")
            else:
                new_subschema = result.ok()
                if new_subschema is not schema:
                    parent["items"] = new_subschema
    elif isinstance(schema, list):
        for idx, subschema in enumerate(schema):
            if isinstance(subschema, dict):
                if subschema.get("$ref") in recursive:
                    if min_items > idx:
                        return Err(InfiniteRecursionError("Infinite recursion in items"))
                    parent["maxItems"] = idx
                    if idx == 0:
                        skip_keywords.append("items")
                    else:
                        parent["items"] = schema[:idx]
                    break
    return Ok(None)


def _on_properties_reached_limit(
    parent: ObjectSchema, schema: ObjectSchema, required: list[str], recursive: set[str]
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
        parent["properties"] = properties
    return Ok(None)


def _on_any_of_reached_limit(
    parent: ObjectSchema, schema: ObjectSchema, recursive: set[str]
) -> Result[None, InfiniteRecursionError]:
    removal = []
    replacement = {}
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
        combinators = []
        for idx, subschema in enumerate(schema):
            if idx in replacement:
                combinators.append(replacement[idx])
            elif idx not in removal:
                combinators.append(subschema)
        parent["anyOf"] = combinators
    return Ok(None)
