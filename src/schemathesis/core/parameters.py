from enum import Enum

LOCATION_TO_CONTAINER = {
    "path": "path_parameters",
    "query": "query",
    "header": "headers",
    "cookie": "cookies",
    "body": "body",
}


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
