from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha1
from typing import Any, Callable, Dict, Union, overload
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen

import jsonschema
import referencing.retrieval
import requests
from referencing import Registry, Resource, Specification
from referencing.exceptions import PointerToNowhere, Unretrievable
from referencing.jsonschema import DRAFT4

from ...constants import DEFAULT_RESPONSE_TIMEOUT
from ...internal.copy import fast_deepcopy
from ...loaders import load_yaml
from .constants import ALL_KEYWORDS
from .converter import to_json_schema_recursive
from .utils import get_type

# Reference resolving will stop after this depth
RECURSION_DEPTH_LIMIT = 100


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


JSONType = Union[None, bool, float, str, list, Dict[str, Any]]


class InliningResolver(jsonschema.RefResolver):
    """Inlines resolved schemas."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault(
            "handlers", {"file": load_file_uri, "": load_file, "http": load_remote_uri, "https": load_remote_uri}
        )
        super().__init__(*args, **kwargs)

    @overload
    def resolve_all(self, item: dict[str, Any], recursion_level: int = 0) -> dict[str, Any]:
        pass

    @overload
    def resolve_all(self, item: list, recursion_level: int = 0) -> list:
        pass

    def resolve_all(self, item: JSONType, recursion_level: int = 0) -> JSONType:
        """Recursively resolve all references in the given object."""
        resolve = self.resolve_all
        if isinstance(item, dict):
            ref = item.get("$ref")
            if isinstance(ref, str):
                url, resolved = self.resolve(ref)
                self.push_scope(url)
                try:
                    # If the next level of recursion exceeds the limit, then we need to copy it explicitly
                    # In other cases, this method create new objects for mutable types (dict & list)
                    next_recursion_level = recursion_level + 1
                    if next_recursion_level > RECURSION_DEPTH_LIMIT:
                        copied = fast_deepcopy(resolved)
                        remove_optional_references(copied)
                        return copied
                    return resolve(resolved, next_recursion_level)
                finally:
                    self.pop_scope()
            return {
                key: resolve(sub_item, recursion_level) if isinstance(sub_item, (dict, list)) else sub_item
                for key, sub_item in item.items()
            }
        if isinstance(item, list):
            return [
                self.resolve_all(sub_item, recursion_level) if isinstance(sub_item, (dict, list)) else sub_item
                for sub_item in item
            ]
        return item

    def resolve_in_scope(self, definition: dict[str, Any], scope: str) -> tuple[list[str], dict[str, Any]]:
        scopes = [scope]
        # if there is `$ref` then we have a scope change that should be used during validation later to
        # resolve nested references correctly
        if "$ref" in definition:
            self.push_scope(scope)
            try:
                new_scope, definition = self.resolve(definition["$ref"])
            finally:
                self.pop_scope()
            scopes.append(new_scope)
        return scopes, definition


class ConvertingResolver(InliningResolver):
    """Convert resolved OpenAPI schemas to JSON Schema.

    When recursive schemas are validated we need to have resolved documents properly converted.
    This approach is the simplest one, since this logic isolated in a single place.
    """

    def __init__(self, *args: Any, nullable_name: Any, is_response_schema: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.nullable_name = nullable_name
        self.is_response_schema = is_response_schema

    def resolve(self, ref: str) -> tuple[str, Any]:
        url, document = super().resolve(ref)
        document = to_json_schema_recursive(
            document, nullable_name=self.nullable_name, is_response_schema=self.is_response_schema
        )
        return url, document


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


@dataclass
class Unresolvable:
    pass


UNRESOLVABLE = Unresolvable()


def resolve_pointer(document: Any, pointer: str) -> dict | list | str | int | float | None | Unresolvable:
    """Implementation is adapted from Rust's `serde-json` crate.

    Ref: https://github.com/serde-rs/json/blob/master/src/value/mod.rs#L751
    """
    if not pointer:
        return document
    if not pointer.startswith("/"):
        return UNRESOLVABLE

    def replace(value: str) -> str:
        return value.replace("~1", "/").replace("~0", "~")

    tokens = map(replace, pointer.split("/")[1:])
    target = document
    for token in tokens:
        if isinstance(target, dict):
            target = target.get(token, UNRESOLVABLE)
            if target is UNRESOLVABLE:
                return UNRESOLVABLE
        elif isinstance(target, list):
            try:
                target = target[int(token)]
            except IndexError:
                return UNRESOLVABLE
        else:
            return UNRESOLVABLE
    return target


# logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
INLINED_REFERENCE_ROOT_KEY = "x-inlined-references"
INLINED_REFERENCE_PREFIX = f"#/{INLINED_REFERENCE_ROOT_KEY}"
SELF_URN = "urn:self"
MAX_RECURSION_DEPTH = 5

ReferencedSchemas = Dict[str, Dict[str, Any]]

# TODO:
#  - use caching for input schemas
#  - avoid mutating the original + don't create a copy unless necessary. HashTrie?
#  - Raise custom error when the referenced value is invalid
#  - What if scope is not needed? I.e. we can just use uri of the resource and then all relative refs will be properly handled. Check how it works with local refs
#  - Traverse only components that may have references (before passing here)


def retrieve_from_file(url: str) -> Any:
    url = url.rstrip("/")
    retrieved = load_file_impl(url, open)
    logger.debug("Retrieved %s", url)
    return retrieved


def inline_references(
    uri: str, scope: str, schema: dict[str, Any], components: dict[str, Any], draft: Specification
) -> dict[str, Any]:
    """Inline all non-local and recursive references in the given schema.

    This function performs three passes:

    1. Inline all non-local references. Each external reference is resolved and the referenced data is stored
       in the root of the schema under a special key. The reference itself is modified so it points to the locally
       stored data. The stored data may contain its own references, therefore this routine is repeated until there
       are no more references to process.

    2. Find all recursive references. At this point, schema is self-contained, meaning it has no external references.
       This step traverses the schema and finds all references whose resolution would lead to themselves immediately,
       or through a chain of references.

    3. Limited inlining of recursive references. The input schema is a tree and this step traverses it and if it finds
       a recursive reference (as detected in the previous step), it merges the referenced data into the place of the
       reference. To avoid infinite recursion, this process is limited by `MAX_RECURSION_DEPTH` iterations.
       At the last iteration, the reference is not inlined, but instead it is removed from the parent schema in a way
       so the parent schema remains valid. It includes removing optional parts of the schema that contain
       this reference. The only exception is when the schema describes infinitely recursive data, in which case an
       error is raised.

    NOTE: Recursive references are inlined up to `MAX_RECURSION_DEPTH` levels because `hypothesis-jsonschema` does not yet
    support generating recursive data.
    """

    @referencing.retrieval.to_cached_resource(loads=lambda x: x, from_contents=draft.create_resource)  # type: ignore[misc]
    def cached_retrieve(ref: str) -> Any:
        """Resolve non-local reference."""
        logger.debug("Retrieving %s", ref)

        if scope:
            base = scope
        else:
            base = uri

        parsed = urlsplit(ref)
        try:
            if parsed.scheme == "":
                url = urljoin(base, ref)
                parsed = urlsplit(url)
                if parsed.scheme == "file":
                    url = parsed.path
                return retrieve_from_file(url)
            if parsed.scheme == "file":
                url = urljoin(base, parsed.netloc)
                return retrieve_from_file(url)
            if parsed.scheme in ("http", "https"):
                retrieved = load_remote_uri(ref)
                logger.debug("Retrieved %s", ref)
                return retrieved
        except Exception as exc:
            logger.debug("Failed to retrieve %s: %s", ref, exc)
            raise
        logger.debug("Unretrievable %s", ref)
        raise Unretrievable(ref)

    registry = Registry(retrieve=cached_retrieve).with_resources(
        [
            ("", Resource(contents=components, specification=DRAFT4)),
            (SELF_URN, Resource(contents=schema, specification=DRAFT4)),
        ]
    )

    logger.debug("Inlining non-local references: %s", schema)
    referenced_schemas = collect_referenced_schemas(schema, registry.resolver())

    if referenced_schemas:
        # Check for recursive references
        references = find_recursive_references(referenced_schemas)
        logger.debug("Found %s recursive references", len(references))
        inline_recursive_references(referenced_schemas, referenced_schemas, references)
        logger.debug("Inlined schema: %s", schema)
    else:
        # Trivial case - no extra processing needed, just remove the key
        logger.debug("No references inlined")
        del schema[INLINED_REFERENCE_ROOT_KEY]
    return schema


def collect_referenced_schemas(schema: dict[str, Any], resolver: Any) -> ReferencedSchemas:
    referenced_schemas: ReferencedSchemas = {}
    schema[INLINED_REFERENCE_ROOT_KEY] = referenced_schemas
    _collect_referenced_schemas(schema, referenced_schemas, resolver)
    return referenced_schemas


def _collect_referenced_schemas(
    item: dict[str, Any] | list, referenced_schemas: ReferencedSchemas, resolver: Any
) -> None:
    logger.debug("Processing %r", item)
    if isinstance(item, dict):
        ref = item.get("$ref")
        if isinstance(ref, str):
            resolved = move_referenced_data(item, ref, referenced_schemas, resolver)
            if resolved is not None:
                item, resolver = resolved
                _collect_referenced_schemas(item, referenced_schemas, resolver)
        else:
            for sub_item in item.values():
                if sub_item and isinstance(sub_item, (dict, list)):
                    _collect_referenced_schemas(sub_item, referenced_schemas, resolver)
    elif isinstance(item, list):
        for sub_item in item:
            if sub_item and isinstance(sub_item, (dict, list)):
                _collect_referenced_schemas(sub_item, referenced_schemas, resolver)


def move_referenced_data(
    item: dict[str, Any], ref: str, referenced_schemas: ReferencedSchemas, resolver: Any
) -> tuple[dict[str, Any], Any] | None:
    if ref.startswith(INLINED_REFERENCE_PREFIX):
        logger.debug("Already inlined %s", ref)
        return None
    logger.debug("Resolving %s", ref)
    try:
        resolved = resolver.lookup(ref)
        # Copy the data as it might be mutated
        contents = fast_deepcopy(resolved.contents)
        key = _make_reference_key(ref)
        referenced_schemas[key] = contents
        new_ref = f"{INLINED_REFERENCE_PREFIX}/{key}"
        item["$ref"] = new_ref
        logger.debug("Inlined reference: %s -> %s", ref, new_ref)
    except PointerToNowhere as exc:
        try:
            resolved = resolver.lookup(f"{SELF_URN}{ref}")
            contents = resolved.contents
            key = _make_reference_key(ref)
            referenced_schemas[key] = contents
            new_ref = f"{INLINED_REFERENCE_PREFIX}/{key}"
            item["$ref"] = new_ref
            logger.debug("Keep local reference: %s", ref)
        except PointerToNowhere:
            logger.debug("Failed to resolve %s: %s", ref, exc)
            raise exc from None
    logger.debug("Resolved %s -> %s", ref, contents)
    return contents, resolved.resolver


def find_recursive_references(schema_storage: ReferencedSchemas) -> set[str]:
    """Find all recursive references in the given schema storage."""
    references: set[str] = set()
    for item in schema_storage.values():
        _find_recursive_references(item, schema_storage, references)
    return references


def _find_recursive_references(
    item: dict[str, Any] | list,
    referenced_schemas: ReferencedSchemas,
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
                referenced_item = referenced_schemas[ref.split("/")[-1]]
                subtree_path = path + (ref,)
                _find_recursive_references(referenced_item, referenced_schemas, references, subtree_path)
        else:
            for value in item.values():
                if isinstance(value, (dict, list)):
                    _find_recursive_references(value, referenced_schemas, references, path)
    else:
        for sub_item in item:
            if isinstance(sub_item, (dict, list)):
                _find_recursive_references(item, referenced_schemas, references, path)


def inline_recursive_references(
    item: dict[str, Any] | list,
    referenced_schemas: ReferencedSchemas,
    references: set[str],
    path: tuple[str, ...] = (),
) -> None:
    """Inline all recursive references in the given item."""
    if isinstance(item, dict):
        ref = item.get("$ref")
        if isinstance(ref, str):
            subtree_path = path + (ref,)
            referenced_item = referenced_schemas[ref.split("/")[-1]]
            if ref in references:
                if path.count(ref) < MAX_RECURSION_DEPTH:
                    logger.debug("Inlining recursive reference: %s", ref)
                    item.clear()
                    item.update(fast_deepcopy(referenced_item))
                    inline_recursive_references(item, referenced_schemas, references, subtree_path)
                else:
                    logger.debug("Max recursion depth reached for %s at %s", ref, path)
            else:
                inline_recursive_references(referenced_item, referenced_schemas, references, subtree_path)
        else:
            for value in item.values():
                if isinstance(value, (dict, list)):
                    inline_recursive_references(value, referenced_schemas, references, path)
    else:
        for sub_item in item:
            if isinstance(sub_item, (dict, list)):
                inline_recursive_references(sub_item, referenced_schemas, references, path)


def _make_reference_key(reference: str) -> str:
    # TODO: use traversal path to make the key
    # TODO: or maybe don't use hash at all and have readable keys
    digest = sha1()
    digest.update(reference.encode("utf-8"))
    return digest.hexdigest()
