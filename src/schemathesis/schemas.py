"""Schema objects provide a convenient interface to raw schemas.

Their responsibilities:
  - Provide a unified way to work with different types of schemas
  - Give all endpoints / methods combinations that are available directly from the schema;

They give only static definitions of endpoints.
"""
import itertools
import re
from copy import deepcopy
from typing import Any, Dict, Generator, Iterator, List, Optional, Set, Tuple, Union, overload
from urllib.parse import urljoin

import attr
import jsonschema

from .types import Body, Filter, Headers, PathParameters, Query


@attr.s(slots=True)
class Endpoint:
    """A container that could be used for test cases generation."""

    path: str = attr.ib()
    method: str = attr.ib()
    path_parameters: PathParameters = attr.ib()
    headers: Headers = attr.ib()
    query: Query = attr.ib()
    body: Body = attr.ib()


def empty_object() -> Dict[str, Any]:
    return {"properties": {}, "additionalProperties": False, "type": "object", "required": []}


@attr.s(slots=True)
class PreparedParameters:
    path_parameters: PathParameters = attr.ib(init=False, factory=empty_object)
    headers: Headers = attr.ib(init=False, factory=empty_object)
    query: Query = attr.ib(init=False, factory=empty_object)
    body: Body = attr.ib(init=False, factory=empty_object)


@attr.s(slots=True)
class BaseSchema:
    raw_schema: Dict[str, Any] = attr.ib()

    @property
    def resolver(self) -> jsonschema.RefResolver:
        if not hasattr(self, "_resolver"):
            # pylint: disable=attribute-defined-outside-init
            self._resolver = jsonschema.RefResolver("", self.raw_schema)
        return self._resolver

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
                parameters = itertools.chain(definition.get("parameters", ()), common_parameters)
                prepared_parameters = self.get_parameters(parameters, definition)
                yield Endpoint(
                    path=full_path,
                    method=method.upper(),
                    path_parameters=prepared_parameters.path_parameters,
                    headers=prepared_parameters.headers,
                    query=prepared_parameters.query,
                    body=prepared_parameters.body,
                )

    def get_parameters(self, parameters: Iterator[Dict[str, Any]], definition: Dict[str, Any]) -> PreparedParameters:
        """Create JSON schemas for query, body, etc from Swagger parameters definitions."""
        result = PreparedParameters()
        for parameter in parameters:
            self.process_parameter(result, parameter)
        return result

    def process_parameter(self, result: PreparedParameters, parameter: Dict[str, Any]) -> None:
        """Convert each Parameter object to a JSON schema."""
        parameter = deepcopy(parameter)
        parameter = self.resolve(parameter)
        if parameter["in"] == "path":
            self.process_path(result, parameter)
        elif parameter["in"] == "query":
            self.process_query(result, parameter)
        elif parameter["in"] == "header":
            self.process_header(result, parameter)
        elif parameter["in"] == "body":
            # Could be only one parameter with "in=body"
            self.process_body(result, parameter)

    def process_path(self, result: PreparedParameters, parameter: Dict[str, Any]) -> None:
        self.add_parameter(result.path_parameters, parameter)

    def process_header(self, result: PreparedParameters, parameter: Dict[str, Any]) -> None:
        self.add_parameter(result.headers, parameter)

    def process_query(self, result: PreparedParameters, parameter: Dict[str, Any]) -> None:
        self.add_parameter(result.query, parameter)

    def process_body(self, result: PreparedParameters, parameter: Dict[str, Any]) -> None:
        # "schema" is a required field
        result.body = self.resolve(parameter["schema"])

    def add_parameter(self, container: Dict[str, Any], parameter: Dict[str, Any]) -> None:
        """Add parameter object to a container."""
        name = parameter["name"]
        container["properties"][name] = self.parameter_to_json_schema(parameter)
        if parameter.get("required", False):
            container["required"].append(name)

    def parameter_to_json_schema(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Parameter object to JSON schema"""
        return {
            key: value
            for key, value in data.items()
            # Do not include keys not supported by JSON schema
            if not (key == "required" and not isinstance(value, list))
        }

    @overload
    def resolve(self, item: Dict[str, Any]) -> Dict[str, Any]:  # pylint: disable=function-redefined
        ...

    @overload
    def resolve(self, item: List) -> List:  # pylint: disable=function-redefined
        ...

    # pylint: disable=function-redefined
    def resolve(self, item: Union[Dict[str, Any], List]) -> Union[Dict[str, Any], List]:
        """Recursively resolve all references in the given object."""
        if isinstance(item, dict):
            if "$ref" in item:
                with self.resolver.resolving(item["$ref"]) as resolved:
                    return self.resolve(resolved)
            for key, sub_item in item.items():
                item[key] = self.resolve(sub_item)
        elif isinstance(item, list):
            for idx, sub_item in enumerate(item):
                item[idx] = self.resolve(sub_item)
        return item


class OpenApi30(SwaggerV20):
    def get_parameters(self, parameters: Iterator[Dict[str, Any]], definition: Dict[str, Any]) -> PreparedParameters:
        """Create JSON schemas for query, body, etc from Swagger parameters definitions."""
        result = super().get_parameters(parameters, definition)
        if "requestBody" in definition:
            self.process_body(result, definition["requestBody"])
        return result

    def process_body(self, result: PreparedParameters, parameter: Dict[str, Any]) -> None:
        parameter = self.resolve(parameter)
        # Take the first media type object
        options = iter(parameter["content"].values())
        parameter = next(options)
        super().process_body(result, parameter)

    def parameter_to_json_schema(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # "schema" field is required for all parameters in Open API 3.0
        return super().parameter_to_json_schema(data["schema"])


def wrap_schema(raw_schema: Dict[str, Any]) -> BaseSchema:
    """Get a proper abstraction for the given raw schema."""
    if "swagger" in raw_schema:
        return SwaggerV20(raw_schema)
    if "openapi" in raw_schema:
        return OpenApi30(raw_schema)
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
