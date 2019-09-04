"""Schema objects provide a convenient interface to raw schemas.

Their responsibilities:
  - Provide a unified way to work with different types of schemas
  - Give all endpoints / methods combinations that are available directly from the schema;

They give only static definitions of endpoints.
"""
import itertools
import re
from copy import deepcopy
from functools import lru_cache
from typing import Any, Dict, Generator, Iterator, List, Optional, Set, Tuple, Union
from urllib.parse import urljoin

import attr

from .types import Filter


@attr.s(slots=True)
class Endpoint:
    """A container that could be used for test cases generation."""

    path: str = attr.ib()
    method: str = attr.ib()
    path_parameters: Dict[str, Any] = attr.ib()
    query: Dict[str, Any] = attr.ib()
    body: Dict[str, Any] = attr.ib()


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
        path: str = self.raw_schema.get("basePath", "/")
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
            common_parameters = get_common_parameters(methods)
            for method, definition in methods.items():
                if method == "parameters" or should_skip_method(method, filter_method):
                    continue
                path, query, body = self.get_parameters(common_parameters, definition)
                yield Endpoint(path=full_path, method=method.upper(), path_parameters=path, query=query, body=body)

    def get_parameters(
        self, common_parameters: List[Dict[str, Any]], definition: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """Create JSON schemas for query, body, etc from Swagger parameters definitions."""
        parameters = itertools.chain(definition.get("parameters", ()), common_parameters)
        path = empty_object()
        # Should these parts be always objects?
        # E.g. body could be empty since "required" could be false for the whole body
        # Generated object: {} - empty body or JSON "{}" ?
        query = empty_object()
        body = empty_object()

        for parameter in parameters:
            parameter = self.prepare_item(parameter)
            if parameter["in"] == "path":
                add_parameter(path, parameter)
            elif parameter["in"] == "query":
                add_parameter(query, parameter)
            elif parameter["in"] == "body":
                # Could be only one parameter with "in=body"
                body = self.prepare_body(parameter)
        return path, query, body

    def prepare_body(self, parameter: Dict[str, Any]) -> Dict[str, Any]:
        """Body is different - we don't need an extra nesting level in the output result.

        E.g. the output will contain only properties from the target object, not {parameter_name: <properties>}
        """
        return convert_property(parameter["schema"])

    def prepare_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        new_item = deepcopy(item)
        if is_reference(new_item):
            new_item = self.resolve_reference(new_item["$ref"])
        elif new_item["in"] == "body" and is_reference(new_item["schema"]):
            new_item["schema"] = self.resolve_reference(new_item["schema"]["$ref"])
        return new_item

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


def empty_object() -> Dict[str, Any]:
    return {"properties": {}, "additionalProperties": False, "type": "object", "required": []}


def add_parameter(container: Dict[str, Any], parameter: Dict[str, Any]) -> None:
    name = parameter["name"]
    container["properties"][name] = convert_property(parameter)
    if parameter.get("required", False):
        container["required"].append(name)


def wrap_schema(raw_schema: Dict[str, Any]) -> BaseSchema:
    """Get a proper abstraction for the given raw schema."""
    if "swagger" in raw_schema:
        return SwaggerV20(raw_schema)
    raise ValueError("Unsupported schema type")


def get_common_parameters(methods: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Common parameters are deep copied from the methods definitions.

    Copying is needed because of further modifications.
    """
    common_parameters = methods.get("parameters")
    if common_parameters is not None:
        return deepcopy(common_parameters)
    return []


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
    return pattern in endpoint or bool(re.search(pattern, endpoint))


def is_reference(item: Dict[str, Any]) -> bool:
    return "$ref" in item


def convert_property(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in data.items()
        # Do not include keys not supported by JSON schema
        if key not in ("name", "in") and (key != "required" or isinstance(value, list))
    }


def traverse_schema(schema: Dict[str, Any]) -> Iterator[Tuple[List[str], Any]]:
    """Iterate over dict levels with producing [k_1, k_2, ...], value where the first list is a path to the value."""
    for key, value in schema.items():
        if isinstance(value, dict) and value:
            for sub_key, sub_value in traverse_schema(value):
                yield [key] + sub_key, sub_value
        else:
            yield [key], value
