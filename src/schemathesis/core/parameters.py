from collections.abc import Iterator, Mapping
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, TypeVar
from urllib.parse import quote

T = TypeVar("T")

# Attribute name on `Case` / `APIOperation` holding generated values for a parameter location.
ContainerName: TypeAlias = Literal["path_parameters", "query", "headers", "cookies", "body"]

LOCATION_TO_CONTAINER: dict[str, ContainerName] = {
    "path": "path_parameters",
    "query": "query",
    "header": "headers",
    "cookie": "cookies",
    "body": "body",
}


class RawQueryString(str):
    """Internal wrapper for pre-serialized raw query string chunks."""

    __slots__ = ()


# Internal key used to carry raw query string payloads for OpenAPI 3.2 `in: querystring`.
RAW_QUERY_STRING_KEY = "x-schemathesis-raw-query-string"


# `DelimitedValue`/`EncodedPath` are `str` subclasses; flatten containers holding them with
# `plain_str_values` before any jsonschema_rs call, which rejects `str` subclasses.
class DelimitedValue(str):
    """A delimiter-joined array/object parameter.

    The string is the logical form; `encoded` is the wire form (each element percent-encoded,
    delimiter left literal) so a server can split it unambiguously.
    """

    encoded: str
    __slots__ = ("encoded",)

    def __new__(cls, logical: str, encoded: str) -> "DelimitedValue":
        instance = super().__new__(cls, logical)
        instance.encoded = encoded
        return instance


class EncodedPath(str):
    """A fully percent-encoded path parameter value. Re-quoting it is a no-op."""

    __slots__ = ()


def plain_str_values(container: dict[str, Any]) -> dict[str, Any]:
    """Downcast `str` subclasses to plain `str`; jsonschema_rs rejects subclasses."""
    converted = None
    for key, value in container.items():
        if type(value) is not str and isinstance(value, str):
            if converted is None:
                converted = dict(container)
            converted[key] = str(value)
    return converted if converted is not None else container


def split_delimited_query(params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Split off pre-encoded delimited values, returning their `key=value` chunk and the rest."""
    raw_parts = [
        f"{quote(str(key), safe='')}={value.encoded}"
        for key, value in params.items()
        if isinstance(value, DelimitedValue)
    ]
    if not raw_parts:
        return "", params
    rest = {key: value for key, value in params.items() if not isinstance(value, DelimitedValue)}
    return "&".join(raw_parts), rest


class ParameterLocation(str, Enum):
    """API parameter location."""

    QUERY = "query"
    HEADER = "header"
    PATH = "path"
    COOKIE = "cookie"
    BODY = "body"
    UNKNOWN = None

    if TYPE_CHECKING:
        container_name: ContainerName
        is_in_header: bool


# Stored as direct instance attributes (faster than @property + descriptor dispatch
# on every access — these are queried in hot strategy / check / examples paths).
ParameterLocation.QUERY.container_name = "query"
ParameterLocation.HEADER.container_name = "headers"
ParameterLocation.PATH.container_name = "path_parameters"
ParameterLocation.COOKIE.container_name = "cookies"
ParameterLocation.BODY.container_name = "body"

ParameterLocation.QUERY.is_in_header = False
ParameterLocation.HEADER.is_in_header = True
ParameterLocation.PATH.is_in_header = False
ParameterLocation.COOKIE.is_in_header = True
ParameterLocation.BODY.is_in_header = False
ParameterLocation.UNKNOWN.is_in_header = False

HEADER_LOCATIONS = frozenset([ParameterLocation.HEADER, ParameterLocation.COOKIE])

CONTAINER_TO_LOCATION: dict[ContainerName, ParameterLocation] = {
    "path_parameters": ParameterLocation.PATH,
    "query": ParameterLocation.QUERY,
    "headers": ParameterLocation.HEADER,
    "cookies": ParameterLocation.COOKIE,
    "body": ParameterLocation.BODY,
}


def iter_path_parameters(parameters: Mapping[str, T]) -> Iterator[tuple[str, T]]:
    """Yield `(name, value)` for entries keyed `path.<name>` (the OpenAPI link parameter convention)."""
    prefix = f"{ParameterLocation.PATH.value}."
    for key, value in parameters.items():
        if key.startswith(prefix):
            yield key[len(prefix) :], value
