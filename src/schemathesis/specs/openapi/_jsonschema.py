from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Dict, Iterable, MutableMapping, NewType, Protocol, Set, Union, cast
from urllib.parse import urlsplit
from urllib.request import urlopen

import referencing.retrieval
import requests
from referencing import Registry, Resource, Specification
from referencing.exceptions import Unresolvable, Unretrievable

from ...constants import DEFAULT_RESPONSE_TIMEOUT
from ...internal.copy import fast_deepcopy, merge_into
from ...loaders import load_yaml
from .constants import ALL_KEYWORDS
from .utils import get_type


def load_file_impl(location: str, opener: Callable) -> dict[str, Any]:
    """Load a schema from the given file."""
    with opener(location) as fd:
        return load_yaml(fd)


@lru_cache
def load_file(location: str) -> dict[str, Any]:
    """Load a schema from the given file."""
    return load_file_impl(location, open)


@lru_cache
def load_file_uri(location: str) -> dict[str, Any]:
    """Load a schema from the given file uri."""
    return load_file_impl(location, urlopen)


@lru_cache
def load_remote_uri(uri: str) -> Any:
    """Load the resource and parse it as YAML / JSON."""
    response = requests.get(uri, timeout=DEFAULT_RESPONSE_TIMEOUT / 1000)
    return load_yaml(response.content)


def remove_optional_references(schema: dict[str, Any]) -> None:
    """Remove optional parts of the schema that contain references.

    It covers only the most popular cases, as removing all optional parts is complicated.
    We might fall back to filtering out invalid cases in the future.
    """

    def clean_properties(s: dict[str, Any]) -> None:
        properties = s["properties"]
        required = s.get("required", [])
        for name, value in list(properties.items()):
            if name not in required and contains_ref(value):
                # Drop the property - it will not be generated
                del properties[name]
            elif on_single_item_combinators(value):
                properties.pop(name, None)
            else:
                stack.append(value)

    def clean_items(s: dict[str, Any]) -> None:
        items = s["items"]
        min_items = s.get("minItems", 0)
        if not min_items:
            if isinstance(items, dict) and ("$ref" in items or on_single_item_combinators(items)):
                force_empty_list(s)
            if isinstance(items, list) and any_ref(items):
                force_empty_list(s)

    def clean_additional_properties(s: dict[str, Any]) -> None:
        additional_properties = s["additionalProperties"]
        if isinstance(additional_properties, dict) and "$ref" in additional_properties:
            s["additionalProperties"] = False

    def force_empty_list(s: dict[str, Any]) -> None:
        del s["items"]
        s["maxItems"] = 0

    def any_ref(i: list[dict[str, Any]]) -> bool:
        return any("$ref" in item for item in i)

    def contains_ref(s: dict[str, Any]) -> bool:
        if "$ref" in s:
            return True
        i = s.get("items")
        return (isinstance(i, dict) and "$ref" in i) or isinstance(i, list) and any_ref(i)

    def can_elide(s: dict[str, Any]) -> bool:
        # Whether this schema could be dropped from a list of schemas
        type_ = get_type(s)
        if type_ == ["object"]:
            # Empty object is valid for this schema -> could be dropped
            return s.get("required", []) == [] and s.get("minProperties", 0) == 0
        # Has at least one keyword -> should not be removed
        return not any(k in ALL_KEYWORDS for k in s)

    def on_single_item_combinators(s: dict[str, Any]) -> list[str]:
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
            v = s.get(keyword)
            if v is not None:
                elided = [sub for sub in v if not can_elide(sub)]
                if len(elided) == 1 and contains_ref(elided[0]):
                    found.append(keyword)
        return found

    stack = [schema]
    while stack:
        definition = stack.pop()
        if isinstance(definition, dict):
            # Optional properties
            if "properties" in definition:
                clean_properties(definition)
            # Optional items
            if "items" in definition:
                clean_items(definition)
            # Not required additional properties
            if "additionalProperties" in definition:
                clean_additional_properties(definition)
            for k in on_single_item_combinators(definition):
                del definition[k]


