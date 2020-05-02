# pylint: disable=too-many-instance-attributes
"""Schema objects provide a convenient interface to raw schemas.

Their responsibilities:
  - Provide a unified way to work with different types of schemas
  - Give all endpoints / methods combinations that are available directly from the schema;

They give only static definitions of endpoints.
"""
import itertools
from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional, Tuple, Union, overload
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen

import attr
import hypothesis
import jsonschema
import yaml
from requests.structures import CaseInsensitiveDict

from ._hypothesis import make_test_or_exception
from .constants import HookLocation
from .converter import to_json_schema, to_json_schema_recursive
from .exceptions import InvalidSchema
from .filters import should_skip_by_tag, should_skip_endpoint, should_skip_method
from .hooks import HookDispatcher, warn_deprecated_hook
from .models import Endpoint, empty_object
from .types import Filter, GenericTest, Hook, NotSet
from .utils import NOT_SET, GenericResponse, StringDatesYAMLLoader, deprecated

# Reference resolving will stop after this depth
RECURSION_DEPTH_LIMIT = 100


def load_file_impl(location: str, opener: Callable) -> Dict[str, Any]:
    """Load a schema from the given file."""
    with opener(location) as fd:
        return yaml.load(fd, StringDatesYAMLLoader)


@lru_cache()
def load_file(location: str) -> Dict[str, Any]:
    """Load a schema from the given file."""
    return load_file_impl(location, open)


@lru_cache()
def load_file_uri(location: str) -> Dict[str, Any]:
    """Load a schema from the given file uri."""
    return load_file_impl(location, urlopen)


