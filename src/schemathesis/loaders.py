from __future__ import annotations

import re
import sys
from functools import lru_cache
from typing import TYPE_CHECKING, Any, BinaryIO, Callable, TextIO, TypeVar

from .exceptions import SchemaError, SchemaErrorType, extract_requests_exception_details

if TYPE_CHECKING:
    import yaml

    from .transports.responses import GenericResponse

R = TypeVar("R", bound="GenericResponse")


def load_schema_from_url(loader: Callable[[], R]) -> R:
    import requests

    try:
        response = loader()
    except requests.RequestException as exc:
        url = exc.request.url if exc.request is not None else None
        if isinstance(exc, requests.exceptions.SSLError):
            type_ = SchemaErrorType.CONNECTION_SSL
        elif isinstance(exc, requests.exceptions.ConnectionError):
            type_ = SchemaErrorType.CONNECTION_OTHER
        else:
            type_ = SchemaErrorType.NETWORK_OTHER
        message, extras = extract_requests_exception_details(exc)
        raise SchemaError(message=message, type=type_, url=url, response=exc.response, extras=extras) from exc
    _raise_for_status(response)
    return response


def _raise_for_status(response: GenericResponse) -> None:
    from .transports.responses import get_reason

    status_code = response.status_code
    reason = get_reason(status_code)
    if status_code >= 500:
        message = f"Failed to load schema due to server error (HTTP {status_code} {reason})"
        type_ = SchemaErrorType.HTTP_SERVER_ERROR
    elif status_code >= 400:
        message = f"Failed to load schema due to client error (HTTP {status_code} {reason})"
        if status_code == 403:
            type_ = SchemaErrorType.HTTP_FORBIDDEN
        elif status_code == 404:
            type_ = SchemaErrorType.HTTP_NOT_FOUND
        else:
            type_ = SchemaErrorType.HTTP_CLIENT_ERROR
    else:
        return None
    raise SchemaError(message=message, type=type_, url=response.request.url, response=response, extras=[])


def load_app(path: str) -> Any:
    """Import an application from a string."""
    path, name = (re.split(r":(?![\\/])", path, maxsplit=1) + [""])[:2]
    __import__(path)
    # accessing the module from sys.modules returns a proper module, while `__import__`
    # may return a parent module (system dependent)
    module = sys.modules[path]
    return getattr(module, name)


@lru_cache
def get_yaml_loader() -> type[yaml.SafeLoader]:
    """Create a YAML loader, that doesn't parse specific tokens into Python objects."""
    import yaml

    try:
        from yaml import CSafeLoader as SafeLoader
    except ImportError:
        from yaml import SafeLoader  # type: ignore

    cls: type[yaml.SafeLoader] = type("YAMLLoader", (SafeLoader,), {})
    cls.yaml_implicit_resolvers = {
        key: [(tag, regexp) for tag, regexp in mapping if tag != "tag:yaml.org,2002:timestamp"]
        for key, mapping in cls.yaml_implicit_resolvers.copy().items()
    }

    # Fix pyyaml scientific notation parse bug
    # See PR: https://github.com/yaml/pyyaml/pull/174 for upstream fix
    cls.add_implicit_resolver(  # type: ignore
        "tag:yaml.org,2002:float",
        re.compile(
            r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+]?[0-9]+)?
                       |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
                       |\.[0-9_]+(?:[eE][-+]?[0-9]+)?
                       |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*
                       |[-+]?\.(?:inf|Inf|INF)
                       |\.(?:nan|NaN|NAN))$""",
            re.X,
        ),
        list("-+0123456789."),
    )

    def construct_mapping(self: SafeLoader, node: yaml.Node, deep: bool = False) -> dict[str, Any]:
        if isinstance(node, yaml.MappingNode):
            self.flatten_mapping(node)  # type: ignore
        mapping = {}
        for key_node, value_node in node.value:
            # If the key has a tag different from `str` - use its string value.
            # With this change all integer keys or YAML 1.1 boolean-ish values like "on" / "off" will not be cast to
            # a different type
            if key_node.tag != "tag:yaml.org,2002:str":
                key = key_node.value
            else:
                key = self.construct_object(key_node, deep)  # type: ignore
            mapping[key] = self.construct_object(value_node, deep)  # type: ignore
        return mapping

    cls.construct_mapping = construct_mapping  # type: ignore
    return cls


def load_yaml(stream: str | bytes | TextIO | BinaryIO) -> Any:
    import yaml

    return yaml.load(stream, get_yaml_loader())
