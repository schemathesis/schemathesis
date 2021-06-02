# pylint: disable=too-many-ancestors
import itertools
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from difflib import get_close_matches
from json import JSONDecodeError
from typing import Any, Callable, ClassVar, Dict, Generator, Iterable, List, Optional, Sequence, Tuple, Type, Union
from urllib.parse import urlsplit

import jsonschema
import requests
from hypothesis.strategies import SearchStrategy

from ... import failures
from ...constants import DataGenerationMethod
from ...exceptions import (
    InvalidSchema,
    get_missing_content_type_error,
    get_response_parsing_error,
    get_schema_validation_error,
)
from ...hooks import HookContext, HookDispatcher
from ...models import APIOperation, Case, OperationDefinition
from ...schemas import BaseSchema
from ...stateful import APIStateMachine, Stateful, StatefulTest
from ...types import FormData
from ...utils import Err, GenericResponse, Ok, Result, get_response_payload, is_json_media_type
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
from .references import RECURSION_DEPTH_LIMIT, ConvertingResolver, InliningResolver
from .security import BaseSecurityProcessor, OpenAPISecurityProcessor, SwaggerSecurityProcessor
from .stateful import create_state_machine

SCHEMA_ERROR_MESSAGE = "Schema parsing failed. Please check your schema."
SCHEMA_PARSING_ERRORS = (KeyError, AttributeError, jsonschema.exceptions.RefResolutionError)


