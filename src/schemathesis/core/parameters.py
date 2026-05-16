from enum import Enum
from typing import TYPE_CHECKING, Literal, TypeAlias

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
