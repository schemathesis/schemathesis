from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Mapping, Any

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


def unrecurse(referenced_schemas: MovedSchemas, cache: TransformCache) -> None:
    """Transform all schemas containing recursive references into non-recursive ones.

    Transformation is done by inlining the referenced schema into the schema that references it up to the
    given limits. When the limit is reached (either `max_depth` or `max_inlinings`), all optional subschemas
    that lead to the recursive reference are removed from the schema. If all such subschemas are required,
    which means infinite recursion, an error is raised.
    """
    # TODO: Get a list of paths to every recursive reference and use it instead of full traversal
    context = InlineContext()
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
            result = on_reached_limit(referenced_item, cache)
            if isinstance(result, Err):
                raise NotImplementedError("TODO!")
            replacement = result.ok()
        context.pop()
        return replacement
    if not schema:
        return {}
    new: ObjectSchema = {}
    for key, value in schema.items():
        if key in (
            "additionalProperties",
            "contains",
            "if",
            "then",
            "else",
            "not",
            "propertyNames",
            "items",
        ) and isinstance(value, dict):
            _unrecurse_schema(new, key, value, storage, cache, context)
        elif key in ("properties", "patternProperties"):
            _unrecurse_keyed_subschemas(new, key, value, storage, cache, context)
        elif key in ("anyOf", "allOf", "oneOf", "additionalItems", "items") and isinstance(value, list):
            _unrecurse_list_of_schemas(new, key, value, storage, cache, context)
        else:
            continue
    if not new:
        return schema
    for key, value in schema.items():
        if key not in new:
            new[key] = value
    return new


def _unrecurse_schema(
    new: ObjectSchema,
    key: str,
    schema: ObjectSchema,
    storage: MovedSchemas,
    cache: TransformCache,
    context: InlineContext,
) -> None:
    replacement = _unrecurse(schema, storage, cache, context)
    if replacement is not schema:
        new[key] = replacement


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
                            result = on_reached_limit(referenced_item, cache)
                            if isinstance(result, Err):
                                raise NotImplementedError("TODO!")
                            else:
                                replacement = result.ok()
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
                        result = on_reached_limit(referenced_item, cache)
                        if isinstance(result, Err):
                            raise NotImplementedError("TODO!")
                        else:
                            replacement = result.ok()
                            cache.unrecursed_schemas[schema_key] = replacement
                context.pop()
                new_items[idx] = replacement

    if new_items:
        items = []
        for idx, subschema in enumerate(schemas):
            if idx in new_items:
                items.append(new_items[idx])
            else:
                items.append(subschema)
        new[keyword] = items