class BaseOpenAPISchema(BaseSchema):
    nullable_name: str
    links_field: str
    allowed_http_methods: Tuple[str, ...]
    security: BaseSecurityProcessor
    parameter_cls: Type[OpenAPIParameter]
    component_locations: ClassVar[Tuple[str, ...]] = ()
    _operations_by_id: Dict[str, APIOperation]

    @property  # pragma: no mutate
    def spec_version(self) -> str:
        raise NotImplementedError

    def get_stateful_tests(
        self, response: GenericResponse, operation: APIOperation, stateful: Optional[Stateful]
    ) -> Sequence[StatefulTest]:
        if stateful == Stateful.links:
            return links.get_links(response, operation, field=self.links_field)
        return []

    def __repr__(self) -> str:
        info = self.raw_schema["info"]
        return f"{self.__class__.__name__} for {info['title']} ({info['version']})"

    def get_all_operations(self) -> Generator[Result[APIOperation, InvalidSchema], None, None]:
        """Iterate over all operations defined in the API.

        Each yielded item is either `Ok` or `Err`, depending on the presence of errors during schema processing.

        There are two cases for the `Err` variant:

          1. The error happened while resolving a group of API operations. In Open API, you can put all operations for
             a specific path behind a reference, and if it is not resolvable, then all operations won't be available
             for testing. For example - a file with those definitions was removed. Here we know the path but don't
             know what operations are there.
          2. Errors while processing a known API operation.

        In both cases, Schemathesis lets the callee decide what to do with these variants. It allows it to test valid
        operations and show errors for invalid ones.
        """
        try:
            paths = self.raw_schema["paths"]  # pylint: disable=unsubscriptable-object
        except KeyError as exc:
            # Missing `paths` is not recoverable
            raise InvalidSchema(SCHEMA_ERROR_MESSAGE) from exc

        context = HookContext()
        for path, methods in paths.items():
            method = None
            try:
                full_path = self.get_full_path(path)  # Should be available for later use
                if should_skip_endpoint(full_path, self.endpoint):
                    continue
                self.dispatch_hook("before_process_path", context, path, methods)
                scope, raw_methods = self._resolve_methods(methods)
                common_parameters = self.resolver.resolve_all(methods.get("parameters", []), RECURSION_DEPTH_LIMIT - 5)
                for method, definition in raw_methods.items():
                    try:
                        # Setting a low recursion limit doesn't solve the problem with recursive references & inlining
                        # too much but decreases the number of cases when Schemathesis stuck on this step.
                        with self.resolver.in_scope(scope):
                            resolved_definition = self.resolver.resolve_all(definition, RECURSION_DEPTH_LIMIT - 5)
                        # Only method definitions are parsed
                        if (
                            method not in self.allowed_http_methods
                            or should_skip_method(method, self.method)
                            or should_skip_deprecated(
                                resolved_definition.get("deprecated", False), self.skip_deprecated_operations
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
                        raw_definition = OperationDefinition(
                            raw_methods[method], resolved_definition, scope, parameters
                        )
                        yield Ok(self.make_operation(path, method, parameters, raw_definition))
                    except SCHEMA_PARSING_ERRORS as exc:
                        yield self._into_err(exc, path, method)
            except SCHEMA_PARSING_ERRORS as exc:
                yield self._into_err(exc, path, method)

    def _into_err(self, error: Exception, path: Optional[str], method: Optional[str]) -> Err[InvalidSchema]:
        try:
            full_path = self.get_full_path(path) if isinstance(path, str) else None
            raise InvalidSchema(SCHEMA_ERROR_MESSAGE, path=path, method=method, full_path=full_path) from error
        except InvalidSchema as exc:
            return Err(exc)

    def collect_parameters(
        self, parameters: Iterable[Dict[str, Any]], definition: Dict[str, Any]
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

    def make_operation(
        self,
        path: str,
        method: str,
        parameters: List[OpenAPIParameter],
        raw_definition: OperationDefinition,
    ) -> APIOperation:
        """Create JSON schemas for the query, body, etc from Swagger parameters definitions."""
        base_url = self.get_base_url()
        operation: APIOperation[OpenAPIParameter, Case] = APIOperation(
            path=path,
            method=method,
            definition=raw_definition,
            base_url=base_url,
            app=self.app,
            schema=self,
        )
        for parameter in parameters:
            operation.add_parameter(parameter)
        self.security.process_definitions(self.raw_schema, operation, self.resolver)
        return operation

    @property
    def resolver(self) -> InliningResolver:
        if not hasattr(self, "_resolver"):
            # pylint: disable=attribute-defined-outside-init
            self._resolver = InliningResolver(self.location or "", self.raw_schema)
        return self._resolver

    def get_content_types(self, operation: APIOperation, response: GenericResponse) -> List[str]:
        """Content types available for this API operation."""
        raise NotImplementedError

    def get_strategies_from_examples(self, operation: APIOperation) -> List[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        raise NotImplementedError

    def get_response_schema(self, definition: Dict[str, Any], scope: str) -> Tuple[List[str], Optional[Dict[str, Any]]]:
        """Extract response schema from `responses`."""
        raise NotImplementedError

    def get_operation_by_id(self, operation_id: str) -> APIOperation:
        """Get an `APIOperation` instance by its `operationId`."""
        if not hasattr(self, "_operations_by_id"):
            self._operations_by_id = dict(self._group_operations_by_id())
        return self._operations_by_id[operation_id]

    def _group_operations_by_id(self) -> Generator[Tuple[str, APIOperation], None, None]:
        for path, methods in self.raw_schema["paths"].items():
            scope, raw_methods = self._resolve_methods(methods)
            common_parameters = self.resolver.resolve_all(methods.get("parameters", []), RECURSION_DEPTH_LIMIT - 5)
            for method, definition in methods.items():
                if method not in self.allowed_http_methods or "operationId" not in definition:
                    continue
                with self.resolver.in_scope(scope):
                    resolved_definition = self.resolver.resolve_all(definition, RECURSION_DEPTH_LIMIT - 5)
                parameters = self.collect_parameters(
                    itertools.chain(resolved_definition.get("parameters", ()), common_parameters), resolved_definition
                )
                raw_definition = OperationDefinition(raw_methods[method], resolved_definition, scope, parameters)
                yield resolved_definition["operationId"], self.make_operation(path, method, parameters, raw_definition)

    def get_operation_by_reference(self, reference: str) -> APIOperation:
        """Get local or external `APIOperation` instance by reference.

        Reference example: #/paths/~1users~1{user_id}/patch
        """
        scope, data = self.resolver.resolve(reference)
        path, method = scope.rsplit("/", maxsplit=2)[-2:]
        path = path.replace("~1", "/").replace("~0", "~")
        resolved_definition = self.resolver.resolve_all(data)
        parent_ref, _ = reference.rsplit("/", maxsplit=1)
        _, methods = self.resolver.resolve(parent_ref)
        common_parameters = self.resolver.resolve_all(methods.get("parameters", []), RECURSION_DEPTH_LIMIT - 5)
        parameters = self.collect_parameters(
            itertools.chain(resolved_definition.get("parameters", ()), common_parameters), resolved_definition
        )
        raw_definition = OperationDefinition(data, resolved_definition, scope, parameters)
        return self.make_operation(path, method, parameters, raw_definition)

    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: Optional[HookDispatcher] = None,
        data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
    ) -> SearchStrategy:
        return get_case_strategy(operation=operation, hooks=hooks, data_generation_method=data_generation_method)

    def get_parameter_serializer(self, operation: APIOperation, location: str) -> Optional[Callable]:
        definitions = [item for item in operation.definition.resolved.get("parameters", []) if item["in"] == location]
        security_parameters = self.security.get_security_definitions_as_parameters(
            self.raw_schema, operation, self.resolver, location
        )
        security_parameters = [item for item in security_parameters if item["in"] == location]
        if security_parameters:
            definitions.extend(security_parameters)
        if definitions:
            return self._get_parameter_serializer(definitions)
        return None

    def _get_parameter_serializer(self, definitions: List[Dict[str, Any]]) -> Optional[Callable]:
        raise NotImplementedError

    def _get_response_definitions(self, operation: APIOperation, response: GenericResponse) -> Optional[Dict[str, Any]]:
        try:
            responses = operation.definition.resolved["responses"]
        except KeyError as exc:
            # Possible to get if `validate_schema=False` is passed during schema creation
            raise InvalidSchema("Schema parsing failed. Please check your schema.") from exc
        status_code = str(response.status_code)
        if status_code in responses:
            return responses[status_code]
        if "default" in responses:
            return responses["default"]
        return None

    def get_headers(self, operation: APIOperation, response: GenericResponse) -> Optional[Dict[str, Dict[str, Any]]]:
        definitions = self._get_response_definitions(operation, response)
        if not definitions:
            return None
        return definitions.get("headers")

    def as_state_machine(self) -> Type[APIStateMachine]:
        return create_state_machine(self)

    def add_link(
        self,
        source: APIOperation,
        target: Union[str, APIOperation],
        status_code: Union[str, int],
        parameters: Optional[Dict[str, str]] = None,
        request_body: Any = None,
    ) -> None:
        """Add a new Open API link to the schema definition.

        :param APIOperation source: This operation is the source of data
        :param target: This operation will receive the data from this link.
            Can be an ``APIOperation`` instance or a reference like this - ``#/paths/~1users~1{userId}/get``
        :param str status_code: The link is triggered when the source API operation responds with this status code.
        :param parameters: A dictionary that describes how parameters should be extracted from the matched response.
            The key represents the parameter name in the target API operation, and the value is a runtime
            expression string.
        :param request_body: A literal value or runtime expression to use as a request body when
            calling the target operation.

        .. code-block:: python

            schema = schemathesis.from_uri("http://0.0.0.0/schema.yaml")

            schema.add_link(
                source=schema["/users/"]["POST"],
                target=schema["/users/{userId}"]["GET"],
                status_code="201",
                parameters={"userId": "$response.body#/id"},
            )
        """
        if parameters is None and request_body is None:
            raise ValueError("You need to provide `parameters` or `request_body`.")
        if hasattr(self, "_operations"):
            delattr(self, "_operations")
        for operation, methods in self.raw_schema["paths"].items():
            if operation == source.path:
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
                    self.raw_schema["paths"][operation][method] = definition
                # The reference should be removed completely, otherwise new keys in this dictionary will be ignored
                # due to the `$ref` keyword behavior
                self.raw_schema["paths"][operation].pop("$ref", None)
                if found:
                    return
        name = f"{source.method.upper()} {source.path}"
        # Use a name without basePath, as the user doesn't use it.
        # E.g. `source=schema["/users/"]["POST"]` without a prefix
        message = f"No such API operation: `{name}`."
        possibilities = [
            f"{op.ok().method.upper()} {op.ok().path}" for op in self.get_all_operations() if isinstance(op, Ok)
        ]
        matches = get_close_matches(name, possibilities)
        if matches:
            message += f" Did you mean `{matches[0]}`?"
        message += " Check if the requested API operation passes the filters in the schema."
        raise ValueError(message)

    def get_links(self, operation: APIOperation) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = defaultdict(dict)
        for status_code, link in links.get_all_links(operation):
            result[status_code][link.name] = link
        return result

    def validate_response(self, operation: APIOperation, response: GenericResponse) -> None:
        responses = {str(key): value for key, value in operation.definition.raw.get("responses", {}).items()}
        status_code = str(response.status_code)
        if status_code in responses:
            definition = responses[status_code]
        elif "default" in responses:
            definition = responses["default"]
        else:
            # No response defined for the received response status code
            return
        scopes, schema = self.get_response_schema(definition, operation.definition.scope)
        if not schema:
            # No schema to check against
            return
        content_type = response.headers.get("Content-Type")
        if content_type is None:
            media_types = self.get_content_types(operation, response)
            formatted_media_types = "\n    ".join(media_types)
            raise get_missing_content_type_error()(
                "The response is missing the `Content-Type` header. The schema defines the following media types:\n\n"
                f"    {formatted_media_types}",
                context=failures.MissingContentType(media_types),
            )
        if not is_json_media_type(content_type):
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
                f"The received response is not valid JSON:\n\n    {payload}\n\nException: \n\n    {exc}",
                context=failures.JSONDecodeErrorContext(
                    validation_message=exc.msg, document=exc.doc, position=exc.pos, lineno=exc.lineno, colno=exc.colno
                ),
            ) from exc
        resolver = ConvertingResolver(self.location or "", self.raw_schema, nullable_name=self.nullable_name)
        with in_scopes(resolver, scopes):
            try:
                jsonschema.validate(data, schema, cls=jsonschema.Draft4Validator, resolver=resolver)
            except jsonschema.ValidationError as exc:
                exc_class = get_schema_validation_error(exc)
                raise exc_class(
                    f"The received response does not conform to the defined schema!\n\nDetails: \n\n{exc}",
                    context=failures.ValidationErrorContext(
                        validation_message=exc.message,
                        schema_path=list(exc.absolute_schema_path),
                        schema=exc.schema,
                        instance_path=list(exc.absolute_path),
                        instance=exc.instance,
                    ),
                ) from exc
        return None  # explicitly return None for mypy

    def prepare_schema(self, schema: Any) -> Any:
        """Inline Open API definitions.

        Inlining components helps `hypothesis-jsonschema` generate data that involves non-resolved references.
        """
        schema = deepcopy(schema)
        # Different spec versions allow different keywords to store possible reference targets
        for key in self.component_locations:
            if key in self.raw_schema:
                schema[key] = to_json_schema_recursive(self.raw_schema[key], self.nullable_name)
        return schema


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
    allowed_http_methods: Tuple[str, ...] = ("get", "put", "post", "delete", "options", "head", "patch")
    parameter_cls: Type[OpenAPIParameter] = OpenAPI20Parameter
    security = SwaggerSecurityProcessor()
    component_locations: ClassVar[Tuple[str, ...]] = ("definitions", "parameters", "responses")
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
        self, parameters: Iterable[Dict[str, Any]], definition: Dict[str, Any]
    ) -> List[OpenAPIParameter]:
        # The main difference with Open API 3.0 is that it has `body` and `form` parameters that we need to handle
        # differently.
        collected: List[OpenAPIParameter] = []
        # NOTE. The Open API 2.0 spec doesn't strictly imply having media types in the "consumes" keyword.
        # It is not enforced by the meta schema and has no "MUST" verb in the spec text.
        # Also, not every API has operations with payload (they might have only GET operations without payloads).
        # For these reasons, it might be (and often is) absent, and we need to provide the proper media type in case
        # we have operations with a payload.
        media_types = self._get_consumes_for_operation(definition)
        # For `in=body` parameters, we imply `application/json` as the default media type because it is the most common.
        body_media_types = media_types or (OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE,)
        # If an API operation has parameters with `in=formData`, Schemathesis should know how to serialize it.
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

    def get_strategies_from_examples(self, operation: APIOperation) -> List[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        return get_strategies_from_examples(operation, self.examples_field)

    def get_response_schema(self, definition: Dict[str, Any], scope: str) -> Tuple[List[str], Optional[Dict[str, Any]]]:
        scopes, definition = self.resolver.resolve_in_scope(deepcopy(definition), scope)
        schema = definition.get("schema")
        if not schema:
            return scopes, None
        # Extra conversion to JSON Schema is needed here if there was one $ref in the input
        # because it is not converted
        return scopes, to_json_schema_recursive(schema, self.nullable_name)

    def get_content_types(self, operation: APIOperation, response: GenericResponse) -> List[str]:
        produces = operation.definition.raw.get("produces", None)
        if produces:
            return produces
        return self.raw_schema.get("produces", [])

    def _get_parameter_serializer(self, definitions: List[Dict[str, Any]]) -> Optional[Callable]:
        return serialization.serialize_swagger2_parameters(definitions)

    def prepare_multipart(
        self, form_data: FormData, operation: APIOperation
    ) -> Tuple[Optional[List], Optional[Dict[str, Any]]]:
        """Prepare form data for sending with `requests`.

        :param form_data: Raw generated data as a dictionary.
        :param operation: The tested API operation for which the data was generated.
        :return: `files` and `data` values for `requests.request`.
        """
        files, data = [], {}
        # If there is no content types specified for the request or "application/x-www-form-urlencoded" is specified
        # explicitly, then use it., but if "multipart/form-data" is specified, then use it
        content_types = self.get_request_payload_content_types(operation)
        is_multipart = "multipart/form-data" in content_types

        def add_file(file_value: Any) -> None:
            if isinstance(file_value, list):
                for item in file_value:
                    files.append((name, (None, item)))
            else:
                files.append((name, file_value))

        for parameter in operation.definition.parameters:
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

    def get_request_payload_content_types(self, operation: APIOperation) -> List[str]:
        return self._get_consumes_for_operation(operation.definition.resolved)

    def _get_consumes_for_operation(self, definition: Dict[str, Any]) -> List[str]:
        """Get the `consumes` value for the given API operation.

        :param definition: Raw API operation definition.
        :return: A list of media-types for this operation.
        :rtype: List[str]
        """
        global_consumes = self.raw_schema.get("consumes", [])
        consumes = definition.get("consumes", [])
        if not consumes:
            consumes = global_consumes
        return consumes


class OpenApi30(SwaggerV20):  # pylint: disable=too-many-ancestors
    nullable_name = "nullable"
    example_field = "example"
    examples_field = "examples"
    allowed_http_methods = SwaggerV20.allowed_http_methods + ("trace",)
    security = OpenAPISecurityProcessor()
    parameter_cls = OpenAPI30Parameter
    component_locations = ("components",)
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
        self, parameters: Iterable[Dict[str, Any]], definition: Dict[str, Any]
    ) -> List[OpenAPIParameter]:
        # Open API 3.0 has the `requestBody` keyword, which may contain multiple different payload variants.
        collected: List[OpenAPIParameter] = [OpenAPI30Parameter(definition=parameter) for parameter in parameters]
        if "requestBody" in definition:
            required = definition["requestBody"].get("required", False)
            for media_type, content in definition["requestBody"]["content"].items():
                collected.append(OpenAPI30Body(content, media_type=media_type, required=required))
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

    def get_strategies_from_examples(self, operation: APIOperation) -> List[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        return get_strategies_from_examples(operation, self.examples_field)

    def get_content_types(self, operation: APIOperation, response: GenericResponse) -> List[str]:
        definitions = self._get_response_definitions(operation, response)
        if not definitions:
            return []
        return list(definitions.get("content", {}).keys())

    def _get_parameter_serializer(self, definitions: List[Dict[str, Any]]) -> Optional[Callable]:
        return serialization.serialize_openapi3_parameters(definitions)

    def get_request_payload_content_types(self, operation: APIOperation) -> List[str]:
        return list(operation.definition.resolved["requestBody"]["content"].keys())

    def prepare_multipart(
        self, form_data: FormData, operation: APIOperation
    ) -> Tuple[Optional[List], Optional[Dict[str, Any]]]:
        """Prepare form data for sending with `requests`.

        :param form_data: Raw generated data as a dictionary.
        :param operation: The tested API operation for which the data was generated.
        :return: `files` and `data` values for `requests.request`.
        """
        files = []
        content = operation.definition.resolved["requestBody"]["content"]
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