@attr.s()  # pragma: no mutate
class BaseSchema(Mapping):
    raw_schema: Dict[str, Any] = attr.ib()  # pragma: no mutate
    location: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    base_url: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    method: Optional[Filter] = attr.ib(default=None)  # pragma: no mutate
    endpoint: Optional[Filter] = attr.ib(default=None)  # pragma: no mutate
    tag: Optional[Filter] = attr.ib(default=None)  # pragma: no mutate
    app: Any = attr.ib(default=None)  # pragma: no mutate
    hooks: HookDispatcher = attr.ib(factory=HookDispatcher)  # pragma: no mutate
    test_function: Optional[GenericTest] = attr.ib(default=None)  # pragma: no mutate
    validate_schema: bool = attr.ib(default=True)  # pragma: no mutate

    def __iter__(self) -> Iterator[str]:
        return iter(self.endpoints)

    def __getitem__(self, item: str) -> CaseInsensitiveDict:
        return self.endpoints[item]

    def __len__(self) -> int:
        return len(self.endpoints)

    @property  # pragma: no mutate
    def spec_version(self) -> str:
        raise NotImplementedError

    @property  # pragma: no mutate
    def verbose_name(self) -> str:
        raise NotImplementedError

    @property
    def endpoints(self) -> Dict[str, CaseInsensitiveDict]:
        if not hasattr(self, "_endpoints"):
            # pylint: disable=attribute-defined-outside-init
            endpoints = self.get_all_endpoints()
            self._endpoints = endpoints_to_dict(endpoints)
        return self._endpoints

    @property
    def resolver(self) -> jsonschema.RefResolver:
        if not hasattr(self, "_resolver"):
            # pylint: disable=attribute-defined-outside-init
            self._resolver = jsonschema.RefResolver(
                self.location or "", self.raw_schema, handlers={"file": load_file_uri, "": load_file}
            )
        return self._resolver

    @property
    def endpoints_count(self) -> int:
        return len(list(self.get_all_endpoints()))

    def get_all_endpoints(self) -> Generator[Endpoint, None, None]:
        raise NotImplementedError

    def get_all_tests(
        self, func: Callable, settings: Optional[hypothesis.settings] = None, seed: Optional[int] = None
    ) -> Generator[Tuple[Endpoint, Union[Callable, InvalidSchema]], None, None]:
        """Generate all endpoints and Hypothesis tests for them."""
        test: Union[Callable, InvalidSchema]
        for endpoint in self.get_all_endpoints():
            test = make_test_or_exception(endpoint, func, settings, seed)
            yield endpoint, test

    def parametrize(
        self,
        method: Optional[Filter] = NOT_SET,
        endpoint: Optional[Filter] = NOT_SET,
        tag: Optional[Filter] = NOT_SET,
        validate_schema: Union[bool, NotSet] = NOT_SET,
    ) -> Callable:
        """Mark a test function as a parametrized one."""

        def wrapper(func: GenericTest) -> GenericTest:
            HookDispatcher.add_dispatcher(func)
            func._schemathesis_test = self.clone(func, method, endpoint, tag, validate_schema)  # type: ignore
            return func

        return wrapper

    def clone(  # pylint: disable=too-many-arguments
        self,
        test_function: Optional[GenericTest] = None,
        method: Optional[Filter] = NOT_SET,
        endpoint: Optional[Filter] = NOT_SET,
        tag: Optional[Filter] = NOT_SET,
        validate_schema: Union[bool, NotSet] = NOT_SET,
    ) -> "BaseSchema":
        if method is NOT_SET:
            method = self.method
        if endpoint is NOT_SET:
            endpoint = self.endpoint
        if tag is NOT_SET:
            tag = self.tag
        if validate_schema is NOT_SET:
            validate_schema = self.validate_schema

        return self.__class__(
            self.raw_schema,
            location=self.location,
            base_url=self.base_url,
            method=method,
            endpoint=endpoint,
            tag=tag,
            app=self.app,
            hooks=self.hooks,
            test_function=test_function,
            validate_schema=validate_schema,  # type: ignore
        )

    def _get_response_schema(self, definition: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract response schema from `responses`."""
        raise NotImplementedError

    @deprecated("'register_hook` is deprecated, use `hooks.register' instead")
    def register_hook(self, place: str, hook: Hook) -> None:
        warn_deprecated_hook(hook)
        if place not in HookLocation.__members__:
            raise KeyError(place)
        self.hooks.register_hook_with_name(f"before_generate_{place}", hook, skip_validation=True)

    @deprecated("'with_hook` is deprecated, use `hooks.apply' instead")
    def with_hook(self, place: str, hook: Hook) -> Callable[[GenericTest], GenericTest]:
        """Register a hook for a specific test."""
        warn_deprecated_hook(hook)
        if place not in HookLocation.__members__:
            raise KeyError(place)

        return self.hooks.apply(f"before_generate_{place}", hook, skip_validation=True)

    def get_content_types(self, endpoint: Endpoint, response: GenericResponse) -> List[str]:
        """Content types available for this endpoint."""
        raise NotImplementedError


class SwaggerV20(BaseSchema):
    nullable_name = "x-nullable"
    example_field = "x-example"
    operations: Tuple[str, ...] = ("get", "put", "post", "delete", "options", "head", "patch")

    def __repr__(self) -> str:
        info = self.raw_schema["info"]
        return f"{self.__class__.__name__} for {info['title']} ({info['version']})"

    @property
    def spec_version(self) -> str:
        return self.raw_schema["swagger"]

    @property
    def verbose_name(self) -> str:
        return f"Swagger {self.spec_version}"

    @property
    def base_path(self) -> str:
        """Base path for the schema."""
        path: str = self.raw_schema.get("basePath", "/")  # pragma: no mutate
        if not path.endswith("/"):
            path += "/"
        return path

    def get_full_path(self, path: str) -> str:
        """Compute full path for the given path."""
        return urljoin(self.base_path, path.lstrip("/"))  # pragma: no mutate

    def get_all_endpoints(self) -> Generator[Endpoint, None, None]:
        try:
            paths = self.raw_schema["paths"]  # pylint: disable=unsubscriptable-object
            for path, methods in paths.items():
                full_path = self.get_full_path(path)
                if should_skip_endpoint(full_path, self.endpoint):
                    continue
                # Only one level is resolved for `raw_methods` so method names are available, but everything deeper
                # is not resolved.
                raw_methods = self.resolve(deepcopy(methods), RECURSION_DEPTH_LIMIT)
                methods = self.resolve(methods)
                common_parameters = get_common_parameters(methods)
                for method, resolved_definition in methods.items():
                    # Only method definitions are parsed
                    if (
                        method not in self.operations
                        or should_skip_method(method, self.method)
                        or should_skip_by_tag(resolved_definition.get("tags"), self.tag)
                    ):
                        continue
                    parameters = itertools.chain(resolved_definition.get("parameters", ()), common_parameters)
                    # To prevent recursion errors we need to pass not resolved schema as well
                    # It could be used for response validation
                    raw_definition = raw_methods[method]
                    yield self.make_endpoint(full_path, method, parameters, resolved_definition, raw_definition)
        except (KeyError, AttributeError, jsonschema.exceptions.RefResolutionError):
            raise InvalidSchema("Schema parsing failed. Please check your schema.")

    def make_endpoint(  # pylint: disable=too-many-arguments
        self,
        full_path: str,
        method: str,
        parameters: Iterator[Dict[str, Any]],
        resolved_definition: Dict[str, Any],
        raw_definition: Dict[str, Any],
    ) -> Endpoint:
        """Create JSON schemas for query, body, etc from Swagger parameters definitions."""
        base_url = self.base_url
        if base_url is not None:
            base_url = base_url.rstrip("/")  # pragma: no mutate
        endpoint = Endpoint(
            path=full_path,
            method=method.upper(),
            definition=raw_definition,
            base_url=base_url,
            app=self.app,
            schema=self,
        )
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
        endpoint.path_parameters = self.add_parameter(endpoint.path_parameters, parameter)

    def process_header(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        endpoint.headers = self.add_parameter(endpoint.headers, parameter)

    def process_query(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        endpoint.query = self.add_parameter(endpoint.query, parameter)

    def process_body(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        # "schema" is a required field
        endpoint.body = parameter["schema"]

    def process_form_data(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        endpoint.form_data = self.add_parameter(endpoint.form_data, parameter)

    def add_parameter(self, container: Optional[Dict[str, Any]], parameter: Dict[str, Any]) -> Dict[str, Any]:
        """Add parameter object to the container."""
        name = parameter["name"]
        container = container or empty_object()
        container["properties"][name] = self.parameter_to_json_schema(parameter)
        if parameter.get("required", False):
            container["required"].append(name)
        return self.add_examples(container, parameter)

    def add_examples(self, container: Dict[str, Any], parameter: Dict[str, Any]) -> Dict[str, Any]:
        if self.example_field in parameter:
            examples = container.setdefault("example", {})  # examples should be merged together
            examples[parameter["name"]] = parameter[self.example_field]
        return container

    def parameter_to_json_schema(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Parameter object to a JSON schema."""
        return {
            key: value
            for key, value in data.items()
            # Do not include keys not supported by JSON schema
            if not (key == "required" and not isinstance(value, list))
        }

    @overload  # pragma: no mutate
    def resolve(
        self, item: Dict[str, Any], recursion_level: int = 0
    ) -> Dict[str, Any]:  # pylint: disable=function-redefined
        pass

    @overload  # pragma: no mutate
    def resolve(self, item: List, recursion_level: int = 0) -> List:  # pylint: disable=function-redefined
        pass

    # pylint: disable=function-redefined
    def resolve(self, item: Union[Dict[str, Any], List], recursion_level: int = 0) -> Union[Dict[str, Any], List]:
        """Recursively resolve all references in the given object."""
        if recursion_level > RECURSION_DEPTH_LIMIT:
            return item
        if isinstance(item, dict):
            item = self.prepare(item)
            if "$ref" in item:
                with self.resolver.resolving(item["$ref"]) as resolved:
                    return self.resolve(resolved, recursion_level + 1)
            for key, sub_item in item.items():
                item[key] = self.resolve(sub_item, recursion_level)
        elif isinstance(item, list):
            for idx, sub_item in enumerate(item):
                item[idx] = self.resolve(sub_item, recursion_level)
        return item

    def prepare(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Parse schema extension, e.g. "x-nullable" field."""
        return to_json_schema(item, self.nullable_name)

    def _get_response_schema(self, definition: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        schema = definition.get("schema")
        if not schema:
            return None
        return to_json_schema_recursive(schema, self.nullable_name)

    def get_content_types(self, endpoint: Endpoint, response: GenericResponse) -> List[str]:
        produces = endpoint.definition.get("produces", None)
        if produces:
            return produces
        return self.raw_schema.get("produces", [])


class OpenApi30(SwaggerV20):  # pylint: disable=too-many-ancestors
    nullable_name = "nullable"
    example_field = "example"
    operations = SwaggerV20.operations + ("trace",)

    @property
    def spec_version(self) -> str:
        return self.raw_schema["openapi"]

    @property
    def verbose_name(self) -> str:
        return f"Open API {self.spec_version}"

    @property
    def base_path(self) -> str:
        """Base path for the schema."""
        servers = self.raw_schema.get("servers", [])
        if servers:
            # assume we're the first server in list
            server = servers[0]
            url = server["url"].format(**{k: v["default"] for k, v in server.get("variables", {}).items()})
            path = urlsplit(url).path
        else:
            path = "/"
        if not path.endswith("/"):
            path += "/"
        return path

    def make_endpoint(  # pylint: disable=too-many-arguments
        self,
        full_path: str,
        method: str,
        parameters: Iterator[Dict[str, Any]],
        resolved_definition: Dict[str, Any],
        raw_definition: Dict[str, Any],
    ) -> Endpoint:
        """Create JSON schemas for query, body, etc from Swagger parameters definitions."""
        endpoint = super().make_endpoint(full_path, method, parameters, resolved_definition, raw_definition)
        if "requestBody" in resolved_definition:
            self.process_body(endpoint, resolved_definition["requestBody"])
        return endpoint

    def process_by_type(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        if parameter["in"] == "cookie":
            self.process_cookie(endpoint, parameter)
        else:
            super().process_by_type(endpoint, parameter)

    def add_examples(self, container: Dict[str, Any], parameter: Dict[str, Any]) -> Dict[str, Any]:
        if self.example_field in parameter["schema"]:
            examples = container.setdefault("example", {})  # examples should be merged together
            examples[parameter["name"]] = parameter["schema"][self.example_field]
        # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.2.md#parameter-object
        # > Furthermore, if referencing a schema which contains an example,
        # > the example value SHALL override the example provided by the schema
        return super().add_examples(container, parameter)

    def process_cookie(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        endpoint.cookies = self.add_parameter(endpoint.cookies, parameter)

    def process_body(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        # Take the first media type object
        options = iter(parameter["content"].values())
        parameter = next(options)
        # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.2.md#media-type-object
        # > Furthermore, if referencing a schema which contains an example,
        # > the example value SHALL override the example provided by the schema
        if "example" in parameter:
            parameter["schema"]["example"] = parameter["example"]
        super().process_body(endpoint, parameter)

    def parameter_to_json_schema(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # "schema" field is required for all parameters in Open API 3.0
        return super().parameter_to_json_schema(data["schema"])

    def _get_response_schema(self, definition: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        options = iter(definition.get("content", {}).values())
        option = next(options, None)
        if option:
            return to_json_schema_recursive(option["schema"], self.nullable_name)
        return None

    def get_content_types(self, endpoint: Endpoint, response: GenericResponse) -> List[str]:
        try:
            responses = endpoint.definition["responses"]
        except KeyError:
            # Possible to get if `validate_schema=False` is passed during schema creation
            raise InvalidSchema("Schema parsing failed. Please check your schema.")
        definitions = responses.get(str(response.status_code), {}).get("content", {})
        return list(definitions.keys())


def get_common_parameters(methods: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Common parameters are deep copied from the methods definitions.

    Copying is needed because of further modifications.
    """
    common_parameters = methods.get("parameters")
    if common_parameters is not None:
        return deepcopy(common_parameters)
    return []


def endpoints_to_dict(endpoints: Generator[Endpoint, None, None]) -> Dict[str, CaseInsensitiveDict]:
    output: Dict[str, CaseInsensitiveDict] = {}
    for endpoint in endpoints:
        output.setdefault(endpoint.path, CaseInsensitiveDict())
        output[endpoint.path][endpoint.method] = endpoint
    return output
