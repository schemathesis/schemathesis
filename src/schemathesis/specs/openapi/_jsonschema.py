from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha1
from typing import Any, Callable, Dict, MutableMapping, Protocol, Union, Iterable, cast
from urllib.parse import urlsplit
from urllib.request import urlopen

import referencing.retrieval
import requests
from referencing import Resource, Specification, Registry
from referencing.exceptions import Unresolvable, Unretrievable

from ...constants import DEFAULT_RESPONSE_TIMEOUT
from ...internal.copy import fast_deepcopy
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
#  - use caching for input schemas
#  - Raise custom error when the referenced value is invalid
#  - Traverse only components that may have references (before passing here)

logger = logging.getLogger(__name__)
MOVED_REFERENCE_ROOT_KEY = "x-moved-references"
MOVED_REFERENCE_PREFIX = f"#/{MOVED_REFERENCE_ROOT_KEY}/"
MOVED_REFERENCE_KEY_LENGTH = len(MOVED_REFERENCE_PREFIX)
ObjectSchema = MutableMapping[str, Any]
Schema = Union[bool, ObjectSchema]
MovedSchemas = Dict[str, ObjectSchema]


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
    # Previously moved references
    moved_references: MovedSchemas
    # Maximum depth of recursive schemas inlining
    max_recursion_depth: int = 5


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
    logger.debug("Input: %s", schema)

    referenced_schemas = to_self_contained_jsonschema(schema, resolver, config)

    if referenced_schemas:
        # Check for recursive references
        references = find_recursive_references(referenced_schemas, config.moved_references)
        logger.debug("Found %s recursive references", len(references))
        if references:
            # TODO: Probably this could be done just once per test run, or exploring just only what is needed
            # and caching already explored schemas
            inline_recursive_references(config.moved_references, config.moved_references, references, config)
        # Leave only references that are used in this particular schema
        schema[MOVED_REFERENCE_ROOT_KEY] = {
            key: value for key, value in config.moved_references.items() if key in referenced_schemas
        }
    else:
        logger.debug("No references found")
    logger.debug("Output: %s", schema)
    return schema


def to_self_contained_jsonschema(schema: ObjectSchema, resolver: Resolver, config: TransformConfig) -> set[str]:
    referenced_schemas: set[str] = set()
    _to_self_contained_jsonschema(schema, referenced_schemas, resolver, config)
    return referenced_schemas


def _to_self_contained_jsonschema(
    item: ObjectSchema | list[ObjectSchema],
    referenced_schemas: set[str],
    resolver: Resolver,
    config: TransformConfig,
) -> None:
    logger.debug("Processing %r", item)
    if isinstance(item, dict):
        type_ = item.get("type")
        if type_ == "file":
            _replace_file_type(item)
        if type_ == "object":
            if config.remove_write_only:
                # Write-only properties should not occur in responses
                rewrite_properties(item, is_write_only)
            if config.remove_read_only:
                # Read-only properties should not occur in requests
                rewrite_properties(item, is_read_only)
        if item.get(config.nullable_key) is True:
            _replace_nullable(item, config.nullable_key)
        ref = item.get("$ref")
        if isinstance(ref, str):
            resolved = move_referenced_data(item, ref, referenced_schemas, config, resolver)
            if resolved is not None:
                item, resolver = resolved
                _to_self_contained_jsonschema(item, referenced_schemas, resolver, config)
        else:
            for sub_item in item.values():
                if sub_item and isinstance(sub_item, (dict, list)):
                    _to_self_contained_jsonschema(sub_item, referenced_schemas, resolver, config)
    else:
        for sub_item in item:
            if sub_item and isinstance(sub_item, (dict, list)):
                _to_self_contained_jsonschema(sub_item, referenced_schemas, resolver, config)


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


def move_referenced_data(
    item: ObjectSchema, ref: str, referenced_schemas: set[str], config: TransformConfig, resolver: Resolver
) -> tuple[Any, Resolver] | None:
    if ref.startswith(MOVED_REFERENCE_PREFIX):
        logger.debug("Already moved %s", ref)
        return None
    if ref.startswith("file://"):
        ref = ref[7:]
    logger.debug("Resolving %s", ref)
    resolved = resolver.lookup(ref)
    key = _make_reference_key(ref)
    config.moved_references[key] = resolved.contents
    referenced_schemas.add(key)
    new_ref = f"{MOVED_REFERENCE_PREFIX}{key}"
    item["$ref"] = new_ref
    logger.debug("Moved reference: %s -> %s", ref, new_ref)
    logger.debug("Resolved %s -> %s", ref, resolved.contents)
    return resolved.contents, resolved.resolver


def find_recursive_references(moved_references: set[str], schema_storage: MovedSchemas) -> set[str]:
    """Find all recursive references in the given schema storage."""
    references: set[str] = set()
    for key in moved_references:
        # TODO: Some objects might already been searched
        _find_recursive_references(schema_storage[key], schema_storage, references)
    return references


def _find_recursive_references(
    item: ObjectSchema | list[ObjectSchema],
    referenced_schemas: MovedSchemas,
    references: set[str],
    path: tuple[str, ...] = (),
) -> None:
    logger.debug("Traversing %r at %r", item, path)
    if isinstance(item, dict):
        ref = item.get("$ref")
        if isinstance(ref, str):
            if ref in path:
                # The reference was already seen in the current traversl path, it means that it's recursive
                logger.debug("Found recursive reference: %s at %r", ref, path)
                references.add(ref)
            else:
                # Otherwise explore the referenced item
                key = ref[MOVED_REFERENCE_KEY_LENGTH:]
                referenced_item = referenced_schemas[key]
                subtree_path = path + (ref,)
                _find_recursive_references(referenced_item, referenced_schemas, references, subtree_path)
        else:
            for value in item.values():
                if isinstance(value, (dict, list)):
                    _find_recursive_references(value, referenced_schemas, references, path)
    else:
        for sub_item in item:
            if isinstance(sub_item, (dict, list)):
                _find_recursive_references(sub_item, referenced_schemas, references, path)


def inline_recursive_references(
    item: ObjectSchema | list[ObjectSchema],
    referenced_schemas: MovedSchemas,
    references: set[str],
    config: TransformConfig,
    path: tuple[str, ...] = (),
) -> None:
    """Inline all recursive references in the given item."""
    if isinstance(item, dict):
        ref = item.get("$ref")
        if isinstance(ref, str):
            subtree_path = path + (ref,)
            referenced_item = referenced_schemas[ref.split("/")[-1]]
            if ref in references:
                if path.count(ref) < config.max_recursion_depth:
                    logger.debug("Inlining recursive reference: %s", ref)
                    item.clear()
                    item.update(fast_deepcopy(referenced_item))
                    inline_recursive_references(item, referenced_schemas, references, config, subtree_path)
                else:
                    logger.debug("Max recursion depth reached for %s at %s", ref, path)
            else:
                inline_recursive_references(referenced_item, referenced_schemas, references, config, subtree_path)
        else:
            for value in item.values():
                if isinstance(value, (dict, list)):
                    inline_recursive_references(value, referenced_schemas, references, config, path)
    else:
        for sub_item in item:
            if isinstance(sub_item, (dict, list)):
                inline_recursive_references(sub_item, referenced_schemas, references, config, path)


def _make_reference_key(reference: str) -> str:
    # TODO: use traversal path to make the key - in different files there could be different objects with the same name
    # TODO: or maybe don't use hash at all and have readable keys
    digest = sha1()
    digest.update(reference.encode("utf-8"))
    return digest.hexdigest()


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