@dataclass
class NewSchemaContext:
    original: ObjectSchema
    cache: TransformCache
    new: dict[str, Any] = field(default_factory=dict)
    remove: list[str] = field(default_factory=list)

    def set_keyword(self, keyword: str, value: Any) -> None:
        self.new[keyword] = value

    def remove_keyword(self, keyword: str) -> None:
        self.remove.append(keyword)

    @property
    def properties(self) -> dict[str, Schema]:
        return self.original.get("properties", {})

    @property
    def min_properties(self) -> int:
        return self.original.get("minProperties", 0)

    @property
    def min_items(self) -> int:
        return self.original.get("minItems", 0)

    @property
    def required(self) -> list[str]:
        return self.original.get("required", [])

    def is_recursive_reference(self, reference: Any) -> bool:
        return reference in self.cache.recursive_references

    def has_recursive_reference(self, schema: ObjectSchema) -> bool:
        reference = schema.get("$ref")
        return reference is not None and self.is_recursive_reference(reference)

    def has_required_properties_matching(self, pattern: str) -> bool:
        return any(re.match(pattern, entry) for entry in self.required)

    def _maybe_replace_mapping(
        self,
        keyword: str,
        obj: dict[str, Schema],
        replacement: ObjectSchema,
        removal: list[str],
    ) -> None:
        if removal or replacement:
            # Move the rest of the properties to the new place
            for key, subschema in obj.items():
                if key not in replacement and key not in removal:
                    replacement[key] = subschema
            if replacement:
                self.set_keyword(keyword, replacement)
            else:
                self.remove_keyword(keyword)

    def _maybe_replace_list(
        self,
        keyword: str,
        schemas: list[Schema],
        replacement: Mapping[int, Schema],
        removal: list[int] | None = None,
    ) -> None:
        removal = removal or []
        if replacement or removal:
            items = []
            for idx, subschema in enumerate(schemas):
                if idx in replacement:
                    items.append(replacement[idx])
                elif idx not in removal:
                    items.append(subschema)
            self.set_keyword(keyword, items)

    def forbid_additional_properties(self) -> Err[InfiniteRecursionError] | None:
        if self.min_properties > len(self.properties):
            return Err(InfiniteRecursionError("Infinite recursion in additionalProperties"))
        self.set_keyword("additionalProperties", False)
        return None

    def forbid_items(self) -> Err[InfiniteRecursionError] | None:
        if self.min_items > 0:
            return Err(InfiniteRecursionError("Infinite recursion in items"))
        self.set_keyword("maxItems", 0)
        self.remove_keyword("items")

    def forbid_property_names(self) -> Err[InfiniteRecursionError] | None:
        if self.min_properties > 0:
            return Err(InfiniteRecursionError("Infinite recursion in propertyNames"))
        self.set_keyword("maxProperties", 0)
        self.remove_keyword("propertyNames")

    def on_additional_properties(
        self,
        schema: ObjectSchema,
    ) -> Result[None, InfiniteRecursionError]:
        if self.has_recursive_reference(schema):
            error = self.forbid_additional_properties()
            if error is not None:
                return error
        else:
            result = on_reached_limit(schema, self.cache)
            if isinstance(result, Err):
                error = self.forbid_additional_properties()
                if error is not None:
                    return error
            else:
                replacement = result.ok()
                if replacement is not schema:
                    self.set_keyword("additionalProperties", replacement)
        return Ok(None)

    def on_items(
        self,
        schema: ObjectSchema | list[Schema],
    ) -> Result[None, InfiniteRecursionError]:
        if isinstance(schema, dict):
            if self.has_recursive_reference(schema):
                error = self.forbid_items()
                if error is not None:
                    return error
            else:
                result = on_reached_limit(schema, self.cache)
                if isinstance(result, Err):
                    error = self.forbid_items()
                    if error is not None:
                        return error
                else:
                    replacement = result.ok()
                    if replacement is not schema:
                        self.set_keyword("items", replacement)
        elif isinstance(schema, list):
            for idx, subschema in enumerate(schema):
                if isinstance(subschema, dict):
                    if self.has_recursive_reference(subschema):
                        if self.min_items > idx:
                            return Err(InfiniteRecursionError("Infinite recursion in items"))
                        self.set_keyword("maxItems", idx)
                        if idx == 0:
                            self.remove_keyword("items")
                        else:
                            self.set_keyword("items", schema[:idx])
                        break
        return Ok(None)

    def on_properties(self, properties: dict[str, Schema]) -> Result[None, InfiniteRecursionError]:
        removal = []
        replacement = {}
        for key, subschema in properties.items():
            if isinstance(subschema, dict):
                if self.has_recursive_reference(subschema):
                    if key in self.required:
                        return Err(InfiniteRecursionError(f"Infinite recursion in a required property: {key}"))
                    # New schema should not have this property
                    removal.append(key)
                else:
                    result = on_reached_limit(subschema, self.cache)
                    if isinstance(result, Err):
                        if key in self.required:
                            return result
                        removal.append(key)
                    else:
                        new = result.ok()
                        if new is not subschema:
                            replacement[key] = new
        self._maybe_replace_mapping("properties", properties, replacement, removal)
        return Ok(None)

    def on_any_of(self, schemas: list[Schema]) -> Result[None, InfiniteRecursionError]:
        removal: list[int] = []
        replacement: dict[int, Schema] = {}
        for idx, subschema in enumerate(schemas):
            if isinstance(subschema, dict):
                if self.has_recursive_reference(subschema):
                    removal.append(idx)
                else:
                    result = on_reached_limit(subschema, self.cache)
                    if isinstance(result, Err):
                        removal.append(idx)
                    else:
                        new = result.ok()
                        if new is not subschema:
                            replacement[idx] = new
        if len(removal) == len(schemas):
            return Err(InfiniteRecursionError("Infinite recursion in anyOf"))
        self._maybe_replace_list("anyOf", schemas, replacement, removal)
        return Ok(None)

    def on_pattern_properties(
        self,
        pattern_properties: dict[str, Schema],
    ) -> Result[None, InfiniteRecursionError]:
        removal = []
        replacement = {}
        for pattern, subschema in pattern_properties.items():
            if isinstance(subschema, dict):
                if self.has_recursive_reference(subschema):
                    if self.has_required_properties_matching(pattern):
                        return Err(InfiniteRecursionError(f"Infinite recursion in {pattern}"))
                    # TODO: All matching properties should be removed from `properties` too
                    removal.append(pattern)
                else:
                    result = on_reached_limit(subschema, self.cache)
                    if isinstance(result, Err):
                        if self.has_required_properties_matching(pattern):
                            return result
                        removal.append(pattern)
                    else:
                        new = result.ok()
                        if new is not subschema:
                            replacement[pattern] = new
        self._maybe_replace_mapping("patternProperties", pattern_properties, replacement, removal)
        return Ok(None)

    def on_property_names(self, schema: ObjectSchema) -> Result[None, InfiniteRecursionError]:
        if self.has_recursive_reference(schema):
            error = self.forbid_property_names()
            if error is not None:
                return error
        else:
            result = on_reached_limit(schema, self.cache)
            if isinstance(result, Err):
                error = self.forbid_property_names()
                if error is not None:
                    return error
            else:
                new = result.ok()
                if new is not schema:
                    self.set_keyword("propertyNames", new)
        return Ok(None)

    def on_schema(
        self, schema: ObjectSchema, key: str, allow_modification: bool = True
    ) -> Result[None, InfiniteRecursionError]:
        if self.has_recursive_reference(schema):
            return Err(InfiniteRecursionError(f"Infinite recursion in {key}"))
        result = on_reached_limit(schema, self.cache)
        if isinstance(result, Err):
            return result
        new = result.ok()
        if new is not schema:
            if allow_modification:
                self.set_keyword(key, new)
            else:
                # `not` can't be modified
                return Err(InfiniteRecursionError(f"Infinite recursion in {key}"))
        return Ok(None)

    def on_list_of_schemas(self, keyword: str, schemas: list[Schema]) -> Result[None, InfiniteRecursionError]:
        replacement = {}
        for idx, subschema in enumerate(schemas):
            if isinstance(subschema, dict):
                if self.has_recursive_reference(subschema):
                    return Err(InfiniteRecursionError(f"Infinite recursion in {keyword}"))
                else:
                    result = on_reached_limit(subschema, self.cache)
                    if isinstance(result, Err):
                        return result
                    new = result.ok()
                    if new is not subschema:
                        replacement[idx] = new
        self._maybe_replace_list(keyword, schemas, replacement)
        return Ok(None)

    def dispatch(self) -> Result[ObjectSchema, InfiniteRecursionError]:
        reference = self.original.get("$ref")
        if self.has_recursive_reference(self.original) or not self.original:
            return Ok({})
        elif reference is not None:
            return Ok(self.original)
        for keyword, value in self.original.items():
            if keyword == "additionalProperties" and isinstance(value, dict):
                result = self.on_additional_properties(value)
            elif keyword == "items":
                result = self.on_items(value)
            elif keyword == "properties":
                result = self.on_properties(value)
            elif keyword == "anyOf":
                result = self.on_any_of(value)
            elif keyword == "patternProperties":
                result = self.on_pattern_properties(value)
            elif keyword == "propertyNames":
                result = self.on_property_names(value)
            elif keyword in ("contains", "if", "then", "else", "not"):
                result = self.on_schema(value, keyword, allow_modification=keyword != "not")
            elif keyword in ("allOf", "oneOf", "additionalItems") and isinstance(value, list):
                result = self.on_list_of_schemas(keyword, value)
            else:
                continue
            if isinstance(result, Err):
                return result
        return Ok(self.finish())

    def finish(self) -> ObjectSchema:
        if not self.new and not self.remove:
            return self.original
        for keyword, value in self.original.items():
            if keyword not in self.remove and keyword not in self.new:
                self.set_keyword(keyword, value)
        return self.new


def on_reached_limit(schema: ObjectSchema, cache: TransformCache) -> Result[ObjectSchema, InfiniteRecursionError]:
    """Remove all optional subschemas that lead to recursive references."""
    return NewSchemaContext(schema, cache).dispatch()
