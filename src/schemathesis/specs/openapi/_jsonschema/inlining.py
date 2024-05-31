from __future__ import annotations

from dataclasses import dataclass, field
import re

from ....internal.result import Err, Ok, Result
from .constants import MOVED_SCHEMAS_PREFIX
from .cache import TransformCache
from .errors import InfiniteRecursionError
from .keys import _key_for_reference
from .types import MovedSchemas, ObjectSchema, Schema


DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_INLININGS = 100


@dataclass
class InlineContext:
    """Context for inlining recursive references."""

    total_inlinings: int = 0
    path: list[str] = field(default_factory=list)
    max_depth: int = DEFAULT_MAX_DEPTH
    max_inlinings: int = DEFAULT_MAX_INLININGS
    cache: dict[str, ObjectSchema] = field(default_factory=dict)

    def push(self, reference: str) -> bool:
        """Push the current path and check if the limit is reached."""
        self.path.append(reference)
        self.total_inlinings += 1
        return self.total_inlinings < self.max_inlinings and self.path.count(reference) < self.max_depth

    def pop(self) -> None:
        """Pop the current path."""
        self.path.pop()


def unrecurse(referenced_schemas: MovedSchemas, cache: TransformCache, context: InlineContext | None = None) -> None:
    """Transform all schemas containing recursive references into non-recursive ones.

    Transformation is done by inlining the referenced schema into the schema that references it up to the
    given limits. When the limit is reached (either `max_depth` or `max_inlinings`), all optional subschemas
    that lead to the recursive reference are removed from the schema. If all such subschemas are required,
    which means infinite recursion, an error is raised.
    """
    # TODO: Get a list of paths to every recursive reference and use it instead of full traversal
    context = context or InlineContext()
    for name, schema in referenced_schemas.items():
        if name in cache.inlined_schemas:
            continue
        new_schema = _unrecurse(schema, referenced_schemas, cache, context)
        if new_schema is not schema:
            cache.recursive_references.discard(f"{MOVED_SCHEMAS_PREFIX}{name}")
            referenced_schemas[name] = new_schema
            context.cache.clear()
        else:
            cache.inlined_schemas.add(name)


def _unrecurse(
    schema: ObjectSchema, storage: MovedSchemas, cache: TransformCache, context: InlineContext
) -> ObjectSchema:
    reference = schema.get("$ref")
    if reference in cache.recursive_references:
        schema_key, _ = _key_for_reference(reference)
        referenced_item = storage[schema_key]
        if context.push(schema_key):
            replacement = _unrecurse(referenced_item, storage, cache, context)
        else:
            replacement = on_reached_limit(referenced_item, cache)
        context.pop()
        return replacement
    if not schema:
        return {}
    new: ObjectSchema = {}
    for key, value in schema.items():
        if key == "additionalProperties" and isinstance(value, dict):
            _unrecurse_additional_properties(new, value, storage, cache, context)
        elif key == "items":
            _unrecurse_items(new, value, storage, cache, context)
        elif key in ("properties", "patternProperties"):
            _unrecurse_keyed_subschemas(new, key, value, storage, cache, context)
        elif key == "propertyNames":
            pass
        elif key in ("contains", "if", "then", "else", "not"):
            pass
        elif key in ("anyOf", "allOf", "oneOf", "additionalItems") and isinstance(value, list):
            _unrecurse_list_of_schemas(new, key, value, storage, cache, context)
        else:
            continue
    if not new:
        return schema
    for key, value in schema.items():
        if key not in new:
            new[key] = value
    return new


def _unrecurse_additional_properties(
    new: ObjectSchema, schema: ObjectSchema, storage: MovedSchemas, cache: TransformCache, context: InlineContext
) -> None:
    replacement = _unrecurse(schema, storage, cache, context)
    if replacement is not schema:
        new["additionalProperties"] = replacement


def _unrecurse_items(
    new: ObjectSchema,
    schema: Schema | list[Schema],
    storage: MovedSchemas,
    cache: TransformCache,
    context: InlineContext,
) -> None:
    if isinstance(schema, dict):
        replacement = _unrecurse(schema, storage, cache, context)
        if replacement is not schema:
            new["items"] = replacement
    elif isinstance(schema, list):
        _unrecurse_list_of_schemas(new, "items", schema, storage, cache, context)


