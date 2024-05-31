from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Any

from ....internal.result import Err, Ok, Result
from .cache import TransformCache
from .errors import InfiniteRecursionError
from .keys import _key_for_reference, _make_moved_reference
from .types import MovedSchemas, ObjectSchema, Schema, SchemaKey


DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_INLININGS = 100


@dataclass
class UnrecurseContext:
    """Context for unrecursing schemas with recursive references."""

    schemas: MovedSchemas
    transform_cache: TransformCache
    total_inlinings: int
    path: list[str]
    max_depth: int
    max_inlinings: int
    local_cache: dict[str, ObjectSchema]

    __slots__ = ("schemas", "transform_cache", "total_inlinings", "path", "max_depth", "max_inlinings", "local_cache")

    @classmethod
    def new(cls, schemas: MovedSchemas, cache: TransformCache) -> UnrecurseContext:
        return cls(
            schemas,
            cache,
            total_inlinings=0,
            path=[],
            max_depth=DEFAULT_MAX_DEPTH,
            max_inlinings=DEFAULT_MAX_INLININGS,
            local_cache={},
        )

    def push(self, reference: str) -> bool:
        """Push the current path and check if the limit is reached."""
        self.path.append(reference)
        self.total_inlinings += 1
        return self.total_inlinings < self.max_inlinings and self.path.count(reference) < self.max_depth

    def pop(self) -> None:
        """Pop the current path."""
        self.path.pop()

    def reset(self) -> None:
        """Reset the context."""
        self.total_inlinings = 0
        self.local_cache.clear()

    def get_cached_replacement(self, key: SchemaKey) -> ObjectSchema | None:
        return self.local_cache.get(key)

    def set_cached_replacement(self, key: SchemaKey, replacement: ObjectSchema) -> None:
        self.local_cache[key] = replacement

    def is_unrecursed(self, key: SchemaKey) -> bool:
        return key in self.transform_cache.unrecursed_schemas

    def discard_recursive_reference(self, key: SchemaKey) -> None:
        self.transform_cache.recursive_references.discard(_make_moved_reference(key))

    def to_leaf_schema(self, schema: ObjectSchema) -> Result[ObjectSchema, InfiniteRecursionError]:
        return LeafTransformer.run(schema, self)


def unrecurse(schemas: MovedSchemas, cache: TransformCache) -> None:
    """Transform all schemas containing recursive references into non-recursive ones.

    Transformation is done by inlining the referenced schema into the schema that references it up to the
    given limits. When the limit is reached (either `max_depth` or `max_inlinings`), all optional subschemas
    that lead to the recursive reference are removed from the schema. If all such subschemas are required,
    which means infinite recursion, an error is raised.
    """
    # TODO: Get a list of paths to every recursive reference and use it instead of full traversal
    ctx = UnrecurseContext.new(schemas, cache)
    for name, original in schemas.items():
        if ctx.is_unrecursed(name):
            continue
        result = SchemaTransformer(original, ctx, new={}, remove=[]).dispatch()
        if isinstance(result, Err):
            raise NotImplementedError("TODO!")
        new = result.ok()
        if new is not original:
            schemas[name] = new
        ctx.discard_recursive_reference(name)
        ctx.reset()


@dataclass
class BaseTransformer:
    original: ObjectSchema
    ctx: UnrecurseContext
    new: dict[str, Any]
    remove: list[str]

    __slots__ = ("original", "ctx", "new", "remove")

    @classmethod
    def run(cls, original: ObjectSchema, ctx: UnrecurseContext) -> Result[ObjectSchema, InfiniteRecursionError]:
        return cls(original, ctx, new={}, remove=[]).dispatch()

    def descend(self, schema: ObjectSchema) -> Result[ObjectSchema, InfiniteRecursionError]:
        return self.__class__.run(schema, self.ctx)

    def dispatch(self) -> Result[ObjectSchema, InfiniteRecursionError]:
        raise NotImplementedError


