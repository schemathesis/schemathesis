from __future__ import annotations

from dataclasses import dataclass, field
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
            if path.count(key) < DEFAULT_MAX_DEPTH:
                referenced_item = referenced_schemas[key]
                # Extend with a deep copy as the tree should grow with owned data
                merge_into(schema, referenced_item)
                path.append(key)
                _inline_recursive_references(schema, referenced_schemas, recursive, path)
                path.pop()
        return
    for subschema in iter_subschemas(schema):
        _inline_recursive_references(subschema, referenced_schemas, recursive, path)


DEFAULT_MAX_DEPTH = 2
DEFAULT_MAX_INLININGS = 100


@dataclass
class InlineContext:
    """Context for inlining recursive references."""

    total_inlinings: int = 0
    path: list[str] = field(default_factory=list)
    max_depth: int = DEFAULT_MAX_DEPTH
    max_inlinings: int = DEFAULT_MAX_INLININGS

    def push(self, reference: str) -> bool:
        """Push the current path and check if the limit is reached."""
        self.path.append(reference)
        self.total_inlinings += 1
        return self.total_inlinings < self.max_inlinings and len(self.path) < self.max_depth

    def pop(self) -> None:
        """Pop the current path."""
        self.path.pop()


def unrecurse(referenced_schemas: MovedSchemas, recursive: set[str], context: InlineContext | None = None) -> None:
    """Transform all schemas containing recursive references into non-recursive ones.

    Transformation is done by inlining the referenced schema into the schema that references it up to the
    given limits. When the limit is reached (either `max_depth` or `max_inlinings`), all optional subschemas
    that lead to the recursive reference are removed from the schema. If all such subschemas are required,
    which means infinite recursion, an error is raised.
    """
    # TODO: pass the list of keys that are actually used
    # TODO: Get full paths to every recursive reference - it will save a lot of time here and there will be
    #       much less traversal needed
    # TODO: Reuse already inlined schemas. I.e. if a dependency of the current schema is already inlined,
    #      just reuse it instead of inlining it again. No modifications or traversals at all.
    context = context or InlineContext()
    for name, schema in referenced_schemas.items():
        new_schema = _unrecurse(schema, referenced_schemas, recursive, context)
        if new_schema is not schema:
            referenced_schemas[name] = new_schema


def _unrecurse(
    schema: ObjectSchema, storage: MovedSchemas, recursive: set[str], context: InlineContext
) -> ObjectSchema:
    reference = schema.get("$ref")
    if reference in recursive or not schema:
        return {}
    elif reference is not None:
        return schema
    new = {}
    for key, value in schema.items():
        if key == "additionalProperties" and isinstance(value, dict):
            pass
        elif key == "items":
            pass
        elif key == "properties":
            properties = {}
            for subkey, subschema in value.items():
                if isinstance(subschema, dict):
                    reference = subschema.get("$ref")
                    if reference is None:
                        new_subschema = _unrecurse(subschema, storage, recursive, context)
                        if new_subschema is not subschema:
                            properties[subkey] = new_subschema
                    elif reference in recursive:
                        key, _ = _key_for_reference(reference)
                        referenced_item = storage[key]
                        if context.push(key):
                            replacement = _unrecurse(referenced_item, storage, recursive, context)
                        else:
                            replacement = on_reached_limit(referenced_item, recursive)
                        properties[subkey] = replacement
                    # NOTE: Non-recursive references are left as is
            if properties:
                for subkey, subschema in value.items():
                    if subkey not in properties:
                        properties[subkey] = subschema
                new["properties"] = properties
        elif key == "anyOf":
            pass
        elif key == "patternProperties":
            pass
        elif key == "propertyNames":
            pass
        elif key in ("contains", "if", "then", "else", "not"):
            pass
        elif key in ("allOf", "oneOf", "additionalItems") and isinstance(value, list):
            pass
        else:
            continue
    if not new:
        return schema
    for key, value in schema.items():
        if key not in new:
            new[key] = value
    return new


def on_reached_limit(schema: ObjectSchema, recursive: set[str]) -> ObjectSchema:
    """Remove all optional subschemas that lead to recursive references."""
    # TODO: results of `on_reached_limit` should be cached to avoid recalculating them
    result = _on_reached_limit(schema, recursive)
    if isinstance(result, Ok):
        return result.ok()
    raise result.err()


def _on_reached_limit(schema: ObjectSchema, recursive: set[str]) -> Result[ObjectSchema, InfiniteRecursionError]:
    reference = schema.get("$ref")
    if reference in recursive or not schema:
        return Ok({})
    elif reference is not None:
        return Ok(schema)
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
                new["propertyNames"] = new_subschema
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
        # TODO: Maybe reuse `replacement`?
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