def _unrecurse_keyed_subschemas(
    new: ObjectSchema,
    key: str,
    schema: ObjectSchema,
    storage: MovedSchemas,
    cache: TransformCache,
    context: InlineContext,
) -> None:
    properties = {}
    for subkey, subschema in schema.items():
        if isinstance(subschema, dict):
            reference = subschema.get("$ref")
            if reference is None:
                new_subschema = _unrecurse(subschema, storage, cache, context)
                if new_subschema is not subschema:
                    properties[subkey] = new_subschema
            elif reference in cache.recursive_references:
                schema_key, _ = _key_for_reference(reference)
                if schema_key in context.cache:
                    replacement = context.cache[schema_key]
                else:
                    referenced_item = storage[schema_key]
                    if context.push(schema_key):
                        replacement = _unrecurse(referenced_item, storage, cache, context)
                    else:
                        while "$ref" in referenced_item:
                            schema_key, _ = _key_for_reference(referenced_item["$ref"])
                            referenced_item = storage[schema_key]
                        if schema_key in cache.unrecursed_schemas:
                            replacement = cache.unrecursed_schemas[schema_key]
                        else:
                            replacement = on_reached_limit(referenced_item, cache)
                            cache.unrecursed_schemas[schema_key] = replacement
                    context.cache[schema_key] = replacement
                    context.pop()
                properties[subkey] = replacement
            # NOTE: Non-recursive references are left as is
    if properties:
        for subkey, subschema in schema.items():
            if subkey not in properties:
                properties[subkey] = subschema
        new[key] = properties


def _unrecurse_list_of_schemas(
    new: ObjectSchema,
    keyword: str,
    schemas: list[Schema],
    storage: MovedSchemas,
    cache: TransformCache,
    context: InlineContext,
) -> None:
    new_items = {}
    for idx, subschema in enumerate(schemas):
        if isinstance(subschema, dict):
            reference = subschema.get("$ref")
            if reference is None:
                replacement = _unrecurse(subschema, storage, cache, context)
                if replacement is not subschema:
                    new_items[idx] = replacement
            elif reference in cache.recursive_references:
                schema_key, _ = _key_for_reference(reference)
                referenced_item = storage[schema_key]
                if context.push(keyword):
                    replacement = _unrecurse(referenced_item, storage, cache, context)
                else:
                    if schema_key in cache.unrecursed_schemas:
                        replacement = cache.unrecursed_schemas[schema_key]
                    else:
                        # TODO: Handle recursion error - it still should be propagated here and this property
                        #      should be removed similar to the logic in `on_reached_limit`
                        replacement = on_reached_limit(referenced_item, cache)
                        cache.unrecursed_schemas[schema_key] = replacement
                context.pop()
                new_items[idx] = replacement
    if new_items:
        items: list[Schema] = []
        for idx, subschema in enumerate(schemas):
            if idx in new_items:
                items.append(new_items[idx])
            else:
                items.append(subschema)
        new[keyword] = items


def on_reached_limit(schema: ObjectSchema, cache: TransformCache) -> ObjectSchema:
    """Remove all optional subschemas that lead to recursive references."""
    result = _on_reached_limit(schema, cache)
    if isinstance(result, Ok):
        return result.ok()
    raise result.err()


