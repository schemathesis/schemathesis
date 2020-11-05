# pylint: disable=too-many-ancestors
import itertools
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from difflib import get_close_matches
from json import JSONDecodeError
from typing import Any, Callable, Dict, Generator, List, Optional, Sequence, Tuple, Type, Union
from urllib.parse import urlsplit

import jsonschema
import requests
from hypothesis.strategies import SearchStrategy

from ...constants import DataGenerationMethod
from ...exceptions import InvalidSchema, get_response_parsing_error, get_schema_validation_error
from ...hooks import HookContext, HookDispatcher
from ...models import Case, Endpoint, EndpointDefinition, empty_object
from ...schemas import BaseSchema
from ...stateful import APIStateMachine, Feedback, Stateful, StatefulTest
from ...types import FormData
from ...utils import GenericResponse
from . import links, serialization
from ._hypothesis import get_case_strategy
from .converter import to_json_schema_recursive
from .examples import get_strategies_from_examples
from .filters import (
    should_skip_by_operation_id,
    should_skip_by_tag,
    should_skip_deprecated,
    should_skip_endpoint,
    should_skip_method,
)
from .references import ConvertingResolver
from .security import BaseSecurityProcessor, OpenAPISecurityProcessor, SwaggerSecurityProcessor
from .stateful import create_state_machine


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
        self, response: GenericResponse, endpoint: Endpoint, stateful: Optional[Stateful]
    ) -> Sequence[StatefulTest]:
        if stateful == Stateful.links:
            return links.get_links(response, endpoint, field=self.links_field)
        return []

    def __repr__(self) -> str:
        info = self.raw_schema["info"]
        return f"{self.__class__.__name__} for {info['title']} ({info['version']})"

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
                        or should_skip_deprecated(
                            resolved_definition.get("deprecated", False), self.skip_deprecated_endpoints
                        )
                        or should_skip_by_tag(resolved_definition.get("tags"), self.tag)
                        or should_skip_by_operation_id(resolved_definition.get("operationId"), self.operation_id)
                    ):
                        continue
                    parameters = list(itertools.chain(resolved_definition.get("parameters", ()), common_parameters))
                    # To prevent recursion errors we need to pass not resolved schema as well
                    # It could be used for response validation
                    raw_definition = EndpointDefinition(raw_methods[method], resolved_definition, scope, parameters)
                    yield self.make_endpoint(path, method, parameters, resolved_definition, raw_definition)
        except (KeyError, AttributeError, jsonschema.exceptions.RefResolutionError) as exc:
            raise InvalidSchema("Schema parsing failed. Please check your schema.") from exc

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
        parameters: List[Dict[str, Any]],
        resolved_definition: Dict[str, Any],
        raw_definition: EndpointDefinition,
    ) -> Endpoint:
        """Create JSON schemas for the query, body, etc from Swagger parameters definitions."""
        base_url = self.get_base_url()
        endpoint = Endpoint(
            path=path,
            method=method,
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
        """Get examples from the endpoint."""
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
                parameters = list(itertools.chain(resolved_definition.get("parameters", ()), common_parameters))
                raw_definition = EndpointDefinition(raw_methods[method], resolved_definition, scope, parameters)
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
        parameters = list(itertools.chain(resolved_definition.get("parameters", ()), common_parameters))
        raw_definition = EndpointDefinition(data, resolved_definition, scope, parameters)
        return self.make_endpoint(path, method, parameters, resolved_definition, raw_definition)

    def get_case_strategy(
        self,
        endpoint: Endpoint,
        hooks: Optional[HookDispatcher] = None,
        feedback: Optional[Feedback] = None,
        data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
    ) -> SearchStrategy:
        return get_case_strategy(endpoint, hooks, feedback, data_generation_method)

    def get_hypothesis_conversion(self, endpoint: Endpoint, location: str) -> Optional[Callable]:
        definitions = [item for item in endpoint.definition.resolved.get("parameters", []) if item["in"] == location]
        security_parameters = self.security.get_security_definitions_as_parameters(
            self.raw_schema, endpoint, self.resolver, location
        )
        if security_parameters:
            definitions.extend(security_parameters)
        if definitions:
            return self._get_hypothesis_conversion(definitions)
        return None

    def _get_hypothesis_conversion(self, definitions: List[Dict[str, Any]]) -> Optional[Callable]:
        raise NotImplementedError

    def _get_response_definitions(self, endpoint: Endpoint, response: GenericResponse) -> Optional[Dict[str, Any]]:
        try:
            responses = endpoint.definition.resolved["responses"]
        except KeyError as exc:
            # Possible to get if `validate_schema=False` is passed during schema creation
            raise InvalidSchema("Schema parsing failed. Please check your schema.") from exc
        status_code = str(response.status_code)
        if status_code in responses:
            return responses[status_code]
        if "default" in responses:
            return responses["default"]
        return None

    def get_headers(self, endpoint: Endpoint, response: GenericResponse) -> Optional[Dict[str, Dict[str, Any]]]:
        definitions = self._get_response_definitions(endpoint, response)
        if not definitions:
            return None
        return definitions.get("headers")

    def as_state_machine(self) -> Type[APIStateMachine]:
        return create_state_machine(self)

    def add_link(  # pylint: disable=too-many-arguments
        self,
        source: Endpoint,
        target: Union[str, Endpoint],
        status_code: Union[str, int],
        parameters: Optional[Dict[str, str]] = None,
        request_body: Any = None,
    ) -> None:
        """Add a new Open API link to the schema definition.

        :param Endpoint source: This operation is the source of data
        :param target: This operation will receive the data from this link.
            Can be an ``Endpoint`` instance or a reference like this - ``#/paths/~1users~1{userId}/get``
        :param str status_code: The link is triggered when the source endpoint responds with this status code.
        :param parameters: A dictionary that describes how parameters should be extracted from the matched response.
            The key represents the parameter name in the target endpoint, and the value is a runtime expression string.
        :param request_body: A literal value or runtime expression to use as a request body when
            calling the target operation.

        .. code-block:: python

            schema = schemathesis.from_uri("http://0.0.0.0/schema.yaml")

            schema.add_link(
                source=schema["/users/"]["POST"],
                target=schema["/users/{userId}"]["GET"],
                status_code="201",
                parameters={
                    "userId": "$response.body#/id"
                }
            )
        """
        if parameters is None and request_body is None:
            raise ValueError("You need to provide `parameters` or `request_body`.")
        if hasattr(self, "_endpoints"):
            delattr(self, "_endpoints")
        for endpoint, methods in self.raw_schema["paths"].items():
            if endpoint == source.path:
                # Methods should be completely resolved now, otherwise they might miss a resolving scope when
                # they will be fully resolved later
                methods = self.resolver.resolve_all(methods)
                found = False
                for method, definition in methods.items():
                    if method.upper() == source.method.upper():
                        found = True
                        links.add_link(
                            definition["responses"], self.links_field, parameters, request_body, status_code, target
                        )
                    # If methods are behind a reference, then on the next resolving they will miss the new link
                    # Therefore we need to modify it this way
                    self.raw_schema["paths"][endpoint][method] = definition
                # The reference should be removed completely, otherwise new keys in this dictionary will be ignored
                # due to the `$ref` keyword behavior
                self.raw_schema["paths"][endpoint].pop("$ref", None)
                if found:
                    return
        message = f"No such endpoint: `{source.verbose_name}`."
        possibilities = [e.verbose_name for e in self.get_all_endpoints()]
        matches = get_close_matches(source.verbose_name, possibilities)
        if matches:
            message += f" Did you mean `{matches[0]}`?"
        message += " Check if the requested endpoint passes the filters in the schema."
        raise ValueError(message)

    def get_links(self, endpoint: Endpoint) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = defaultdict(dict)
        for status_code, link in links.get_all_links(endpoint):
            result[status_code][link.name] = link
        return result

    def validate_response(self, endpoint: Endpoint, response: GenericResponse) -> None:
        responses = {str(key): value for key, value in endpoint.definition.raw.get("responses", {}).items()}
        status_code = str(response.status_code)
        if status_code in responses:
            definition = responses[status_code]
        elif "default" in responses:
            definition = responses["default"]
        else:
            # No response defined for the received response status code
            return
        scopes, schema = self.get_response_schema(definition, endpoint.definition.scope)
        if not schema:
            return
        try:
            if isinstance(response, requests.Response):
                data = response.json()
            else:
                data = response.json
        except JSONDecodeError as exc:
            exc_class = get_response_parsing_error(exc)
            if isinstance(response, requests.Response):
                raw_content = response.content
            else:
                raw_content = response.get_data()
            payload = raw_content.decode(errors="replace")
            raise exc_class(
                f"The received response is not valid JSON:\n\n    {payload}\n\nException: \n\n    {exc}"
            ) from exc
        with in_scopes(self.resolver, scopes):
            try:
                jsonschema.validate(data, schema, cls=jsonschema.Draft4Validator, resolver=self.resolver)
            except jsonschema.ValidationError as exc:
                exc_class = get_schema_validation_error(exc)
                raise exc_class(
                    f"The received response does not conform to the defined schema!\n\nDetails: \n\n{exc}"
                ) from exc
        return None  # explicitly return None for mypy


@contextmanager
def in_scopes(resolver: jsonschema.RefResolver, scopes: List[str]) -> Generator[None, None, None]:
    """Push all available scopes into the resolver.

    There could be an additional scope change during a schema resolving in `get_response_schema`, so in total there
    could be a stack of two scopes maximum. This context manager handles both cases (1 or 2 scope changes) in the same
    way.
    """
    with ExitStack() as stack:
        for scope in scopes:
            stack.enter_context(resolver.in_scope(scope))
        yield


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
        """Get examples from the endpoint."""
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

    def _get_hypothesis_conversion(self, definitions: List[Dict[str, Any]]) -> Optional[Callable]:
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
        parameters: List[Dict[str, Any]],
        resolved_definition: Dict[str, Any],
        raw_definition: EndpointDefinition,
    ) -> Endpoint:
        """Create JSON schemas for the query, body, etc from Swagger parameters definitions."""
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
        """Get examples from the endpoint."""
        return get_strategies_from_examples(endpoint, self.examples_field)

    def get_content_types(self, endpoint: Endpoint, response: GenericResponse) -> List[str]:
        definitions = self._get_response_definitions(endpoint, response)
        if not definitions:
            return []
        return list(definitions.get("content", {}).keys())

    def _get_hypothesis_conversion(self, definitions: List[Dict[str, Any]]) -> Optional[Callable]:
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


def get_schema_from_parameter(data: Dict[str, Any]) -> Dict[str, Any]:
    # In Open API 3.0 there could be "schema" or "content" field. They are mutually exclusive.
    if "schema" in data:
        return data["schema"]
    options = iter(data["content"].values())
    return next(options)["schema"]
