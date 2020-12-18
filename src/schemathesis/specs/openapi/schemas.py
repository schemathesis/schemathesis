# pylint: disable=too-many-ancestors
import itertools
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from difflib import get_close_matches
from json import JSONDecodeError
from typing import Any, Callable, Dict, Generator, Iterable, List, Optional, Sequence, Tuple, Type, Union
from urllib.parse import urlsplit

import jsonschema
import requests
from hypothesis.strategies import SearchStrategy

from ...constants import DataGenerationMethod
from ...exceptions import (
    InvalidSchema,
    get_missing_content_type_error,
    get_response_parsing_error,
    get_schema_validation_error,
)
from ...hooks import HookContext, HookDispatcher
from ...models import Case, Endpoint, EndpointDefinition
from ...schemas import BaseSchema
from ...stateful import APIStateMachine, Feedback, Stateful, StatefulTest
from ...types import FormData
from ...utils import GenericResponse, get_response_payload
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
from .parameters import (
    OpenAPI20Body,
    OpenAPI20CompositeBody,
    OpenAPI20Parameter,
    OpenAPI30Body,
    OpenAPI30Parameter,
    OpenAPIParameter,
)
from .references import ConvertingResolver
from .security import BaseSecurityProcessor, OpenAPISecurityProcessor, SwaggerSecurityProcessor
from .stateful import create_state_machine