@dataclass
class SchemaTransformer(BaseTransformer):
    def dispatch(self) -> Result[ObjectSchema, InfiniteRecursionError]:
        if not self.original:
            return Ok({})
        reference = self.original.get("$ref")
        if reference in self.ctx.transform_cache.recursive_references:
            schema_key, _ = _key_for_reference(reference)
            referenced_item = self.ctx.schemas[schema_key]
            if self.ctx.push(schema_key):
                result = self.descend(referenced_item)
                if isinstance(result, Err):
                    raise NotImplementedError("TODO!")
                replacement = result.ok()
            else:
                result = LeafTransformer.run(referenced_item, self.ctx)
                if isinstance(result, Err):
                    raise NotImplementedError("TODO!")
                replacement = result.ok()
            self.ctx.pop()
            return Ok(replacement)
        for key, value in self.original.items():
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
                r = self.on_schema(key, value)
            elif key in ("properties", "patternProperties"):
                r = self.on_keyed_subschemas(key, value)
            elif key in ("anyOf", "allOf", "oneOf", "additionalItems", "items") and isinstance(value, list):
                r = self.on_list_of_schemas(key, value)
            else:
                continue
            if isinstance(r, Err):
                return r
        return Ok(self.finish())

    def finish(self) -> ObjectSchema:
        if not self.new:
            return self.original
        for key, value in self.original.items():
            if key not in self.new:
                self.new[key] = value
        return self.new

    def on_schema(self, key: str, schema: ObjectSchema) -> Result[None, InfiniteRecursionError]:
        result = self.descend(schema)
        if isinstance(result, Err):
            return result
        else:
            new = result.ok()
            if new is not schema:
                # TODO: reuse set_keyword
                self.new[key] = new
        return Ok(None)

    def on_keyed_subschemas(self, key: str, schema: ObjectSchema) -> Result[None, InfiniteRecursionError]:
        properties = {}
        for subkey, subschema in schema.items():
            if isinstance(subschema, dict):
                reference = subschema.get("$ref")
                if reference is None:
                    result = self.descend(subschema)
                    if isinstance(result, Err):
                        raise NotImplementedError("TODO!")
                    new = result.ok()
                    if new is not subschema:
                        properties[subkey] = new
                elif reference in self.ctx.transform_cache.recursive_references:
                    schema_key, _ = _key_for_reference(reference)
                    cached = self.ctx.get_cached_replacement(schema_key)
                    if cached is not None:
                        replacement = cached
                    else:
                        referenced_item = self.ctx.schemas[schema_key]
                        if self.ctx.push(schema_key):
                            result = self.descend(referenced_item)
                            if isinstance(result, Err):
                                raise NotImplementedError("TODO!")
                            replacement = result.ok()
                        else:
                            while "$ref" in referenced_item:
                                schema_key, _ = _key_for_reference(referenced_item["$ref"])
                                referenced_item = self.ctx.schemas[schema_key]
                            if schema_key in self.ctx.transform_cache.unrecursed_schemas:
                                replacement = self.ctx.transform_cache.unrecursed_schemas[schema_key]
                            else:
                                result = self.ctx.to_leaf_schema(referenced_item)
                                if isinstance(result, Err):
                                    raise NotImplementedError("TODO!")
                                else:
                                    replacement = result.ok()
                                    self.ctx.transform_cache.unrecursed_schemas[schema_key] = replacement
                        self.ctx.set_cached_replacement(schema_key, replacement)
                        self.ctx.pop()
                    properties[subkey] = replacement
                # NOTE: Non-recursive references are left as is
        if properties:
            for subkey, subschema in schema.items():
                if subkey not in properties:
                    properties[subkey] = subschema
            self.new[key] = properties
        return Ok(None)

    def on_list_of_schemas(self, keyword: str, schemas: list[Schema]) -> Result[None, InfiniteRecursionError]:
        new_items = {}
        for idx, subschema in enumerate(schemas):
            if isinstance(subschema, dict):
                reference = subschema.get("$ref")
                if reference is None:
                    result = self.descend(subschema)
                    if isinstance(result, Err):
                        raise NotImplementedError("TODO!")
                    replacement = result.ok()
                    if replacement is not subschema:
                        new_items[idx] = replacement
                elif reference in self.ctx.transform_cache.recursive_references:
                    schema_key, _ = _key_for_reference(reference)
                    referenced_item = self.ctx.schemas[schema_key]
                    if self.ctx.push(keyword):
                        result = self.descend(referenced_item)
                        if isinstance(result, Err):
                            raise NotImplementedError("TODO!")
                        replacement = result.ok()
                    else:
                        if schema_key in self.ctx.transform_cache.unrecursed_schemas:
                            replacement = self.ctx.transform_cache.unrecursed_schemas[schema_key]
                        else:
                            result = self.ctx.to_leaf_schema(referenced_item)
                            if isinstance(result, Err):
                                raise NotImplementedError("TODO!")
                            else:
                                replacement = result.ok()
                                self.ctx.transform_cache.unrecursed_schemas[schema_key] = replacement
                    self.ctx.pop()
                    new_items[idx] = replacement

        if new_items:
            items: list[Schema] = []
            for idx, subschema in enumerate(schemas):
                if idx in new_items:
                    items.append(new_items[idx])
                else:
                    items.append(subschema)
            self.new[keyword] = items
        return Ok(None)


@dataclass
class LeafTransformer(BaseTransformer):
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
        return reference in self.ctx.transform_cache.recursive_references

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
        return None

    def forbid_property_names(self) -> Err[InfiniteRecursionError] | None:
        if self.min_properties > 0:
            return Err(InfiniteRecursionError("Infinite recursion in propertyNames"))
        self.set_keyword("maxProperties", 0)
        self.remove_keyword("propertyNames")
        return None

    def on_additional_properties(
        self,
        schema: ObjectSchema,
    ) -> Result[None, InfiniteRecursionError]:
        if self.has_recursive_reference(schema):
            error = self.forbid_additional_properties()
            if error is not None:
                return error
        else:
            result = self.descend(schema)
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
                result = self.descend(schema)
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
                    result = self.descend(subschema)
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
                    result = self.descend(subschema)
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
                    result = self.descend(subschema)
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
            result = self.descend(schema)
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
        result = self.descend(schema)
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
                    result = self.descend(subschema)
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
