# pylint: disable=too-many-ancestors
import itertools
from copy import deepcopy
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

import jsonschema
from hypothesis.strategies import SearchStrategy
from requests.structures import CaseInsensitiveDict

from ...exceptions import InvalidSchema
from ...hooks import HookContext
from ...models import Case, Endpoint, EndpointDefinition, empty_object
from ...schemas import BaseSchema
from ...stateful import StatefulTest
from ...types import FormData
from ...utils import GenericResponse
from . import links, serialization
from .converter import to_json_schema_recursive
from .examples import get_strategies_from_examples
from .filters import should_skip_by_operation_id, should_skip_by_tag, should_skip_endpoint, should_skip_method
from .references import ConvertingResolver
from .security import BaseSecurityProcessor, OpenAPISecurityProcessor, SwaggerSecurityProcessor


class BaseOpenAPISchema(BaseSchema):
    nullable_name: str
    links_field: str
    operations: Tuple[str, ...]
    security: BaseSecurityProcessor
    _endpoints_by_operation_id: Dict[str, Endpoint]

    @property  # pragma: no mutate
    def spec_version(self) -> str:
        raise NotImplementedError

    def get_stateful_tests(
        self, response: GenericResponse, endpoint: Endpoint, stateful: Optional[str]
    ) -> Sequence[StatefulTest]:
        if stateful == "links":
            return links.get_links(response, endpoint, field=self.links_field)
        return []

    def __repr__(self) -> str:
        info = self.raw_schema["info"]
        return f"{self.__class__.__name__} for {info['title']} ({info['version']})"

    @property
    def endpoints(self) -> Dict[str, CaseInsensitiveDict]:
        if not hasattr(self, "_endpoints"):
            # pylint: disable=attribute-defined-outside-init
            endpoints = self.get_all_endpoints()
            self._endpoints = endpoints_to_dict(endpoints)
        return self._endpoints

    def get_all_endpoints(self) -> Generator[Endpoint, None, None]:
        try:
            paths = self.raw_schema["paths"]  # pylint: disable=unsubscriptable-object
            context = HookContext()
            for path, methods in paths.items():
                full_path = self.get_full_path(path)
                if should_skip_endpoint(full_path, self.endpoint):
                    continue
                self.dispatch_hook("before_process_path", context, path, methods)
                scope, raw_methods = self._resolve_methods(methods)
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
                    raw_definition = EndpointDefinition(raw_methods[method], resolved_definition, scope)
                    yield self.make_endpoint(path, method, parameters, resolved_definition, raw_definition)
        except (KeyError, AttributeError, jsonschema.exceptions.RefResolutionError):
            raise InvalidSchema("Schema parsing failed. Please check your schema.")

    def _resolve_methods(self, methods: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        # We need to know a proper scope in what methods are.
        # It will allow us to provide a proper reference resolving in `response_schema_conformance` and avoid
        # recursion errors
        if "$ref" in methods:
            return deepcopy(self.resolver.resolve(methods["$ref"]))
        return self.resolver.resolution_scope, deepcopy(methods)

    def make_endpoint(  # pylint: disable=too-many-arguments
        self,
        path: str,
        method: str,
        parameters: Iterator[Dict[str, Any]],
        resolved_definition: Dict[str, Any],
        raw_definition: EndpointDefinition,
    ) -> Endpoint:
        """Create JSON schemas for query, body, etc from Swagger parameters definitions."""
        base_url = self.get_base_url()
        endpoint = Endpoint(
            path=path, method=method.upper(), definition=raw_definition, base_url=base_url, app=self.app, schema=self,
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
        raise NotImplementedError

    @property
    def resolver(self) -> ConvertingResolver:
        if not hasattr(self, "_resolver"):
            # pylint: disable=attribute-defined-outside-init
            self._resolver = ConvertingResolver(self.location or "", self.raw_schema, nullable_name=self.nullable_name)
        return self._resolver

    def get_content_types(self, endpoint: Endpoint, response: GenericResponse) -> List[str]:
        """Content types available for this endpoint."""
        raise NotImplementedError

    def get_strategies_from_examples(self, endpoint: Endpoint) -> List[SearchStrategy[Case]]:
        """Get examples from endpoint."""
        raise NotImplementedError

    def get_response_schema(self, definition: Dict[str, Any], scope: str) -> Tuple[List[str], Optional[Dict[str, Any]]]:
        """Extract response schema from `responses`."""
        raise NotImplementedError

    def get_endpoint_by_operation_id(self, operation_id: str) -> Endpoint:
        """Get an `Endpoint` instance by its `operationId`."""
        if not hasattr(self, "_endpoints_by_operation_id"):
            self._endpoints_by_operation_id = dict(self._group_endpoints_by_operation_id())
        return self._endpoints_by_operation_id[operation_id]

    def _group_endpoints_by_operation_id(self) -> Generator[Tuple[str, Endpoint], None, None]:
        for path, methods in self.raw_schema["paths"].items():
            scope, raw_methods = self._resolve_methods(methods)
            methods = self.resolver.resolve_all(methods)
            common_parameters = get_common_parameters(methods)
            for method, resolved_definition in methods.items():
                if method not in self.operations or "operationId" not in resolved_definition:
                    continue
                parameters = itertools.chain(resolved_definition.get("parameters", ()), common_parameters)
                raw_definition = EndpointDefinition(raw_methods[method], resolved_definition, scope)
                yield resolved_definition["operationId"], self.make_endpoint(
                    path, method, parameters, resolved_definition, raw_definition
                )

    def get_endpoint_by_reference(self, reference: str) -> Endpoint:
        """Get local or external `Endpoint` instance by reference.

        Reference example: #/paths/~1users~1{user_id}/patch
        """
        scope, data = self.resolver.resolve(reference)
        path, method = scope.rsplit("/", maxsplit=2)[-2:]
        path = path.replace("~1", "/").replace("~0", "~")
        resolved_definition = self.resolver.resolve_all(data)
        parent_ref, _ = reference.rsplit("/", maxsplit=1)
        _, methods = self.resolver.resolve(parent_ref)
        common_parameters = get_common_parameters(methods)
        parameters = itertools.chain(resolved_definition.get("parameters", ()), common_parameters)
        raw_definition = EndpointDefinition(data, resolved_definition, scope)
        return self.make_endpoint(path, method, parameters, resolved_definition, raw_definition)


class SwaggerV20(BaseOpenAPISchema):
    nullable_name = "x-nullable"
    example_field = "x-example"
    examples_field = "x-examples"
    operations: Tuple[str, ...] = ("get", "put", "post", "delete", "options", "head", "patch")
    security = SwaggerSecurityProcessor()
    links_field = "x-links"

    @property
    def spec_version(self) -> str:
        return self.raw_schema["swagger"]

    @property
    def verbose_name(self) -> str:
        return f"Swagger {self.spec_version}"

    def _get_base_path(self) -> str:
        return self.raw_schema.get("basePath", "/")

    def get_strategies_from_examples(self, endpoint: Endpoint) -> List[SearchStrategy[Case]]:
        """Get examples from endpoint."""
        return get_strategies_from_examples(endpoint, self.examples_field)

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

    def get_hypothesis_conversion(self, definitions: List[Dict[str, Any]]) -> Optional[Callable]:
        return serialization.serialize_swagger2_parameters(definitions)

    def prepare_multipart(
        self, form_data: FormData, endpoint: Endpoint
    ) -> Tuple[Optional[List], Optional[Dict[str, Any]]]:
        files, data = [], {}
        # If there is no content types specified for the request or "application/x-www-form-urlencoded" is specified
        # explicitly, then use it., but if "multipart/form-data" is specified, then use it
        consumes = self.get_request_payload_content_types(endpoint)
        is_multipart = "multipart/form-data" in consumes
        for parameter in endpoint.definition.resolved.get("parameters", ()):
            name = parameter["name"]
            if name in form_data:
                if parameter["in"] == "formData" and (is_file(parameter) or is_multipart):
                    if isinstance(form_data[name], list):
                        for item in form_data[name]:
                            files.append((name, (None, item)))
                    else:
                        files.append((name, form_data[name]))
                else:
                    data[name] = form_data[name]
        # `None` is the default value for `files` and `data` arguments in `requests.request`
        return files or None, data or None

    def get_request_payload_content_types(self, endpoint: Endpoint) -> List[str]:
        global_consumes = endpoint.schema.raw_schema.get("consumes", [])
        consumes = endpoint.definition.resolved.get("consumes", [])
        if not consumes:
            consumes = global_consumes
        return consumes


class OpenApi30(SwaggerV20):  # pylint: disable=too-many-ancestors
    nullable_name = "nullable"
    example_field = "example"
    examples_field = "examples"
    operations = SwaggerV20.operations + ("trace",)
    security = OpenAPISecurityProcessor()
    links_field = "links"

    @property
    def spec_version(self) -> str:
        return self.raw_schema["openapi"]

    @property
    def verbose_name(self) -> str:
        return f"Open API {self.spec_version}"

    def _get_base_path(self) -> str:
        servers = self.raw_schema.get("servers", [])
        if servers:
            # assume we're the first server in list
            server = servers[0]
            url = server["url"].format(**{k: v["default"] for k, v in server.get("variables", {}).items()})
            return urlsplit(url).path
        return "/"

    def make_endpoint(  # pylint: disable=too-many-arguments
        self,
        path: str,
        method: str,
        parameters: Iterator[Dict[str, Any]],
        resolved_definition: Dict[str, Any],
        raw_definition: EndpointDefinition,
    ) -> Endpoint:
        """Create JSON schemas for query, body, etc from Swagger parameters definitions."""
        endpoint = super().make_endpoint(path, method, parameters, resolved_definition, raw_definition)
        if "requestBody" in resolved_definition:
            self.process_body(endpoint, resolved_definition["requestBody"])
        return endpoint

    def process_by_type(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        if parameter["in"] == "cookie":
            self.process_cookie(endpoint, parameter)
        else:
            super().process_by_type(endpoint, parameter)

    def add_examples(self, container: Dict[str, Any], parameter: Dict[str, Any]) -> Dict[str, Any]:
        schema = get_schema_from_parameter(parameter)
        if self.example_field in schema:
            examples = container.setdefault("example", {})  # examples should be merged together
            examples[parameter["name"]] = schema[self.example_field]
        # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.2.md#parameter-object
        # > Furthermore, if referencing a schema which contains an example,
        # > the example value SHALL override the example provided by the schema
        return super().add_examples(container, parameter)

    def process_cookie(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        endpoint.cookies = self.add_parameter(endpoint.cookies, parameter)

    def process_body(self, endpoint: Endpoint, parameter: Dict[str, Any]) -> None:
        # Take the first media type object
        options = iter(parameter["content"].items())
        try:
            content_type, parameter = next(options)
        except StopIteration:
            # empty "content" value
            return None
        # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.2.md#media-type-object
        # > Furthermore, if referencing a schema which contains an example,
        # > the example value SHALL override the example provided by the schema
        if "example" in parameter:
            schema = get_schema_from_parameter(parameter)
            schema["example"] = parameter["example"]
        if content_type in ("multipart/form-data", "application/x-www-form-urlencoded"):
            endpoint.form_data = parameter["schema"]
        else:
            super().process_body(endpoint, parameter)

    def parameter_to_json_schema(self, data: Dict[str, Any]) -> Dict[str, Any]:
        schema = get_schema_from_parameter(data)
        return super().parameter_to_json_schema(schema)

    def get_response_schema(self, definition: Dict[str, Any], scope: str) -> Tuple[List[str], Optional[Dict[str, Any]]]:
        scopes, definition = self.resolver.resolve_in_scope(deepcopy(definition), scope)
        options = iter(definition.get("content", {}).values())
        option = next(options, None)
        if option:
            # Extra conversion to JSON Schema is needed here if there was one $ref in the input
            # because it is not converted
            return scopes, to_json_schema_recursive(option["schema"], self.nullable_name)
        return scopes, None

    def get_strategies_from_examples(self, endpoint: Endpoint) -> List[SearchStrategy[Case]]:
        """Get examples from endpoint."""
        return get_strategies_from_examples(endpoint, self.examples_field)

    def get_content_types(self, endpoint: Endpoint, response: GenericResponse) -> List[str]:
        try:
            responses = endpoint.definition.raw["responses"]
        except KeyError:
            # Possible to get if `validate_schema=False` is passed during schema creation
            raise InvalidSchema("Schema parsing failed. Please check your schema.")
        status_code = str(response.status_code)
        if status_code in responses:
            definitions = responses[status_code]
        elif "default" in responses:
            definitions = responses["default"]
        else:
            return []
        return list(definitions.get("content", {}).keys())

    def get_hypothesis_conversion(self, definitions: List[Dict[str, Any]]) -> Optional[Callable]:
        return serialization.serialize_openapi3_parameters(definitions)

    def get_request_payload_content_types(self, endpoint: Endpoint) -> List[str]:
        return list(endpoint.definition.resolved["requestBody"]["content"].keys())

    def prepare_multipart(
        self, form_data: FormData, endpoint: Endpoint
    ) -> Tuple[Optional[List], Optional[Dict[str, Any]]]:
        files, data = [], {}
        content = endpoint.definition.resolved["requestBody"]["content"]
        if "multipart/form-data" in content:
            schema = content["multipart/form-data"]["schema"]
            is_multipart = True
        else:
            schema = next(iter(content.values()))["schema"]
            is_multipart = False
        for name, property_schema in schema.get("properties", {}).items():
            if name in form_data:
                if is_multipart:
                    if isinstance(form_data[name], list):
                        files.extend([(name, item) for item in form_data[name]])
                    elif is_file(property_schema):
                        files.append((name, form_data[name]))
                    else:
                        files.append((name, (None, form_data[name])))
                else:
                    data[name] = form_data[name]
        # `None` is the default value for `files` and `data` arguments in `requests.request`
        return files or None, data or None


def is_file(schema: Dict[str, Any]) -> bool:
    return schema.get("format") in ("binary", "base64")


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


def get_schema_from_parameter(data: Dict[str, Any]) -> Dict[str, Any]:
    # In Open API 3.0 there could be "schema" or "content" field. They are mutually exclusive
    if "schema" in data:
        return data["schema"]
    options = iter(data["content"].values())
    return next(options)["schema"]
