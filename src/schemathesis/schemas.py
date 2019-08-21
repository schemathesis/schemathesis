"""Schema objects provide a convenient interface to raw schemas.

Their responsibilities:
  - Provide a unified way to work with different types of schemas
  - Give all endpoints / methods combinations that are available directly from the schema;

They give only static definitions of endpoints.
"""
from fnmatch import fnmatch
from functools import lru_cache
from typing import Any, Dict, Generator, List, Optional, Set, Tuple, Union
from urllib.parse import urljoin

import attr

from .types import Filter, ParametersList


@attr.s(slots=True)
class Endpoint:
    """A container that could be used for test cases generation."""

    path: str = attr.ib()
    method: str = attr.ib()
    parameters: ParametersList = attr.ib()

    @property
    def path_parameters(self) -> ParametersList:
        return filter_parameters(self.parameters, "path")

    @property
    def query(self) -> ParametersList:
        return filter_parameters(self.parameters, "query")

    @property
    def body(self) -> ParametersList:
        return filter_parameters(self.parameters, "body")


@attr.s(hash=False)
class BaseSchema:
    raw_schema: Dict[str, Any] = attr.ib()

    def get_all_endpoints(
        self, filter_method: Optional[Filter] = None, filter_endpoint: Optional[Filter] = None
    ) -> Generator[Endpoint, None, None]:
        raise NotImplementedError


class SwaggerV20(BaseSchema):
    @property
    def base_path(self) -> str:
        """Base path for the schema."""
        # pylint: disable=unsubscriptable-object
        path: str = self.raw_schema["basePath"]
        if not path.endswith("/"):
            path += "/"
        return path

    def get_full_path(self, path: str) -> str:
        """Compute full path for the given path."""
        return urljoin(self.base_path, path.lstrip("/"))

    def get_all_endpoints(
        self, filter_method: Optional[Filter] = None, filter_endpoint: Optional[Filter] = None
    ) -> Generator[Endpoint, None, None]:
        paths = self.raw_schema["paths"]  # pylint: disable=unsubscriptable-object
        for path, methods in paths.items():
            full_path = self.get_full_path(path)
            if should_skip_endpoint(full_path, filter_endpoint):
                continue
            for method, definition in methods.items():
                if should_skip_method(method, filter_method):
                    continue
                # Maybe decompose definition into smaller parts? something is not needed probably
                parameters = definition.get("parameters", [])
                parameters = [self.prepare_item(item) for item in parameters]
                yield Endpoint(path=full_path, method=method.upper(), parameters=parameters)

    def prepare_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        if is_reference(item):
            item = self.dereference(item["$ref"])
        return item

    @lru_cache()
    def dereference(self, path: str) -> Dict[str, Any]:
        # assume URI fragment
        current: Dict[str, Any] = self.raw_schema
        for part in path[2:].split("/"):
            # Reference not found?
            # Support arrays in JSON pointers?
            current = current[part]  # pylint: disable=unsubscriptable-object
        return current


def wrap_schema(raw_schema: Dict[str, Any]) -> BaseSchema:
    """Get a proper abstraction for the given raw schema."""
    if "swagger" in raw_schema:
        return SwaggerV20(raw_schema)
    raise ValueError("Unsupported schema type")


def force_tuple(item: Filter) -> Union[List, Set, Tuple]:
    if not isinstance(item, (list, set, tuple)):
        return (item,)
    return item


def should_skip_method(method: str, pattern: Optional[Filter]) -> bool:
    if pattern is None:
        return False
    patterns = force_tuple(pattern)
    return method.upper() not in map(str.upper, patterns)


def should_skip_endpoint(endpoint: str, pattern: Optional[Filter]) -> bool:
    if pattern is None:
        return False
    patterns = force_tuple(pattern)
    return not any(is_match(endpoint, item) for item in patterns)


def is_match(endpoint: str, pattern: str) -> bool:
    return pattern in endpoint or fnmatch(endpoint, pattern)


def is_reference(item: Dict[str, Any]) -> bool:
    return "$ref" in item


def filter_parameters(parameters: ParametersList, place: str) -> ParametersList:
    return [parameter for parameter in parameters if parameter["in"] == place]
