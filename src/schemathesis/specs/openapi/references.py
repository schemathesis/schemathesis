from __future__ import annotations

import sys
from functools import lru_cache
from typing import Any, Callable, Dict, Union, overload
from urllib.request import urlopen

import requests

from schemathesis.core.compat import RefResolutionError, RefResolver
from schemathesis.core.deserialization import deserialize_yaml
from schemathesis.core.jsonschema import references
from schemathesis.core.transforms import deepclone
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT

from .converter import to_json_schema_recursive

# Reference resolving will stop after this depth
RECURSION_DEPTH_LIMIT = 100


def load_file_impl(location: str, opener: Callable) -> dict[str, Any]:
    """Load a schema from the given file."""
    with opener(location) as fd:
        return deserialize_yaml(fd)


@lru_cache
def load_file(location: str) -> dict[str, Any]:
    """Load a schema from the given file."""
    return load_file_impl(location, open)


@lru_cache
def load_file_uri(location: str) -> dict[str, Any]:
    """Load a schema from the given file uri."""
    return load_file_impl(location, urlopen)


def load_remote_uri(uri: str) -> Any:
    """Load the resource and parse it as YAML / JSON."""
    response = requests.get(uri, timeout=DEFAULT_RESPONSE_TIMEOUT)
    return deserialize_yaml(response.content)


JSONType = Union[None, bool, float, str, list, Dict[str, Any]]


class InliningResolver(RefResolver):
    """Inlines resolved schemas."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault(
            "handlers", {"file": load_file_uri, "": load_file, "http": load_remote_uri, "https": load_remote_uri}
        )
        super().__init__(*args, **kwargs)

    if sys.version_info >= (3, 11):

        def resolve(self, ref: str) -> tuple[str, Any]:
            try:
                return super().resolve(ref)
            except RefResolutionError as exc:
                exc.add_note(ref)
                raise
    else:

        def resolve(self, ref: str) -> tuple[str, Any]:
            try:
                return super().resolve(ref)
            except RefResolutionError as exc:
                exc.__notes__ = [ref]
                raise

    @overload
    def resolve_all(self, item: dict[str, Any], recursion_level: int = 0) -> dict[str, Any]: ...

    @overload
    def resolve_all(self, item: list, recursion_level: int = 0) -> list: ...

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
                        copied = deepclone(resolved)
                        references.sanitize(copied)
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
            document,
            nullable_name=self.nullable_name,
            is_response_schema=self.is_response_schema,
            update_quantifiers=False,
        )
        return url, document