def dynamic_scope(resolver: Resolver) -> tuple[str, ...]:
    return tuple(uri for uri, _ in resolver.dynamic_scope())


def resolve_pointer(document: Any, pointer: str) -> dict | list | str | int | float | None | Unresolvable:
    """Implementation is adapted from Rust's `serde-json` crate.

    Ref: https://github.com/serde-rs/json/blob/master/src/value/mod.rs#L751
    """
    if not pointer:
        return document
    if not pointer.startswith("/"):
        return Unresolvable(pointer)

    def replace(value: str) -> str:
        return value.replace("~1", "/").replace("~0", "~")

    tokens = map(replace, pointer.split("/")[1:])
    target = document
    for token in tokens:
        if isinstance(target, dict):
            target = target.get(token)
            if target is None:
                return Unresolvable(pointer)
        elif isinstance(target, list):
            try:
                target = target[int(token)]
            except IndexError:
                return Unresolvable(pointer)
        else:
            return Unresolvable(pointer)
    return target


# TODO:
#  - Raise custom error when the referenced value is invalid

logger = logging.getLogger(__name__)
MOVED_SCHEMAS_KEY = "x-moved-schemas"
MOVED_SCHEMAS_PREFIX = f"#/{MOVED_SCHEMAS_KEY}/"
MOVED_SCHEMAS_KEY_LENGTH = len(MOVED_SCHEMAS_PREFIX)
SchemaKey = NewType("SchemaKey", str)
ObjectSchema = MutableMapping[str, Any]
Schema = Union[bool, ObjectSchema]
MovedSchemas = Dict[SchemaKey, ObjectSchema]
ReferencesCache = Dict[str, Set[SchemaKey]]


class Resolved(Protocol):
    contents: Any
    resolver: Resolver


class Resolver(Protocol):
    def lookup(self, ref: str) -> Resolved: ...
    def dynamic_scope(self) -> Iterable[tuple[str, Registry]]: ...


@dataclass
class TransformConfig:
    # The name of the keyword that represents nullable values
    # Usually `nullable` in Open API 3 and `x-nullable` in Open API 2
    nullable_key: str
    # Remove properties with the "writeOnly" flag set to `True`.
    # Write only properties are used in requests and should not be present in responses.
    remove_write_only: bool
    # Remove properties with the "readOnly" flag set to `True`.
    # Read only properties are used in responses and should not be present in requests.
    remove_read_only: bool
    # Components that could be potentially referenced by the schema
    components: dict[str, ObjectSchema]
    cache: TransformCache


@dataclass
class TransformCache:
    # Schemas that were referenced and therefore moved to the root of the schema
    moved_schemas: MovedSchemas = field(default_factory=dict)
    replaced_references: dict[str, str] = field(default_factory=dict)
    # Cache for what other referenced are used by the moved references
    schemas_behind_references: dict[str, set[SchemaKey]] = field(default_factory=dict)
    # Known recursive references
    recursive_references: dict[SchemaKey, Set[str]] = field(default_factory=dict)
    # Cache for transformed schemas
    transformed_references: dict[str, ObjectSchema] = field(default_factory=dict)


PLAIN_KEYWORDS = {
    "format",
    "multipleOf",
    "maximum",
    "exclusiveMaximum",
    "minimum",
    "exclusiveMinimum",
    "maxLength",
    "minLength",
    "pattern",
    "maxItems",
    "minItems",
    "uniqueItems",
    "maxProperties",
    "minProperties",
    "required",
    "enum",
    "type",
    "description",
    "title",
    "collectionFormat",
    "default",
}


def _should_skip(schema: ObjectSchema) -> bool:
    if (
        "x-nullable" in schema
        or "writeOnly" in schema
        or "x-writeOnly" in schema
        or "readOnly" in schema
        or schema.get("type") == "file"
    ):
        return False
    nested = set(schema) - PLAIN_KEYWORDS
    if not nested:
        return True
    if nested == {"items"}:
        items = schema["items"]
        if isinstance(items, dict):
            return _should_skip(items)
    if nested == {"properties"}:
        properties = schema["properties"]
        return all(_should_skip(value) for value in properties.values())
    return False


