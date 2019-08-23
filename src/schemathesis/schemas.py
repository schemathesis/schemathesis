"""Schema objects provide a convenient interface to raw schemas.

Their responsibilities:
  - Provide a unified way to work with different types of schemas
  - Give all endpoints / methods combinations that are available directly from the schema;

They give only static definitions of endpoints.
"""
import itertools
from copy import deepcopy
from fnmatch import fnmatch
from functools import lru_cache
from typing import Any, Dict, Generator, Iterator, List, Optional, Set, Tuple, Union
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
        # pylint: disable=unsubscriptable-object
        path: str = self.raw_schema["basePath"]
        if not path.endswith("/"):
            path += "/"
        return path

    def get_full_path(self, path: str) -> str:
        # TODO check leading / trailing slashes
        return urljoin(self.base_path, path.lstrip("/"))

    def get_all_endpoints(
        self, filter_method: Optional[Filter] = None, filter_endpoint: Optional[Filter] = None
    ) -> Generator[Endpoint, None, None]:
        schema = deepcopy(self.raw_schema)  # modifications are going to happen
        paths = schema["paths"]  # pylint: disable=unsubscriptable-object
        for path, methods in paths.items():
            full_path = self.get_full_path(path)
            if should_skip_endpoint(full_path, filter_endpoint):
                continue
            common_parameters = methods.pop("parameters", [])
            for method, definition in methods.items():
                if should_skip_method(method, filter_method):
                    continue
                # Maybe decompose definition into smaller parts? something is not needed probably
                parameters = itertools.chain(definition.get("parameters", []), common_parameters)
                # a parameter could be either Parameter Object or Reference Object.
                # references should be resolved here to know where to put the parameter - body / query / etc
                prepared_parameters = [self.prepare_item(item) for item in parameters]
                yield Endpoint(path=full_path, method=method.upper(), parameters=prepared_parameters)

    def prepare_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        if is_reference(item):
            item = self.resolve_reference(item["$ref"])
        elif item["in"] == "body" and is_reference(item["schema"]):
            item.update(self.resolve_reference(item["schema"]["$ref"]))
            del item["schema"]
        return item

    @lru_cache()
    def resolve_reference(self, reference: str) -> Dict[str, Any]:
        dereferenced = self.dereference(reference)
        for key, value in traverse_schema(dereferenced):
            if key[-1] == "$ref":
                data = self.resolve_reference(value)
                current = dereferenced
                for k in key[:-2]:
                    current = current[k]
                current[key[-2]] = data
        return dereferenced

    @lru_cache()
    def dereference(self, path: str) -> Dict[str, Any]:
        # assume URI fragment
        current: Dict[str, Any] = self.raw_schema
        for part in path[2:].split("/"):
            # Reference not found?
            # Support arrays in JSON pointers?
            current = current[part]  # pylint: disable=unsubscriptable-object
        return current


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


def traverse_schema(schema: Dict[str, Any]) -> Iterator[Tuple[List[str], Any]]:
    """Iterate over dict levels with producing [k_1, k_2, ...], value where the first list is a path to the value."""
    for key, value in schema.items():
        if isinstance(value, dict) and value:
            for sub_key, sub_value in traverse_schema(value):
                yield [key] + sub_key, sub_value
        else:
            yield [key], value
