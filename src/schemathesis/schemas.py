"""Schema objects provide a convenient interface to raw schemas.

Their responsibilities:
  - Provide a unified way to work with different types of schemas
  - Give all endpoints / methods combinations that are available directly from the schema;

They give only static definitions of endpoints.
"""
import itertools
from copy import deepcopy
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional, Tuple, Union, overload
from urllib.parse import urljoin

import attr
import hypothesis
import jsonschema

from ._hypothesis import create_test
from .filters import should_skip_endpoint, should_skip_method
from .models import Endpoint
from .types import Filter


@attr.s(slots=True)
class BaseSchema:
    raw_schema: Dict[str, Any] = attr.ib()
    method: Optional[Filter] = attr.ib(default=None)
    endpoint: Optional[Filter] = attr.ib(default=None)

    @property
    def resolver(self) -> jsonschema.RefResolver:
        if not hasattr(self, "_resolver"):
            # pylint: disable=attribute-defined-outside-init
            self._resolver = jsonschema.RefResolver("", self.raw_schema)
        return self._resolver

    def get_all_endpoints(self) -> Generator[Endpoint, None, None]:
        raise NotImplementedError

    def get_all_tests(
        self, func: Callable, settings: Optional[hypothesis.settings] = None
    ) -> Generator[Tuple[Endpoint, Callable], None, None]:
        """Generate all endpoints and Hypothesis tests for them."""
        for endpoint in self.get_all_endpoints():
            yield endpoint, create_test(endpoint, func, settings)

    def parametrize(self, method: Optional[Filter] = None, endpoint: Optional[Filter] = None) -> Callable:
        """Mark a test function as a parametrized one."""

        def wrapper(func: Callable) -> Callable:
            func._schemathesis_test = self.__class__(self.raw_schema, method=method, endpoint=endpoint)  # type: ignore
            return func

        return wrapper


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

    def get_all_endpoints(self) -> Generator[Endpoint, None, None]:
        paths = self.raw_schema["paths"]  # pylint: disable=unsubscriptable-object
        for path, methods in paths.items():
            full_path = self.get_full_path(path)
            if should_skip_endpoint(full_path, self.endpoint):
                continue
            common_parameters = get_common_parameters(methods)
            for method, definition in methods.items():
                if method == "parameters" or should_skip_method(method, self.method):
                    continue
                parameters = itertools.chain(definition.get("parameters", ()), common_parameters)
                yield self.make_endpoint(full_path, method, parameters, definition)

    def make_endpoint(
        self, full_path: str, method: str, parameters: Iterator[Dict[str, Any]], definition: Dict[str, Any]
    ) -> Endpoint:
        """Create JSON schemas for query, body, etc from Swagger parameters definitions."""
        endpoint = Endpoint(path=full_path, method=method.upper())
        for parameter in parameters:
            self.process_parameter(endpoint, parameter)
        return endpoint

    def process_parameter(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        """Convert each Parameter object to a JSON schema."""
        parameter = deepcopy(parameter)
        parameter = self.resolve(parameter)
        self.process_by_type(endpoint, parameter)

    def process_by_type(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        if parameter["in"] == "path":
            self.process_path(endpoint, parameter)
        elif parameter["in"] == "query":
            self.process_query(endpoint, parameter)
        elif parameter["in"] == "header":
            self.process_header(endpoint, parameter)
        elif parameter["in"] == "body":
            # Could be only one parameter with "in=body"
            self.process_body(endpoint, parameter)
        elif parameter["in"] == "formData":
            self.process_form_data(endpoint, parameter)

    def process_path(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        self.add_parameter(endpoint.path_parameters, parameter)

    def process_header(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        self.add_parameter(endpoint.headers, parameter)

    def process_query(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        self.add_parameter(endpoint.query, parameter)

    def process_body(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        # "schema" is a required field
        endpoint.body = self.resolve(parameter["schema"])

    def process_form_data(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        self.add_parameter(endpoint.form_data, parameter)

    def add_parameter(self, container: Dict[str, Any], parameter: Dict[str, Any]) -> None:
        """Add parameter object to the container."""
        name = parameter["name"]
        container["properties"][name] = self.parameter_to_json_schema(parameter)
        if parameter.get("required", False):
            container["required"].append(name)

    def parameter_to_json_schema(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Parameter object to a JSON schema."""
        return {
            key: value
            for key, value in data.items()
            # Do not include keys not supported by JSON schema
            if not (key == "required" and not isinstance(value, list))
        }

    @overload
    def resolve(self, item: Dict[str, Any]) -> Dict[str, Any]:  # pylint: disable=function-redefined
        pass

    @overload
    def resolve(self, item: List) -> List:  # pylint: disable=function-redefined
        pass

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
    def make_endpoint(
        self, full_path: str, method: str, parameters: Iterator[Dict[str, Any]], definition: Dict[str, Any]
    ) -> Endpoint:
        """Create JSON schemas for query, body, etc from Swagger parameters definitions."""
        endpoint = super().make_endpoint(full_path, method, parameters, definition)
        if "requestBody" in definition:
            self.process_body(endpoint, definition["requestBody"])
        return endpoint

    def process_by_type(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        if parameter["in"] == "cookie":
            self.process_cookie(endpoint, parameter)
        else:
            super().process_by_type(endpoint, parameter)

    def process_cookie(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        self.add_parameter(endpoint.cookies, parameter)

    def process_body(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        parameter = self.resolve(parameter)
        # Take the first media type object
        options = iter(parameter["content"].values())
        parameter = next(options)
        super().process_body(endpoint, parameter)

    def parameter_to_json_schema(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # "schema" field is required for all parameters in Open API 3.0
        return super().parameter_to_json_schema(data["schema"])


def get_common_parameters(methods: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Common parameters are deep copied from the methods definitions.

    Copying is needed because of further modifications.
    """
    common_parameters = methods.get("parameters")
    if common_parameters is not None:
        return deepcopy(common_parameters)
    return []
