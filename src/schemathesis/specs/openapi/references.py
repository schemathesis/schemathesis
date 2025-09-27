from __future__ import annotations

import sys
from functools import lru_cache
from typing import Any, Callable, Dict, Union
from urllib.request import urlopen

import requests

from schemathesis.core.compat import RefResolutionError, RefResolver
from schemathesis.core.deserialization import deserialize_yaml
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT


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


class ReferenceResolver(RefResolver):
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
