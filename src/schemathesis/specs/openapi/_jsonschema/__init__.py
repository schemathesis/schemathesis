from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Callable, Iterable, Set
from urllib.parse import urlsplit
from urllib.request import urlopen

import referencing.retrieval
import requests
from referencing import Resource, Specification
from referencing.exceptions import Unresolvable, Unretrievable

from ....constants import DEFAULT_RESPONSE_TIMEOUT
from ....loaders import load_yaml
from .iteration import iter_subschemas
from .inlining import inline_recursive_references
from .config import TransformConfig
from .keys import _key_for_reference, _make_moved_reference
from .constants import MOVED_SCHEMAS_KEY, MOVED_SCHEMAS_PREFIX, PLAIN_KEYWORDS
from .transformation import transform_schema
from .types import ObjectSchema, Resolver, SchemaKey


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
        if isinstance(items, list):
            return all(_should_skip(value) for value in items if isinstance(value, dict))
        return True
    if nested == {"properties"}:
        properties = schema["properties"]
        return all(_should_skip(value) for value in properties.values() if isinstance(value, dict))
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

    referenced_schemas = to_self_contained_jsonschema(schema, resolver, config)

    if referenced_schemas:
        # Look for recursive references places reachable from the schema
        recursive = set()
        cache = config.cache.recursive_references
        for key in referenced_schemas:
            cached = cache.get(key)
            if cached is not None:
                recursive.update(cached)
        # Leave only references that are used in this particular schema
        moved_schemas = {key: value for key, value in config.cache.moved_schemas.items() if key in referenced_schemas}
        if recursive:
            # TODO: fix type
            not_inlined = recursive - config.cache.inlined_schemas
            if not_inlined:
                inline_recursive_references(moved_schemas, not_inlined)
                config.cache.inlined_schemas.update(recursive)
        schema[MOVED_SCHEMAS_KEY] = moved_schemas
    if reference_cache_key is not None:
        config.cache.transformed_references[reference_cache_key] = schema
    return schema


def to_self_contained_jsonschema(
    root: ObjectSchema, root_resolver: Resolver, config: TransformConfig
) -> set[SchemaKey]:
    """Moves all references to the root of the schema and returns the set of all referenced schemas."""
    referenced = set()
    # The goal is to find what schemas are reachable from each schema which is done by running DFS
    # on each schema that contains a reference. The result is a set of all referenced schemas.
    for reference, schema, resolver in iter_schema(root, root_resolver, config):
        original_name = config.cache.replaced_references.get(reference, reference)
        if original_name in config.cache.schemas_behind_references:
            referenced.update(config.cache.schemas_behind_references[original_name])
            continue
        referenced_by_schema: Set[SchemaKey] = set()
        traverse_schema(schema, resolver, referenced_by_schema, config)
        key, _ = _key_for_reference(reference)
        referenced_by_schema.add(key)
        referenced.update(referenced_by_schema)
        config.cache.schemas_behind_references[original_name] = referenced_by_schema
    return referenced


def traverse_schema(
    schema: ObjectSchema, resolver: Resolver, referenced: set[SchemaKey], config: TransformConfig
) -> None:
    """Traverse the schema by using DFS and replace all references with their contents."""
    reference = schema.get("$ref")
    if isinstance(reference, str):
        key, is_moved = _key_for_reference(reference)
        if key in referenced:
            return
        referenced.add(key)
        if is_moved:
            # Reference has already been moved, don't replace it
            # Traversal is needed for recursive references
            resolved_schema = config.cache.moved_schemas[key]
        else:
            # Unprocessed reference, move the target
            resolved = resolver.lookup(reference)
            resolved_schema = resolved.contents
            resolver = resolved.resolver
            # Replace the reference
            moved_reference = _make_moved_reference(key)
            schema["$ref"] = moved_reference
            # Update caches
            config.cache.moved_schemas[key] = resolved_schema
            config.cache.replaced_references[moved_reference] = reference
        traverse_schema(resolved_schema, resolver, referenced, config)
    else:
        # Traverse subschemas
        for subschema in iter_subschemas(schema):
            traverse_schema(subschema, resolver, referenced, config)


def iter_schema(
    root: ObjectSchema, resolver: Resolver, config: TransformConfig
) -> Iterable[tuple[str, ObjectSchema, Resolver]]:
    """Iterate over all schemas reachable from the given schema."""
    stack: list[tuple[ObjectSchema, Resolver, list[str]]] = [(root, resolver, [])]
    visited = set()
    while stack:
        schema, resolver, path = stack.pop()
        reference = schema.get("$ref")
        if isinstance(reference, str):
            if reference in path:
                # This reference is recursive forming a cycle in `path`
                # Each reference in the cycle is also recursive as it can form the same cycle by
                # traversal a schema that uses that reference
                idx = path.index(reference)
                cycle = path[idx:]
                for segment in cycle:
                    key, _ = _key_for_reference(segment)
                    cache = config.cache.recursive_references.setdefault(key, set())
                    cache.update(cycle)
            else:
                key, _ = _key_for_reference(reference)
                moved = config.cache.moved_schemas.get(key)
                if moved is not None:
                    if not reference.startswith(MOVED_SCHEMAS_PREFIX):
                        # The referenced schema was already moved, but not all usages of this reference were updated
                        moved_reference = _make_moved_reference(key)
                        schema["$ref"] = moved_reference
                        reference = moved_reference
                    schema = moved
                    yield reference, schema, resolver
                else:
                    yield reference, schema, resolver
                    resolved = resolver.lookup(reference)
                    schema = resolved.contents
                    resolver = resolved.resolver
                    reference = _make_moved_reference(key)
                visited.add(key)
                stack.append((schema, resolver, path + [reference]))
        else:
            transform_schema(schema, config)
            for subschema in iter_subschemas(schema):
                stack.append((subschema, resolver, path))


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