META = {"total": 0, "unique": 0, "iter_schema": 0, "dfs": 0, "inline": 0}
UNIQUE = set()


def to_jsonschema(schema: ObjectSchema, resolver: Resolver, config: TransformConfig) -> ObjectSchema:
    """Transform the given schema to a JSON Schema.

    The resulting schema is compatible with `hypothesis-jsonschema`, specifically it will contain
    only local, non-recursive references.

    This function does the following:

    1. Inlining of non-local references:
       - Resolve all non-local references in the schema.
       - Store the referenced data in the root of the schema under the key "x-moved-references".
       - Modify the references to point to the locally stored data.
       - Repeat this process until all external references are resolved.

    2. Detection of recursive references:
       - Collect all references that lead to themselves, either directly or through a chain of references.

    3. Limited inlining of recursive references:
       - Inline recursive references are up to a maximum depth of `MAX_RECURSION_DEPTH`.
       - At each level, merge the referenced data into the place of the reference.
       - If the maximum recursion depth is reached, remove the reference from the parent schema
         in a way that maintains its validity.
       - Raise an error if the schema describes infinitely recursive data.

    4. Transformation of Open API specific keywords:
       - Transform Open API specific keywords, such as `nullable`, are to their JSON Schema equivalents.

    It accepts shared components and already moved references as input and mutates them to make subsequent calls
    cheaper.
    """
    if _should_skip(schema):
        return schema

    reference_cache_key: str | None = None
    if len(schema) == 1 and "$ref" in schema:
        reference_cache_key = schema["$ref"]
        if reference_cache_key in config.cache.transformed_references:
            return config.cache.transformed_references[reference_cache_key]

    visited = to_self_contained_jsonschema(schema, resolver, config)

    if visited:
        # Look for recursive references places reachable from the schema
        recursive = set()
        cache = config.cache.recursive_references
        for key in visited:
            cached = cache.get(key)
            if cached is not None:
                recursive.update(cached)
        # Leave only references that are used in this particular schema
        # TODO: Track references that are used only in the schema itself - then later traversal is cheaper
        if recursive:
            moved_schemas = {
                key: fast_deepcopy(value) for key, value in config.cache.moved_schemas.items() if key in visited
            }
            inline_recursive_references(moved_schemas, recursive)
        else:
            moved_schemas = {key: value for key, value in config.cache.moved_schemas.items() if key in visited}
        schema[MOVED_SCHEMAS_KEY] = moved_schemas
    if reference_cache_key is not None:
        config.cache.transformed_references[reference_cache_key] = schema
    return schema


def to_self_contained_jsonschema(
    schema: ObjectSchema, root_resolver: Resolver, config: TransformConfig
) -> set[SchemaKey]:
    all_visited = set()
    for ref, key, item, resolver in iter_schema(schema, root_resolver, config):
        original_name = config.cache.replaced_references.get(ref, ref)
        if original_name in config.cache.schemas_behind_references:
            all_visited.update(config.cache.schemas_behind_references[original_name])
            continue
        visited: Set[SchemaKey] = set()
        dfs(item, resolver, visited, config)
        visited.add(key)
        all_visited.update(visited)
        original_name = config.cache.replaced_references.get(ref, ref)
        config.cache.schemas_behind_references[original_name] = visited
    return all_visited


def dfs(item: ObjectSchema, resolver: Resolver, visited: set[SchemaKey], config: TransformConfig) -> None:
    ref = item.get("$ref")
    if isinstance(ref, str):
        if ref.startswith(MOVED_SCHEMAS_PREFIX):
            key = _extract_key_from_ref(ref)
            if key in visited:
                return
            visited.add(key)
            item = config.cache.moved_schemas[key]
        else:
            key = _make_reference_key(ref)
            if key in visited:
                return
            # Mark the schema object as seen
            visited.add(key)
            new_ref = f"{MOVED_SCHEMAS_PREFIX}{key}"
            config.cache.replaced_references[new_ref] = ref
            item["$ref"] = new_ref
            resolved = resolver.lookup(ref)
            config.cache.moved_schemas[key] = resolved.contents
            resolver = resolved.resolver
            item = resolved.contents
        dfs(item, resolver, visited, config)
    else:
        for subschema in iter_subschemas(item):
            dfs(subschema, resolver, visited, config)


