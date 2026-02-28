from enum import Enum

LOCATION_TO_CONTAINER = {
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

    @property
    def container_name(self) -> str:
        return {
            "path": "path_parameters",
            "query": "query",
            "header": "headers",
            "cookie": "cookies",
            "body": "body",
        }[self]

    @property
    def is_in_header(self) -> bool:
        return self in HEADER_LOCATIONS


HEADER_LOCATIONS = frozenset([ParameterLocation.HEADER, ParameterLocation.COOKIE])

CONTAINER_TO_LOCATION = {
    "path_parameters": ParameterLocation.PATH,
    "query": ParameterLocation.QUERY,
    "headers": ParameterLocation.HEADER,
    "cookies": ParameterLocation.COOKIE,
    "body": ParameterLocation.BODY,
}