class BaseOpenAPISchema(BaseSchema):
    nullable_name: str
    links_field: str
    operations: Tuple[str, ...]
    security: BaseSecurityProcessor
    parameter_cls: Type[OpenAPIParameter]
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
                    parameters = self.collect_parameters(
                        itertools.chain(resolved_definition.get("parameters", ()), common_parameters),
                        resolved_definition,
                    )
                    # To prevent recursion errors we need to pass not resolved schema as well
                    # It could be used for response validation
                    raw_definition = EndpointDefinition(raw_methods[method], resolved_definition, scope, parameters)
                    yield self.make_endpoint(path, method, parameters, raw_definition)
        except (KeyError, AttributeError, jsonschema.exceptions.RefResolutionError) as exc:
            raise InvalidSchema("Schema parsing failed. Please check your schema.") from exc

    def collect_parameters(
        self, parameters: Iterable[Dict[str, Any]], endpoint_definition: Dict[str, Any]
    ) -> List[OpenAPIParameter]:
        """Collect Open API parameters.

        They should be used uniformly during the generation step; therefore, we need to convert them into
        a spec-independent list of parameters.
        """
        raise NotImplementedError

    def _resolve_methods(self, methods: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        # We need to know a proper scope in what methods are.
        # It will allow us to provide a proper reference resolving in `response_schema_conformance` and avoid
        # recursion errors
        if "$ref" in methods:
            return deepcopy(self.resolver.resolve(methods["$ref"]))
        return self.resolver.resolution_scope, deepcopy(methods)

    def make_endpoint(
        self,
        path: str,
        method: str,
        parameters: List[OpenAPIParameter],
        raw_definition: EndpointDefinition,
    ) -> Endpoint:
        """Create JSON schemas for the query, body, etc from Swagger parameters definitions."""
        base_url = self.get_base_url()
        endpoint: Endpoint[OpenAPIParameter] = Endpoint(
            path=path,
            method=method,
            definition=raw_definition,
            base_url=base_url,
            app=self.app,
            schema=self,
        )
        for parameter in parameters:
            endpoint.add_parameter(parameter)
        self.security.process_definitions(self.raw_schema, endpoint, self.resolver)
        return endpoint

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
                parameters = self.collect_parameters(
                    itertools.chain(resolved_definition.get("parameters", ()), common_parameters), resolved_definition
                )
                raw_definition = EndpointDefinition(raw_methods[method], resolved_definition, scope, parameters)
                yield resolved_definition["operationId"], self.make_endpoint(path, method, parameters, raw_definition)

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
        parameters = self.collect_parameters(
            itertools.chain(resolved_definition.get("parameters", ()), common_parameters), resolved_definition
        )
        raw_definition = EndpointDefinition(data, resolved_definition, scope, parameters)
        return self.make_endpoint(path, method, parameters, raw_definition)

    def get_case_strategy(
        self,
        endpoint: Endpoint,
        hooks: Optional[HookDispatcher] = None,
        feedback: Optional[Feedback] = None,
        data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
    ) -> SearchStrategy:
        return get_case_strategy(endpoint, hooks, feedback, data_generation_method)

    def get_parameter_serializer(self, endpoint: Endpoint, location: str) -> Optional[Callable]:
        definitions = [item for item in endpoint.definition.resolved.get("parameters", []) if item["in"] == location]
        security_parameters = self.security.get_security_definitions_as_parameters(
            self.raw_schema, endpoint, self.resolver, location
        )
        if security_parameters:
            definitions.extend(security_parameters)
        if definitions:
            return self._get_parameter_serializer(definitions)
        return None

    def _get_parameter_serializer(self, definitions: List[Dict[str, Any]]) -> Optional[Callable]:
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

    def add_link(
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
            # No schema to check against
            return
        content_type = response.headers.get("Content-Type")
        if content_type is None:
            media_types = "\n    ".join(self.get_content_types(endpoint, response))
            raise get_missing_content_type_error()(
                "The response is missing the `Content-Type` header. The schema defines the following media types:\n\n"
                f"    {media_types}"
            )
        if not content_type.startswith("application/json"):
            return
        try:
            if isinstance(response, requests.Response):
                data = response.json()
            else:
                data = response.json
        except JSONDecodeError as exc:
            exc_class = get_response_parsing_error(exc)
            payload = get_response_payload(response)
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


OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE = "application/json"
OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE = "multipart/form-data"


class SwaggerV20(BaseOpenAPISchema):
    nullable_name = "x-nullable"
    example_field = "x-example"
    examples_field = "x-examples"
    operations: Tuple[str, ...] = ("get", "put", "post", "delete", "options", "head", "patch")
    parameter_cls: Type[OpenAPIParameter] = OpenAPI20Parameter
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

    def collect_parameters(
        self, parameters: Iterable[Dict[str, Any]], endpoint_definition: Dict[str, Any]
    ) -> List[OpenAPIParameter]:
        # The main difference with Open API 3.0 is that it has `body` and `form` parameters that we need to handle
        # differently.
        collected: List[OpenAPIParameter] = []
        # NOTE. The Open API 2.0 spec doesn't strictly imply having media types in the "consumes" keyword.
        # It is not enforced by the meta schema and has no "MUST" verb in the spec text.
        # Also, not every API has operations with payload (they might have only GET endpoints without payloads).
        # For these reasons, it might be (and often is) absent, and we need to provide the proper media type in case
        # we have operations with a payload.
        media_types = self._get_consumes_for_endpoint(endpoint_definition)
        # For `in=body` parameters, we imply `application/json` as the default media type because it is the most common.
        body_media_types = media_types or (OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE,)
        # If an endpoint has parameters with `in=formData`, Schemathesis should know how to serialize it.
        # We can't be 100% sure what media type is expected by the server and chose `multipart/form-data` as
        # the default because it is broader since it allows us to upload files.
        form_data_media_types = media_types or (OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE,)

        form_parameters = []
        for parameter in parameters:
            if parameter["in"] == "formData":
                # We need to gather form parameters first before creating a composite parameter for them
                form_parameters.append(parameter)
            elif parameter["in"] == "body":
                for media_type in body_media_types:
                    collected.append(OpenAPI20Body(definition=parameter, media_type=media_type))
            else:
                collected.append(OpenAPI20Parameter(definition=parameter))

        if form_parameters:
            for media_type in form_data_media_types:
                collected.append(
                    # Individual `formData` parameters are joined into a single "composite" one.
                    OpenAPI20CompositeBody.from_parameters(*form_parameters, media_type=media_type)
                )
        return collected

    def get_strategies_from_examples(self, endpoint: Endpoint) -> List[SearchStrategy[Case]]:
        """Get examples from the endpoint."""
        return get_strategies_from_examples(endpoint, self.examples_field)

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

    def _get_parameter_serializer(self, definitions: List[Dict[str, Any]]) -> Optional[Callable]:
        return serialization.serialize_swagger2_parameters(definitions)

    def prepare_multipart(
        self, form_data: FormData, endpoint: Endpoint
    ) -> Tuple[Optional[List], Optional[Dict[str, Any]]]:
        """Prepare form data for sending with `requests`.

        :param form_data: Raw generated data as a dictionary.
        :param endpoint: The tested endpoint for which the data was generated.
        :return: `files` and `data` values for `requests.request`.
        """
        files, data = [], {}
        # If there is no content types specified for the request or "application/x-www-form-urlencoded" is specified
        # explicitly, then use it., but if "multipart/form-data" is specified, then use it
        content_types = self.get_request_payload_content_types(endpoint)
        is_multipart = "multipart/form-data" in content_types

        def add_file(file_value: Any) -> None:
            if isinstance(file_value, list):
                for item in file_value:
                    files.append((name, (None, item)))
            else:
                files.append((name, file_value))

        for parameter in endpoint.definition.parameters:
            if isinstance(parameter, OpenAPI20CompositeBody):
                for form_parameter in parameter.definition:
                    name = form_parameter.name
                    # It might be not in `form_data`, if the parameter is optional
                    if name in form_data:
                        value = form_data[name]
                        if form_parameter.definition.get("type") == "file" or is_multipart:
                            add_file(value)
                        else:
                            data[name] = value
        # `None` is the default value for `files` and `data` arguments in `requests.request`
        return files or None, data or None

    def get_request_payload_content_types(self, endpoint: Endpoint) -> List[str]:
        return self._get_consumes_for_endpoint(endpoint.definition.resolved)

    def _get_consumes_for_endpoint(self, endpoint_definition: Dict[str, Any]) -> List[str]:
        """Get the `consumes` value for the given endpoint.

        :param endpoint_definition: Raw endpoint definition.
        :return: A list of media-types for this endpoint.
        :rtype: List[str]
        """
        global_consumes = self.raw_schema.get("consumes", [])
        consumes = endpoint_definition.get("consumes", [])
        if not consumes:
            consumes = global_consumes
        return consumes


class OpenApi30(SwaggerV20):  # pylint: disable=too-many-ancestors
    nullable_name = "nullable"
    example_field = "example"
    examples_field = "examples"
    operations = SwaggerV20.operations + ("trace",)
    security = OpenAPISecurityProcessor()
    parameter_cls = OpenAPI30Parameter
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

    def collect_parameters(
        self, parameters: Iterable[Dict[str, Any]], endpoint_definition: Dict[str, Any]
    ) -> List[OpenAPIParameter]:
        # Open API 3.0 has the `requestBody` keyword, which may contain multiple different payload variants.
        collected: List[OpenAPIParameter] = [OpenAPI30Parameter(definition=parameter) for parameter in parameters]
        if "requestBody" in endpoint_definition:
            required = endpoint_definition["requestBody"].get("required", False)
            for media_type, definition in endpoint_definition["requestBody"]["content"].items():
                collected.append(OpenAPI30Body(definition, media_type=media_type, required=required))
        return collected

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

    def _get_parameter_serializer(self, definitions: List[Dict[str, Any]]) -> Optional[Callable]:
        return serialization.serialize_openapi3_parameters(definitions)

    def get_request_payload_content_types(self, endpoint: Endpoint) -> List[str]:
        return list(endpoint.definition.resolved["requestBody"]["content"].keys())

    def prepare_multipart(
        self, form_data: FormData, endpoint: Endpoint
    ) -> Tuple[Optional[List], Optional[Dict[str, Any]]]:
        """Prepare form data for sending with `requests`.

        :param form_data: Raw generated data as a dictionary.
        :param endpoint: The tested endpoint for which the data was generated.
        :return: `files` and `data` values for `requests.request`.
        """
        files = []
        content = endpoint.definition.resolved["requestBody"]["content"]
        # Open API 3.0 requires media types to be present. We can get here only if the schema defines
        # the "multipart/form-data" media type
        schema = content["multipart/form-data"]["schema"]
        for name, property_schema in schema.get("properties", {}).items():
            if name in form_data:
                if isinstance(form_data[name], list):
                    files.extend([(name, item) for item in form_data[name]])
                elif property_schema.get("format") in ("binary", "base64"):
                    files.append((name, form_data[name]))
                else:
                    files.append((name, (None, form_data[name])))
        # `None` is the default value for `files` and `data` arguments in `requests.request`
        return files or None, None


def get_common_parameters(methods: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Common parameters are deep copied from the methods definitions.

    Copying is needed because of further modifications.
    """
    common_parameters = methods.get("parameters")
    if common_parameters is not None:
        return deepcopy(common_parameters)
    return []
