# pylint: disable=too-many-ancestors
import itertools
from copy import deepcopy
from typing import Any, Dict, Generator, Iterator, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import jsonschema

from ...exceptions import InvalidSchema
from ...filters import should_skip_by_operation_id, should_skip_by_tag, should_skip_endpoint, should_skip_method
from ...hooks import HookContext
from ...models import Endpoint, EndpointDefinition, empty_object
from ...schemas import BaseSchema
from ...utils import GenericResponse
from .converter import to_json_schema_recursive
from .references import ConvertingResolver
from .security import OpenAPISecurityProcessor, SwaggerSecurityProcessor


class BaseOpenAPISchema(BaseSchema):
    nullable_name: str

    @property  # pragma: no mutate
    def spec_version(self) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        info = self.raw_schema["info"]
        return f"{self.__class__.__name__} for {info['title']} ({info['version']})"

    @property
    def resolver(self) -> ConvertingResolver:
        if not hasattr(self, "_resolver"):
            # pylint: disable=attribute-defined-outside-init
            self._resolver = ConvertingResolver(self.location or "", self.raw_schema, nullable_name=self.nullable_name)
        return self._resolver

    def get_content_types(self, endpoint: Endpoint, response: GenericResponse) -> List[str]:
        """Content types available for this endpoint."""
        raise NotImplementedError

    def get_response_schema(self, definition: Dict[str, Any], scope: str) -> Tuple[List[str], Optional[Dict[str, Any]]]:
        """Extract response schema from `responses`."""
        raise NotImplementedError


class SwaggerV20(BaseOpenAPISchema):
    nullable_name = "x-nullable"
    example_field = "x-example"
    operations: Tuple[str, ...] = ("get", "put", "post", "delete", "options", "head", "patch")
    security = SwaggerSecurityProcessor()

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
            context = HookContext()
            for path, methods in paths.items():
                full_path = self.get_full_path(path)
                if should_skip_endpoint(full_path, self.endpoint):
                    continue
                self.dispatch_hook("before_process_path", context, path, methods)
                # We need to know a proper scope in what methods are.
                # It will allow us to provide a proper reference resolving in `response_schema_conformance` and avoid
                # recursion errors
                if "$ref" in methods:
                    scope, raw_methods = deepcopy(self.resolver.resolve(methods["$ref"]))
                else:
                    raw_methods, scope = deepcopy(methods), self.resolver.resolution_scope
                methods = self.resolver.resolve_all(methods)
                common_parameters = get_common_parameters(methods)
                for method, resolved_definition in methods.items():
                    # Only method definitions are parsed
                    if (
                        method not in self.operations
                        or should_skip_method(method, self.method)
                        or should_skip_by_tag(resolved_definition.get("tags"), self.tag)
                        or should_skip_by_operation_id(resolved_definition.get("operationId"), self.operation_id)
                    ):
                        continue
                    parameters = itertools.chain(resolved_definition.get("parameters", ()), common_parameters)
                    # To prevent recursion errors we need to pass not resolved schema as well
                    # It could be used for response validation
                    raw_definition = EndpointDefinition(raw_methods[method], scope)
                    yield self.make_endpoint(full_path, method, parameters, resolved_definition, raw_definition)
        except (KeyError, AttributeError, jsonschema.exceptions.RefResolutionError):
            raise InvalidSchema("Schema parsing failed. Please check your schema.")

    def make_endpoint(  # pylint: disable=too-many-arguments
        self,
        full_path: str,
        method: str,
        parameters: Iterator[Dict[str, Any]],
        resolved_definition: Dict[str, Any],
        raw_definition: EndpointDefinition,
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
        self.security.process_definitions(self.raw_schema, endpoint, self.resolver)
        return endpoint

    def process_parameter(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        """Convert each Parameter object to a JSON schema."""
        parameter = deepcopy(parameter)
        parameter = self.resolver.resolve_all(parameter)
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

    def get_response_schema(self, definition: Dict[str, Any], scope: str) -> Tuple[List[str], Optional[Dict[str, Any]]]:
        scopes, definition = self.resolver.resolve_in_scope(deepcopy(definition), scope)
        schema = definition.get("schema")
        if not schema:
            return scopes, None
        # Extra conversion to JSON Schema is needed here if there was one $ref in the input
        # because it is not converted
        return scopes, to_json_schema_recursive(schema, self.nullable_name)

    def get_content_types(self, endpoint: Endpoint, response: GenericResponse) -> List[str]:
        produces = endpoint.definition.raw.get("produces", None)
        if produces:
            return produces
        return self.raw_schema.get("produces", [])


class OpenApi30(SwaggerV20):  # pylint: disable=too-many-ancestors
    nullable_name = "nullable"
    example_field = "example"
    operations = SwaggerV20.operations + ("trace",)
    security = OpenAPISecurityProcessor()

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
        raw_definition: EndpointDefinition,
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

    def get_response_schema(self, definition: Dict[str, Any], scope: str) -> Tuple[List[str], Optional[Dict[str, Any]]]:
        scopes, definition = self.resolver.resolve_in_scope(deepcopy(definition), scope)
        options = iter(definition.get("content", {}).values())
        option = next(options, None)
        if option:
            # Extra conversion to JSON Schema is needed here if there was one $ref in the input
            # because it is not converted
            return scopes, to_json_schema_recursive(option["schema"], self.nullable_name)
        return scopes, None

    def get_content_types(self, endpoint: Endpoint, response: GenericResponse) -> List[str]:
        try:
            responses = endpoint.definition.raw["responses"]
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
