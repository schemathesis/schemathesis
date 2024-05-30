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
from .inlining import unrecurse
from .config import TransformConfig
from .keys import _key_for_reference, _make_moved_reference
from .constants import MOVED_SCHEMAS_KEY, MOVED_SCHEMAS_PREFIX, PLAIN_KEYWORDS
from .transformation import transform_schema
from .types import ObjectSchema, Resolver, SchemaKey


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
        # Leave only references that are used in this particular schema
        moved_schemas = {key: value for key, value in config.cache.moved_schemas.items() if key in referenced_schemas}
        # TODO: Idea - as we know all the referenced schemas, including recursive ones, there is no reason to maintain
        # mapping of what reference has what recursive references. we just store all recursive references and filter
        # them from `referenced_schemas` when inlining
        #
        # Look for recursive references places reachable from the schema
        if config.cache.recursive_references:
            # Recursive schemas are inlined up to some limit in order to generate self-referential data
            unrecurse(moved_schemas, config.cache)
        schema[MOVED_SCHEMAS_KEY] = moved_schemas
    if reference_cache_key is not None:
        config.cache.transformed_references[reference_cache_key] = schema
    return schema


def to_self_contained_jsonschema(
    root: ObjectSchema, root_resolver: Resolver, config: TransformConfig
) -> set[SchemaKey]:
    """Moves all references to the root of the schema and returns the set of all referenced schemas."""
    referenced = set()
    stack: list[tuple[ObjectSchema, Resolver, list[str]]] = [(root, root_resolver, [])]
    while stack:
        schema, resolver, path = stack.pop()
        reference = schema.get("$ref")
        if isinstance(reference, str):
            key, _ = _key_for_reference(reference)
            if not reference.startswith(MOVED_SCHEMAS_PREFIX):
                moved_reference = _make_moved_reference(key)
                schema["$ref"] = moved_reference
            else:
                moved_reference = reference
            if moved_reference in path:
                # This reference is recursive forming a cycle in `path`
                # Each reference in the cycle is also recursive as it can form the same cycle by
                # traversal a schema that uses that reference
                idx = path.index(moved_reference)
                config.cache.recursive_references.update(path[idx:])
            else:
                if key in referenced:
                    continue
                referenced.add(key)
                moved = config.cache.moved_schemas.get(key)
                if moved is not None:
                    resolved_schema = moved
                else:
                    if reference.startswith("#/definitions/"):
                        # TODO: properly support other locations
                        resolved_schema = config.components["definitions"][reference[len("#/definitions/") :]]
                    else:
                        resolved = resolver.lookup(reference)
                        resolved_schema = resolved.contents
                        resolver = resolved.resolver
                    schema["$ref"] = moved_reference
                    config.cache.moved_schemas[key] = resolved_schema
                stack.append((resolved_schema, resolver, path + [moved_reference]))
        else:
            transform_schema(schema, config)
            for subschema in iter_subschemas(schema):
                stack.append((subschema, resolver, path))
    return referenced


def get_remote_schema_retriever(draft: Specification) -> Callable[[str], Resource]:
    """Create a retriever for the given draft."""

    @referencing.retrieval.to_cached_resource(loads=lambda x: x, from_contents=draft.create_resource)  # type: ignore[misc]
    def cached_retrieve(ref: str) -> Any:
        """Resolve non-local reference."""
        parsed = urlsplit(ref)
        if parsed.scheme == "":
            return load_file(ref, open)
        if parsed.scheme == "file":
            return load_file(parsed.netloc, open)
        if parsed.scheme in ("http", "https"):
            return load_remote_uri(ref)
        raise Unretrievable(ref)

    return cached_retrieve


def load_file(location: str, opener: Callable) -> dict[str, Any]:
    """Load a schema from the given file."""
    with opener(location) as fd:
        return load_yaml(fd)


@lru_cache
def load_remote_uri(uri: str) -> Any:
    """Load the resource and parse it as YAML / JSON."""
    response = requests.get(uri, timeout=DEFAULT_RESPONSE_TIMEOUT / 1000)
    return load_yaml(response.content)