def iter_subschemas(item: ObjectSchema) -> Iterable[ObjectSchema]:
    for key, value in item.items():
        if key == "additionalProperties" and isinstance(value, dict):
            yield value
        elif key in ("properties", "patternProperties"):
            for subschema in value.values():
                yield subschema
        elif key in ("additionalProperties", "not"):
            yield value
        elif key == "items":
            if isinstance(value, dict):
                yield value
            elif isinstance(value, list):
                for subschema in value:
                    yield subschema
        elif key in ("anyOf", "oneOf", "allOf"):
            for subschema in value:
                yield subschema


def iter_schema(
    schema: ObjectSchema, resolver: Resolver, config: TransformConfig
) -> Iterable[tuple[str, SchemaKey, ObjectSchema, Resolver]]:
    """Iterate over all schemas reachable from the given schema."""
    stack: list[tuple[ObjectSchema, Resolver, list[str]]] = [(schema, resolver, [])]
    visited = set()
    while stack:
        item, resolver, path = stack.pop()
        ref = item.get("$ref")
        type_ = item.get("type")
        if type_ == "file":
            _replace_file_type(item)
        elif type_ == "object":
            if config.remove_write_only:
                # Write-only properties should not occur in responses
                rewrite_properties(item, is_write_only)
            if config.remove_read_only:
                # Read-only properties should not occur in requests
                rewrite_properties(item, is_read_only)
        if item.get(config.nullable_key) is True:
            _replace_nullable(item, config.nullable_key)
        if isinstance(ref, str):
            key = _ref_to_key(ref)
            path = path + [ref]
            if key in visited:
                original_name = config.cache.replaced_references.get(ref, ref)
                if ref in path:
                    ref_idx = path.index(ref)
                else:
                    ref_idx = None
                if original_name in path:
                    original_idx = path.index(original_name)
                else:
                    original_idx = None
                if original_idx is None or (ref_idx is not None and ref_idx < original_idx):
                    idx = ref_idx
                else:
                    idx = original_idx
                cycle = path[idx:]
                for segment in cycle:
                    key = _ref_to_key(segment)
                    recursive_cache = config.cache.recursive_references.setdefault(key, set())
                    recursive_cache.update(cycle)
                continue
            moved = config.cache.moved_schemas.get(key)
            if moved is not None:
                if not ref.startswith(MOVED_SCHEMAS_PREFIX):
                    item["$ref"] = f"{MOVED_SCHEMAS_PREFIX}{key}"
                item = moved
                yield ref, key, item, resolver
            else:
                yield ref, key, item, resolver
                resolved = resolver.lookup(ref)
                item = resolved.contents
                resolver = resolved.resolver
            visited.add(key)
            stack.append((item, resolver, path))
        else:
            for subschema in iter_subschemas(item):
                stack.append((subschema, resolver, path))


def _ref_to_key(ref: str, cutoff: int = MOVED_SCHEMAS_KEY_LENGTH) -> SchemaKey:
    if ref.startswith(MOVED_SCHEMAS_PREFIX):
        return _extract_key_from_ref(ref, cutoff)
    return _make_reference_key(ref)


def _replace_file_type(item: ObjectSchema) -> None:
    item["type"] = "string"
    item["format"] = "binary"


def rewrite_properties(schema: ObjectSchema, predicate: Callable[[ObjectSchema], bool]) -> None:
    required = schema.get("required", [])
    forbidden = []
    for name, subschema in list(schema.get("properties", {}).items()):
        if predicate(subschema):
            if name in required:
                required.remove(name)
            del schema["properties"][name]
            forbidden.append(name)
    if forbidden:
        forbid_properties(schema, forbidden)
    if not schema.get("required"):
        schema.pop("required", None)
    if not schema.get("properties"):
        schema.pop("properties", None)