def _on_reached_limit(schema: ObjectSchema, cache: TransformCache) -> Result[ObjectSchema, InfiniteRecursionError]:
    reference = schema.get("$ref")
    if reference in cache.recursive_references or not schema:
        return Ok({})
    elif reference is not None:
        return Ok(schema)
    new: ObjectSchema = {}
    remove_keywords: list[str] = []
    for key, value in schema.items():
        if key == "additionalProperties" and isinstance(value, dict):
            result = _on_additional_properties_reached_limit(
                new, value, schema.get("minProperties", 0), schema.get("properties", {}), cache
            )
        elif key == "items":
            result = _on_items_reached_limit(new, value, schema.get("minItems", 0), remove_keywords, cache)
        elif key == "properties":
            required = schema.get("required", [])
            result = _on_properties_reached_limit(new, value, required, remove_keywords, cache)
        elif key == "anyOf":
            result = _on_any_of_reached_limit(new, value, cache)
        elif key == "patternProperties":
            result = _on_pattern_properties_reached_limit(
                new, value, schema.get("properties", {}), schema.get("required", []), remove_keywords, cache
            )
        elif key == "propertyNames":
            result = _on_property_names_reached_limit(
                new, value, schema.get("minProperties", 0), remove_keywords, cache
            )
        elif key in ("contains", "if", "then", "else", "not"):
            result = _on_schema_reached_limit(new, value, key, cache, allow_modification=key != "not")
        elif key in ("allOf", "oneOf", "additionalItems") and isinstance(value, list):
            result = _on_list_of_schemas_reached_limit(new, value, key, cache)
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
    cache: TransformCache,
) -> Result[None, InfiniteRecursionError]:
    if schema.get("$ref") in cache.recursive_references:
        if min_properties > len(properties):
            return Err(InfiniteRecursionError("Infinite recursion in additionalProperties"))
        new["additionalProperties"] = False
    else:
        result = _on_reached_limit(schema, cache)
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
    cache: TransformCache,
) -> Result[None, InfiniteRecursionError]:
    if isinstance(schema, dict):
        if schema.get("$ref") in cache.recursive_references:
            if min_items > 0:
                return Err(InfiniteRecursionError("Infinite recursion in items"))
            new["maxItems"] = 0
            remove_keywords.append("items")
        else:
            result = _on_reached_limit(schema, cache)
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
                if subschema.get("$ref") in cache.recursive_references:
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
    new: ObjectSchema, schema: ObjectSchema, min_properties: int, remove_keywords: list[str], cache: TransformCache
) -> Result[None, InfiniteRecursionError]:
    def forbid() -> None:
        new["maxProperties"] = 0
        remove_keywords.append("propertyNames")
        return None

    if schema.get("$ref") in cache.recursive_references:
        if min_properties > 0:
            return Err(InfiniteRecursionError("Infinite recursion in propertyNames"))
        forbid()
    else:
        result = _on_reached_limit(schema, cache)
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
    new: ObjectSchema, schema: ObjectSchema, required: list[str], remove_keywords: list[str], cache: TransformCache
) -> Result[None, InfiniteRecursionError]:
    removal = []
    replacement = {}
    for subkey, subschema in schema.items():
        if isinstance(subschema, dict):
            if subschema.get("$ref") in cache.recursive_references:
                if subkey in required:
                    return Err(InfiniteRecursionError(f"Infinite recursion in a required property: {subkey}"))
                # New schema should not have this property
                removal.append(subkey)
            else:
                result = _on_reached_limit(subschema, cache)
                if isinstance(result, Err):
                    if subkey in required:
                        return result
                    removal.append(subkey)
                else:
                    new_subschema = result.ok()
                    if new_subschema is not subschema:
                        replacement[subkey] = new_subschema
    if removal or replacement:
        for key, subschema in schema.items():
            if key not in replacement and key not in removal:
                replacement[key] = subschema
        if replacement:
            new["properties"] = replacement
        else:
            remove_keywords.append("properties")
    return Ok(None)


def _on_pattern_properties_reached_limit(
    new: ObjectSchema,
    schema: ObjectSchema,
    pattern_properties: dict[str, Schema],
    required: list[str],
    remove_keywords: list[str],
    cache: TransformCache,
) -> Result[None, InfiniteRecursionError]:
    removal = []
    replacement = {}
    for pattern, subschema in schema.items():
        if isinstance(subschema, dict):
            if subschema.get("$ref") in cache.recursive_references:
                if any(re.match(pattern, entry) for entry in required):
                    return Err(InfiniteRecursionError(f"Infinite recursion in {pattern}"))
                # This pattern should be removed from `patternProperties`
                removal.append(pattern)
            else:
                result = _on_reached_limit(subschema, cache)
                if isinstance(result, Err):
                    if any(re.match(pattern, entry) for entry in required):
                        return result
                    removal.append(pattern)
                else:
                    new_subschema = result.ok()
                    if new_subschema is not subschema:
                        replacement[pattern] = new_subschema
    if removal or replacement:
        for pattern, subschema in schema.items():
            if pattern not in removal and pattern not in replacement:
                replacement[pattern] = subschema
        if replacement:
            new["patternProperties"] = replacement
        else:
            remove_keywords.append("patternProperties")
    return Ok(None)


def _on_any_of_reached_limit(
    new: ObjectSchema, schema: list[Schema], cache: TransformCache
) -> Result[None, InfiniteRecursionError]:
    removal: list[int] = []
    replacement: dict[int, Schema] = {}
    for idx, subschema in enumerate(schema):
        if isinstance(subschema, dict):
            if subschema.get("$ref") in cache.recursive_references:
                removal.append(idx)
            else:
                result = _on_reached_limit(subschema, cache)
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
    new: ObjectSchema, schema: ObjectSchema, key: str, cache: TransformCache, allow_modification: bool = True
) -> Result[None, InfiniteRecursionError]:
    if schema.get("$ref") in cache.recursive_references:
        return Err(InfiniteRecursionError(f"Infinite recursion in {key}"))
    result = _on_reached_limit(schema, cache)
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
    new: ObjectSchema, schema: list[ObjectSchema], key: str, cache: TransformCache
) -> Result[None, InfiniteRecursionError]:
    replacement = {}
    for idx, subschema in enumerate(schema):
        if isinstance(subschema, dict):
            if subschema.get("$ref") in cache.recursive_references:
                return Err(InfiniteRecursionError(f"Infinite recursion in {key}"))
            else:
                result = _on_reached_limit(subschema, cache)
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