def forbid_properties(schema: ObjectSchema, forbidden: list[str]) -> None:
    """Explicitly forbid properties via the `not` keyword."""
    not_schema = schema.setdefault("not", {})
    already_forbidden = not_schema.setdefault("required", [])
    already_forbidden.extend(forbidden)
    not_schema["required"] = list(set(already_forbidden))


def is_write_only(schema: Schema) -> bool:
    if isinstance(schema, bool):
        return False
    return schema.get("writeOnly", False) or schema.get("x-writeOnly", False)


def is_read_only(schema: Schema) -> bool:
    if isinstance(schema, bool):
        return False
    return schema.get("readOnly", False)


def _replace_nullable(item: ObjectSchema, nullable_key: str) -> None:
    del item[nullable_key]
    # Move all other keys to a new object, except for `x-moved-references` which should
    # always be at the root level
    inner = {}
    for key, value in list(item.items()):
        inner[key] = value
        del item[key]
    item["anyOf"] = [inner, {"type": "null"}]


def inline_recursive_references(referenced_schemas: MovedSchemas, references: set[str]) -> None:
    keys = {_ref_to_key(ref) for ref in references}
    originals = {key: fast_deepcopy(value) if key in keys else value for key, value in referenced_schemas.items()}
    for ref in references:
        key = _ref_to_key(ref)
        _inline_recursive_references(referenced_schemas[key], originals, references, (key,))


def _inline_recursive_references(
    item: ObjectSchema | list[ObjectSchema],
    referenced_schemas: MovedSchemas,
    references: set[str],
    path: tuple[str, ...],
) -> None:
    """Inline all recursive references in the given item."""
    META["inline"] += 1
    print(META)
    if isinstance(item, dict):
        ref = item.get("$ref")
        if isinstance(ref, str):
            key = _ref_to_key(ref)
            referenced_item = referenced_schemas[key]
            # TODO: There could be less traversal if we know where refs are located within `refrenced_item`.
            #       Just copy the value and directly jump to the next ref in it, or iterate over them
            if ref in references:
                item.clear()
                if path.count(key) < 3:
                    # Extend with a deep copy as the tree should grow with owned data
                    merge_into(item, referenced_item)
                    _inline_recursive_references(item, referenced_schemas, references, path + (key,))
        else:
            for value in item.values():
                if isinstance(value, (dict, list)):
                    _inline_recursive_references(value, referenced_schemas, references, path)
    else:
        for sub_item in item:
            if isinstance(sub_item, (dict, list)):
                _inline_recursive_references(sub_item, referenced_schemas, references, path)


def _extract_key_from_ref(ref: str, cutoff: int = MOVED_SCHEMAS_KEY_LENGTH) -> SchemaKey:
    return cast(SchemaKey, ref[cutoff:])


def _make_reference_key(reference: str) -> SchemaKey:
    # TODO: use traversal path to make the key - in different files there could be different objects with the same name
    # TODO: or maybe don't use hash at all and have readable keys
    if reference.startswith("file://"):
        reference = reference[7:]
    return cast(SchemaKey, reference.replace("/", "-").replace("#", ""))


def get_remote_schema_retriever(draft: Specification) -> Callable[[str], Resource]:
    """Create a retriever for the given draft."""

    @referencing.retrieval.to_cached_resource(loads=lambda x: x, from_contents=draft.create_resource)  # type: ignore[misc]
    def cached_retrieve(ref: str) -> Any:
        """Resolve non-local reference."""
        logger.debug("Retrieving %s", ref)
        parsed = urlsplit(ref)
        try:
            if parsed.scheme == "":
                return retrieve_from_file(ref)
            if parsed.scheme == "file":
                return retrieve_from_file(parsed.path)
            if parsed.scheme in ("http", "https"):
                retrieved = load_remote_uri(ref)
                logger.debug("Retrieved %s", ref)
                return retrieved
        except Exception as exc:
            logger.debug("Failed to retrieve %s: %s", ref, exc)
            raise
        logger.debug("Unretrievable %s", ref)
        raise Unretrievable(ref)

    return cached_retrieve


def retrieve_from_file(url: str) -> Any:
    url = url.rstrip("/")
    retrieved = load_file_impl(url, open)
    logger.debug("Retrieved %s", url)
    return retrieved
