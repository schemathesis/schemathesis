from __future__ import annotations

import itertools
import string
from collections import defaultdict
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from difflib import get_close_matches
from json import JSONDecodeError
from types import SimpleNamespace
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Generator,
    Iterable,
    Iterator,
    Mapping,
    NoReturn,
    cast,
)
from urllib.parse import urlsplit

import jsonschema
from packaging import version
from requests.exceptions import InvalidHeader
from requests.structures import CaseInsensitiveDict
from requests.utils import check_header_validity

from schemathesis.core import INJECTED_PATH_PARAMETER_KEY, NOT_SET, NotSet, Specification, deserialization, media_types
from schemathesis.core.compat import RefResolutionError
from schemathesis.core.errors import InfiniteRecursiveReference, InvalidSchema, OperationNotFound
from schemathesis.core.failures import Failure, FailureGroup, MalformedJson
from schemathesis.core.jsonschema import BundleError, Bundler
from schemathesis.core.result import Err, Ok, Result
from schemathesis.core.transforms import deepclone
from schemathesis.core.transport import Response
from schemathesis.core.validation import INVALID_HEADER_RE
from schemathesis.generation.case import Case
from schemathesis.generation.meta import CaseMetadata
from schemathesis.openapi.checks import JsonSchemaError, MissingContentType
from schemathesis.specs.openapi import adapter
from schemathesis.specs.openapi.adapter import OpenApiResponses, prepare_parameters
from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter
from schemathesis.specs.openapi.stateful import links
from schemathesis.specs.openapi.types import v3

from ...generation import GenerationMode
from ...hooks import HookContext, HookDispatcher
from ...schemas import APIOperation, APIOperationMap, ApiStatistic, BaseSchema, OperationDefinition
from . import serialization
from ._hypothesis import openapi_cases
from .definitions import OPENAPI_30_VALIDATOR, OPENAPI_31_VALIDATOR, SWAGGER_20_VALIDATOR
from .examples import get_strategies_from_examples
from .parameters import (
    OpenAPI20Body,
    OpenAPI20CompositeBody,
    OpenAPI20Parameter,
    OpenAPI30Body,
    OpenAPI30Parameter,
    OpenAPIParameter,
)
from .references import ConvertingResolver, ReferenceResolver
from .security import BaseSecurityProcessor, OpenAPISecurityProcessor, SwaggerSecurityProcessor
from .stateful import create_state_machine

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

    from schemathesis.auths import AuthStorage
    from schemathesis.generation.stateful import APIStateMachine

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
SCHEMA_ERROR_MESSAGE = "Ensure that the definition complies with the OpenAPI specification"
SCHEMA_PARSING_ERRORS = (KeyError, AttributeError, RefResolutionError, InvalidSchema, InfiniteRecursiveReference)


def check_header(parameter: dict[str, Any]) -> None:
    name = parameter["name"]
    if not name:
        raise InvalidSchema("Header name should not be empty")
    if not name.isascii():
        # `urllib3` encodes header names to ASCII
        raise InvalidSchema(f"Header name should be ASCII: {name}")
    try:
        check_header_validity((name, ""))
    except InvalidHeader as exc:
        raise InvalidSchema(str(exc)) from None
    if bool(INVALID_HEADER_RE.search(name)):
        raise InvalidSchema(f"Invalid header name: {name}")


def get_template_fields(template: str) -> set[str]:
    """Extract named placeholders from a string template."""
    try:
        parameters = {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}
        # Check for malformed params to avoid injecting them - they will be checked later on in the workflow
        template.format(**dict.fromkeys(parameters, ""))
        return parameters
    except (ValueError, IndexError):
        return set()


@dataclass(eq=False, repr=False)
class BaseOpenAPISchema(BaseSchema):
    links_field: ClassVar[str] = ""
    header_required_field: ClassVar[str] = ""
    security: ClassVar[BaseSecurityProcessor] = None  # type: ignore
    component_locations: ClassVar[tuple[tuple[str, ...], ...]] = ()
    _path_parameter_template: ClassVar[dict[str, Any]] = None  # type: ignore
    adapter: SpecificationAdapter = None  # type: ignore

    @property
    def specification(self) -> Specification:
        raise NotImplementedError

    def __repr__(self) -> str:
        info = self.raw_schema["info"]
        return f"<{self.__class__.__name__} for {info['title']} {info['version']}>"

    def __iter__(self) -> Iterator[str]:
        return iter(self.raw_schema.get("paths", {}))

    def _get_operation_map(self, path: str) -> APIOperationMap:
        path_item = self.raw_schema.get("paths", {})[path]
        with in_scope(self.resolver, self.location or ""):
            scope, path_item = self._resolve_path_item(path_item)
        self.dispatch_hook("before_process_path", HookContext(), path, path_item)
        map = APIOperationMap(self, {})
        map._data = MethodMap(map, scope, path, CaseInsensitiveDict(path_item))
        return map

    def find_operation_by_label(self, label: str) -> APIOperation | None:
        method, path = label.split(" ", maxsplit=1)
        return self[path][method]

    def on_missing_operation(self, item: str, exc: KeyError) -> NoReturn:
        matches = get_close_matches(item, list(self))
        self._on_missing_operation(item, exc, matches)

    def _on_missing_operation(self, item: str, exc: KeyError | None, matches: list[str]) -> NoReturn:
        message = f"`{item}` not found"
        if matches:
            message += f". Did you mean `{matches[0]}`?"
        raise OperationNotFound(message=message, item=item) from exc

    def _should_skip(
        self,
        path: str,
        method: str,
        definition: dict[str, Any],
        _ctx_cache: SimpleNamespace = SimpleNamespace(
            operation=APIOperation(
                method="",
                path="",
                label="",
                definition=OperationDefinition(raw=None, scope=""),
                schema=None,  # type: ignore
                responses=None,  # type: ignore
            )
        ),
    ) -> bool:
        if method not in HTTP_METHODS:
            return True
        if self.filter_set.is_empty():
            return False
        # Attribute assignment is way faster than creating a new namespace every time
        operation = _ctx_cache.operation
        operation.method = method
        operation.path = path
        operation.label = f"{method.upper()} {path}"
        operation.definition.raw = definition
        operation.schema = self
        return not self.filter_set.match(_ctx_cache)

    def _measure_statistic(self) -> ApiStatistic:
        statistic = ApiStatistic()
        try:
            paths = self.raw_schema["paths"]
        except KeyError:
            return statistic

        resolve = self.resolver.resolve
        resolve_path_item = self._resolve_path_item
        should_skip = self._should_skip
        links_field = self.links_field

        # For operationId lookup
        selected_operations_by_id: set[str] = set()
        # Tuples of (method, path)
        selected_operations_by_path: set[tuple[str, str]] = set()
        collected_links: list[dict] = []

        for path, path_item in paths.items():
            try:
                scope, path_item = resolve_path_item(path_item)
                self.resolver.push_scope(scope)
                try:
                    for method, definition in path_item.items():
                        if method not in HTTP_METHODS or not definition:
                            continue
                        statistic.operations.total += 1
                        is_selected = not should_skip(path, method, definition)
                        if is_selected:
                            statistic.operations.selected += 1
                            # Store both identifiers
                            if "operationId" in definition:
                                selected_operations_by_id.add(definition["operationId"])
                            selected_operations_by_path.add((method, path))
                        for response in definition.get("responses", {}).values():
                            if "$ref" in response:
                                _, response = resolve(response["$ref"])
                            defined_links = response.get(links_field)
                            if defined_links is not None:
                                statistic.links.total += len(defined_links)
                                if is_selected:
                                    collected_links.extend(defined_links.values())
                finally:
                    self.resolver.pop_scope()
            except SCHEMA_PARSING_ERRORS:
                continue

        def is_link_selected(link: dict) -> bool:
            if "$ref" in link:
                _, link = resolve(link["$ref"])

            if "operationId" in link:
                return link["operationId"] in selected_operations_by_id
            else:
                try:
                    scope, _ = resolve(link["operationRef"])
                    path, method = scope.rsplit("/", maxsplit=2)[-2:]
                    path = path.replace("~1", "/").replace("~0", "~")
                    return (method, path) in selected_operations_by_path
                except Exception:
                    return False

        for link in collected_links:
            if is_link_selected(link):
                statistic.links.selected += 1

        return statistic

    def _operation_iter(self) -> Iterator[tuple[str, str, dict[str, Any]]]:
        try:
            paths = self.raw_schema["paths"]
        except KeyError:
            return
        resolve = self.resolver.resolve
        should_skip = self._should_skip
        for path, path_item in paths.items():
            try:
                if "$ref" in path_item:
                    _, path_item = resolve(path_item["$ref"])
                for method, definition in path_item.items():
                    if should_skip(path, method, definition):
                        continue
                    yield (method, path, definition)
            except SCHEMA_PARSING_ERRORS:
                continue

    def _collect_operation_parameters(
        self, path_item: Mapping[str, Any], operation: dict[str, Any]
    ) -> list[OpenAPIParameter]:
        bundler = Bundler()
        shared_parameters = list(prepare_parameters(path_item, resolver=self.resolver, bundler=bundler))
        parameters = list(prepare_parameters(operation, resolver=self.resolver, bundler=bundler))
        return self.collect_parameters(itertools.chain(parameters, shared_parameters), operation)

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
        __tracebackhide__ = True
        try:
            paths = self.raw_schema["paths"]
        except KeyError as exc:
            # This field is optional in Open API 3.1
            if version.parse(self.specification.version) >= version.parse("3.1"):
                return
            # Missing `paths` is not recoverable
            self._raise_invalid_schema(exc)

        context = HookContext()
        # Optimization: local variables are faster than attribute access
        dispatch_hook = self.dispatch_hook
        resolve_path_item = self._resolve_path_item
        should_skip = self._should_skip
        collect_parameters = self.collect_parameters
        make_operation = self.make_operation
        for path, path_item in paths.items():
            method = None
            try:
                dispatch_hook("before_process_path", context, path, path_item)
                scope, path_item = resolve_path_item(path_item)
                with in_scope(self.resolver, scope):
                    bundler = Bundler()
                    shared_parameters = list(prepare_parameters(path_item, resolver=self.resolver, bundler=bundler))
                    for method, entry in path_item.items():
                        if method not in HTTP_METHODS:
                            continue
                        try:
                            if should_skip(path, method, entry):
                                continue
                            raw_parameters = list(prepare_parameters(entry, resolver=self.resolver, bundler=bundler))
                            parameters = collect_parameters(itertools.chain(raw_parameters, shared_parameters), entry)
                            operation = make_operation(
                                path,
                                method,
                                parameters,
                                entry,
                                scope,
                            )
                            yield Ok(operation)
                        except SCHEMA_PARSING_ERRORS as exc:
                            yield self._into_err(exc, path, method)
            except SCHEMA_PARSING_ERRORS as exc:
                yield self._into_err(exc, path, method)

    def _into_err(self, error: Exception, path: str | None, method: str | None) -> Err[InvalidSchema]:
        __tracebackhide__ = True
        try:
            self._raise_invalid_schema(error, path, method)
        except InvalidSchema as exc:
            return Err(exc)

    def _raise_invalid_schema(
        self,
        error: Exception,
        path: str | None = None,
        method: str | None = None,
    ) -> NoReturn:
        __tracebackhide__ = True
        if isinstance(error, InfiniteRecursiveReference):
            raise InvalidSchema(str(error), path=path, method=method) from None
        if isinstance(error, RefResolutionError):
            raise InvalidSchema.from_reference_resolution_error(error, path=path, method=method) from None
        try:
            self.validate()
        except jsonschema.ValidationError as exc:
            raise InvalidSchema.from_jsonschema_error(
                exc, path=path, method=method, config=self.config.output
            ) from None
        raise InvalidSchema(SCHEMA_ERROR_MESSAGE, path=path, method=method) from error

    def validate(self) -> None:
        with suppress(TypeError):
            self._validate()

    def _validate(self) -> None:
        raise NotImplementedError

    def collect_parameters(
        self, parameters: Iterable[dict[str, Any]], definition: dict[str, Any]
    ) -> list[OpenAPIParameter]:
        """Collect Open API parameters.

        They should be used uniformly during the generation step; therefore, we need to convert them into
        a spec-independent list of parameters.
        """
        raise NotImplementedError

    def _parse_responses(self, definition: dict[str, Any], scope: str) -> OpenApiResponses:
        responses = definition.get("responses", {})
        return OpenApiResponses.from_definition(
            definition=responses, resolver=self.resolver, scope=scope, adapter=self.adapter
        )

    def _resolve_path_item(self, methods: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        # The path item could be behind a reference
        # In this case, we need to resolve it to get the proper scope for reference inside the item.
        # It is mostly for validating responses.
        if "$ref" in methods:
            return self.resolver.resolve(methods["$ref"])
        return self.resolver.resolution_scope, methods

    def make_operation(
        self,
        path: str,
        method: str,
        parameters: list[OpenAPIParameter],
        definition: dict[str, Any],
        scope: str,
    ) -> APIOperation:
        __tracebackhide__ = True
        base_url = self.get_base_url()
        responses = self._parse_responses(definition, scope)
        operation: APIOperation[OpenAPIParameter, OpenApiResponses] = APIOperation(
            path=path,
            method=method,
            definition=OperationDefinition(definition, scope),
            base_url=base_url,
            app=self.app,
            schema=self,
            responses=responses,
        )
        for parameter in parameters:
            operation.add_parameter(parameter)
        # Inject unconstrained path parameters if any is missing
        missing_parameter_names = get_template_fields(operation.path) - {
            parameter.name for parameter in operation.path_parameters
        }
        for name in missing_parameter_names:
            definition = {"name": name, INJECTED_PATH_PARAMETER_KEY: True, **deepclone(self._path_parameter_template)}
            for parameter in self.collect_parameters([definition], definition):
                operation.add_parameter(parameter)
        config = self.config.generation_for(operation=operation)
        if config.with_security_parameters:
            self.security.process_definitions(self.raw_schema, operation, self.resolver)
        self.dispatch_hook("before_init_operation", HookContext(operation=operation), operation)
        return operation

    @property
    def resolver(self) -> ReferenceResolver:
        if not hasattr(self, "_resolver"):
            self._resolver = ReferenceResolver(self.location or "", self.raw_schema)
        return self._resolver

    def get_content_types(self, operation: APIOperation, response: Response) -> list[str]:
        """Content types available for this API operation."""
        raise NotImplementedError

    def get_strategies_from_examples(self, operation: APIOperation, **kwargs: Any) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        raise NotImplementedError

    def get_security_requirements(self, operation: APIOperation) -> list[str]:
        """Get applied security requirements for the given API operation."""
        return self.security.get_security_requirements(self.raw_schema, operation)

    def get_operation_by_id(self, operation_id: str) -> APIOperation:
        """Get an `APIOperation` instance by its `operationId`."""
        resolve = self.resolver.resolve
        default_scope = self.resolver.resolution_scope
        for path, path_item in self.raw_schema.get("paths", {}).items():
            # If the path is behind a reference we have to keep the scope
            # The scope is used to resolve nested components later on
            if "$ref" in path_item:
                scope, path_item = resolve(path_item["$ref"])
            else:
                scope = default_scope
            for method, operation in path_item.items():
                if method not in HTTP_METHODS:
                    continue
                if "operationId" in operation and operation["operationId"] == operation_id:
                    parameters = self._collect_operation_parameters(path_item, operation)
                    return self.make_operation(path, method, parameters, operation, scope)
        self._on_missing_operation(operation_id, None, [])

    def get_operation_by_reference(self, reference: str) -> APIOperation:
        """Get local or external `APIOperation` instance by reference.

        Reference example: #/paths/~1users~1{user_id}/patch
        """
        scope, operation = self.resolver.resolve(reference)
        path, method = scope.rsplit("/", maxsplit=2)[-2:]
        path = path.replace("~1", "/").replace("~0", "~")
        parent_ref, _ = reference.rsplit("/", maxsplit=1)
        _, path_item = self.resolver.resolve(parent_ref)
        with in_scope(self.resolver, scope):
            parameters = self._collect_operation_parameters(path_item, operation)
        return self.make_operation(path, method, parameters, operation, scope)

    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        generation_mode: GenerationMode = GenerationMode.POSITIVE,
        **kwargs: Any,
    ) -> SearchStrategy:
        return openapi_cases(
            operation=operation,
            hooks=hooks,
            auth_storage=auth_storage,
            generation_mode=generation_mode,
            **kwargs,
        )

    def get_parameter_serializer(self, operation: APIOperation, location: str) -> Callable | None:
        definitions = [item.definition for item in operation.iter_parameters() if item.location == location]
        config = self.config.generation_for(operation=operation)
        if config.with_security_parameters:
            security_parameters = self.security.get_security_definitions_as_parameters(
                self.raw_schema, operation, self.resolver, location
            )
            security_parameters = [item for item in security_parameters if item["in"] == location]
            if security_parameters:
                definitions.extend(security_parameters)
        if definitions:
            return self._get_parameter_serializer(definitions)
        return None

    def _get_parameter_serializer(self, definitions: list[dict[str, Any]]) -> Callable | None:
        raise NotImplementedError

    def get_headers(
        self, operation: APIOperation, response: Response
    ) -> tuple[list[str], dict[str, dict[str, Any]] | None] | None:
        definition = operation.responses.find_by_status_code(response.status_code)
        if definition is None:
            return None
        # TODO: It should be proper scopes / resolve it eagerly
        return [], definition.definition.get("headers")

    def as_state_machine(self) -> type[APIStateMachine]:
        return create_state_machine(self)

    def get_links(self, operation: APIOperation) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = defaultdict(dict)
        for status_code, link in links.get_all_links(operation):
            if isinstance(link, Ok):
                name = link.ok().name
            else:
                name = link.err().name
            result[status_code][name] = link

        return result

    def get_tags(self, operation: APIOperation) -> list[str] | None:
        return operation.definition.raw.get("tags")

    def validate_response(self, operation: APIOperation, response: Response) -> bool | None:
        __tracebackhide__ = True
        definition = operation.responses.find_by_status_code(response.status_code)
        if definition is None or definition.schema is None:
            # No definition for the given HTTP response, or missing "schema" in the matching definition
            return None

        failures: list[Failure] = []

        content_types = response.headers.get("content-type")
        if content_types is None:
            all_media_types = self.get_content_types(operation, response)
            formatted_content_types = [f"\n- `{content_type}`" for content_type in all_media_types]
            message = f"The following media types are documented in the schema:{''.join(formatted_content_types)}"
            failures.append(MissingContentType(operation=operation.label, message=message, media_types=all_media_types))
            # Default content type
            content_type = "application/json"
        else:
            content_type = content_types[0]

        try:
            data = deserialization.deserialize_response(response, content_type)
        except JSONDecodeError as exc:
            failures.append(MalformedJson.from_exception(operation=operation.label, exc=exc))
            _maybe_raise_one_or_more(failures)
            return None
        except NotImplementedError:
            # If the content type is not supported, we cannot validate it
            _maybe_raise_one_or_more(failures)
            return None
        except Exception as exc:
            failures.append(
                Failure(
                    operation=operation.label,
                    title="Content deserialization error",
                    message=f"Failed to deserialize response content:\n\n  {exc}",
                )
            )
            _maybe_raise_one_or_more(failures)
            return None

        try:
            definition.validator.validate(data)
        except jsonschema.SchemaError as exc:
            raise InvalidSchema.from_jsonschema_error(
                exc, path=operation.path, method=operation.method, config=self.config.output
            ) from exc
        except jsonschema.ValidationError as exc:
            failures.append(
                JsonSchemaError.from_exception(
                    operation=operation.label,
                    exc=exc,
                    config=operation.schema.config.output,
                )
            )
        _maybe_raise_one_or_more(failures)
        return None  # explicitly return None for mypy

    @contextmanager
    def _validating_response(self, scopes: list[str]) -> Generator[ConvertingResolver, None, None]:
        resolver = ConvertingResolver(
            self.location or "", self.raw_schema, nullable_keyword=self.adapter.nullable_keyword
        )
        with in_scopes(resolver, scopes):
            yield resolver


def _maybe_raise_one_or_more(failures: list[Failure]) -> None:
    if not failures:
        return
    if len(failures) == 1:
        raise failures[0] from None
    raise FailureGroup(failures) from None


@contextmanager
def in_scope(resolver: jsonschema.RefResolver, scope: str) -> Generator[None, None, None]:
    resolver.push_scope(scope)
    try:
        yield
    finally:
        resolver.pop_scope()


@contextmanager
def in_scopes(resolver: jsonschema.RefResolver, scopes: list[str]) -> Generator[None, None, None]:
    """Push all available scopes into the resolver."""
    with ExitStack() as stack:
        for scope in scopes:
            stack.enter_context(in_scope(resolver, scope))
        yield


@dataclass
class MethodMap(Mapping):
    """Container for accessing API operations.

    Provides a more specific error message if API operation is not found.
    """

    _parent: APIOperationMap
    # Reference resolution scope
    _scope: str
    # Methods are stored for this path
    _path: str
    # Storage for definitions
    _path_item: CaseInsensitiveDict

    __slots__ = ("_parent", "_scope", "_path", "_path_item")

    def __len__(self) -> int:
        return len(self._path_item)

    def __iter__(self) -> Iterator[str]:
        return iter(self._path_item)

    def _init_operation(self, method: str) -> APIOperation:
        method = method.lower()
        operation = self._path_item[method]
        schema = cast(BaseOpenAPISchema, self._parent._schema)
        path = self._path
        scope = self._scope
        with in_scope(schema.resolver, scope):
            try:
                parameters = schema._collect_operation_parameters(self._path_item, operation)
            except SCHEMA_PARSING_ERRORS as exc:
                schema._raise_invalid_schema(exc, path, method)
        return schema.make_operation(path, method, parameters, operation, scope)

    def __getitem__(self, item: str) -> APIOperation:
        try:
            return self._init_operation(item)
        except LookupError as exc:
            available_methods = ", ".join(key.upper() for key in self if key in HTTP_METHODS)
            message = f"Method `{item.upper()}` not found."
            if available_methods:
                message += f" Available methods: {available_methods}"
            raise LookupError(message) from exc


OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE = "application/json"
OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE = "multipart/form-data"


class SwaggerV20(BaseOpenAPISchema):
    example_field = "x-example"
    examples_field = "x-examples"
    header_required_field = "x-required"
    security = SwaggerSecurityProcessor()
    component_locations: ClassVar[tuple[tuple[str, ...], ...]] = (("definitions",),)
    links_field = "x-links"
    _path_parameter_template = {"in": "path", "required": True, "type": "string"}

    def __post_init__(self) -> None:
        self.adapter = adapter.v2
        super().__post_init__()

    @property
    def specification(self) -> Specification:
        version = self.raw_schema.get("swagger", "2.0")
        return Specification.openapi(version=version)

    def _validate(self) -> None:
        SWAGGER_20_VALIDATOR.validate(self.raw_schema)

    def _get_base_path(self) -> str:
        return self.raw_schema.get("basePath", "/")

    def collect_parameters(
        self, parameters: Iterable[dict[str, Any]], definition: dict[str, Any]
    ) -> list[OpenAPIParameter]:
        # The main difference with Open API 3.0 is that it has `body` and `form` parameters that we need to handle
        # differently.
        collected: list[OpenAPIParameter] = []
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
                # Take the original definition & extract the resource_name from there
                resource_name = None
                for param in definition["parameters"]:
                    if "$ref" in param:
                        _, param = self.resolver.resolve(param["$ref"])
                    if param.get("in") == "body":
                        if "$ref" in param["schema"]:
                            resource_name = _get_resource_name(param["schema"]["$ref"])
                # TODO: It is a corner case, but body could come from shared parameters. Fix it later
                for media_type in body_media_types:
                    collected.append(
                        OpenAPI20Body(definition=parameter, media_type=media_type, resource_name=resource_name)
                    )
            else:
                if parameter["in"] in ("header", "cookie"):
                    check_header(parameter)
                collected.append(OpenAPI20Parameter(definition=parameter))

        if form_parameters:
            for media_type in form_data_media_types:
                collected.append(
                    # Individual `formData` parameters are joined into a single "composite" one.
                    OpenAPI20CompositeBody.from_parameters(*form_parameters, media_type=media_type)
                )
        return collected

    def get_strategies_from_examples(self, operation: APIOperation, **kwargs: Any) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        return get_strategies_from_examples(operation, **kwargs)

    def get_content_types(self, operation: APIOperation, response: Response) -> list[str]:
        produces = operation.definition.raw.get("produces", None)
        if produces:
            return produces
        return self.raw_schema.get("produces", [])

    def _get_parameter_serializer(self, definitions: list[dict[str, Any]]) -> Callable | None:
        return serialization.serialize_swagger2_parameters(definitions)

    def prepare_multipart(
        self, form_data: dict[str, Any], operation: APIOperation
    ) -> tuple[list | None, dict[str, Any] | None]:
        files, data = [], {}
        # If there is no content types specified for the request or "application/x-www-form-urlencoded" is specified
        # explicitly, then use it., but if "multipart/form-data" is specified, then use it
        content_types = self.get_request_payload_content_types(operation)
        is_multipart = "multipart/form-data" in content_types

        known_fields: dict[str, dict] = {}

        for parameter in operation.body:
            if isinstance(parameter, OpenAPI20CompositeBody):
                for form_parameter in parameter.definition:
                    known_fields[form_parameter.name] = form_parameter.definition

        def add_file(name: str, value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    files.append((name, (None, item)))
            else:
                files.append((name, value))

        for name, value in form_data.items():
            param_def = known_fields.get(name)
            if param_def:
                if param_def.get("type") == "file" or is_multipart:
                    add_file(name, value)
                else:
                    data[name] = value
            else:
                # Unknown field — treat it as a file (safe default under multipart/form-data)
                add_file(name, value)
        # `None` is the default value for `files` and `data` arguments in `requests.request`
        return files or None, data or None

    def get_request_payload_content_types(self, operation: APIOperation) -> list[str]:
        return self._get_consumes_for_operation(operation.definition.raw)

    def make_case(
        self,
        *,
        operation: APIOperation,
        method: str | None = None,
        path: str | None = None,
        path_parameters: dict[str, Any] | None = None,
        headers: dict[str, Any] | CaseInsensitiveDict | None = None,
        cookies: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: list | dict[str, Any] | str | int | float | bool | bytes | NotSet = NOT_SET,
        media_type: str | None = None,
        meta: CaseMetadata | None = None,
    ) -> Case:
        if body is not NOT_SET and media_type is None:
            media_type = operation._get_default_media_type()
        return Case(
            operation=operation,
            method=method or operation.method.upper(),
            path=path or operation.path,
            path_parameters=path_parameters or {},
            headers=CaseInsensitiveDict() if headers is None else CaseInsensitiveDict(headers),
            cookies=cookies or {},
            query=query or {},
            body=body,
            media_type=media_type,
            meta=meta,
        )

    def _get_consumes_for_operation(self, definition: dict[str, Any]) -> list[str]:
        global_consumes = self.raw_schema.get("consumes", [])
        consumes = definition.get("consumes", [])
        if not consumes:
            consumes = global_consumes
        return consumes


class OpenApi30(SwaggerV20):
    example_field = "example"
    examples_field = "examples"
    header_required_field = "required"
    security = OpenAPISecurityProcessor()
    component_locations = (("components", "schemas"),)
    links_field = "links"
    _path_parameter_template = {"in": "path", "required": True, "schema": {"type": "string"}}

    def __post_init__(self) -> None:
        if self.specification.version.startswith("3.1"):
            self.adapter = adapter.v3_0
        else:
            self.adapter = adapter.v3_1
        BaseOpenAPISchema.__post_init__(self)

    @property
    def specification(self) -> Specification:
        version = self.raw_schema["openapi"]
        return Specification.openapi(version=version)

    def _validate(self) -> None:
        if self.specification.version.startswith("3.1"):
            # Currently we treat Open API 3.1 as 3.0 in some regard
            OPENAPI_31_VALIDATOR.validate(self.raw_schema)
        else:
            OPENAPI_30_VALIDATOR.validate(self.raw_schema)

    def _get_base_path(self) -> str:
        servers = self.raw_schema.get("servers", [])
        if servers:
            # assume we're the first server in list
            server = servers[0]
            url = server["url"].format(**{k: v["default"] for k, v in server.get("variables", {}).items()})
            return urlsplit(url).path
        return "/"

    def collect_parameters(
        self, parameters: Iterable[dict[str, Any]], definition: dict[str, Any]
    ) -> list[OpenAPIParameter]:
        # Open API 3.0 has the `requestBody` keyword, which may contain multiple different payload variants.
        collected: list[OpenAPIParameter] = []
        operation = cast(v3.Operation, definition)

        for parameter in parameters:
            if parameter["in"] in ("header", "cookie"):
                check_header(parameter)
            collected.append(OpenAPI30Parameter(definition=parameter))

        request_body_or_ref = operation.get("requestBody")
        if request_body_or_ref is not None:
            reference = request_body_or_ref.get("$ref")
            # TODO: Use scopes here
            if isinstance(reference, str):
                _, resolved = self.resolver.resolve(reference)
                request_body_or_ref = cast(v3.RequestBodyOrRef, resolved)
            else:
                request_body_or_ref = cast(v3.RequestBodyOrRef, request_body_or_ref)

            # It could be an object inside `requestBodies`, which could be a reference itself
            reference = request_body_or_ref.get("$ref")
            # TODO: Use scopes here
            if isinstance(reference, str):
                _, resolved = self.resolver.resolve(reference)
                request_body = cast(v3.RequestBody, resolved)
            else:
                request_body = cast(v3.RequestBody, request_body_or_ref)

            required = request_body.get("required", False)
            for media_type, content in request_body["content"].items():
                resource_name = None
                schema = content.get("schema")
                if isinstance(schema, dict):
                    content = cast(v3.MediaType, dict(content))
                    if "$ref" in schema:
                        resource_name = _get_resource_name(schema["$ref"])
                    try:
                        to_bundle = cast(dict[str, Any], schema)
                        bundled = Bundler().bundle(to_bundle, self.resolver, inline_recursive=True)
                        content["schema"] = cast(v3.Schema, bundled)
                    except BundleError as exc:
                        raise InvalidSchema.from_bundle_error(exc, "body") from exc
                collected.append(
                    OpenAPI30Body(content, media_type=media_type, required=required, resource_name=resource_name)
                )
        return collected

    def get_strategies_from_examples(self, operation: APIOperation, **kwargs: Any) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        return get_strategies_from_examples(operation, **kwargs)

    def get_content_types(self, operation: APIOperation, response: Response) -> list[str]:
        definition = operation.responses.find_by_status_code(response.status_code)
        if definition is None:
            return []
        return list(definition.definition.get("content", {}).keys())

    def _get_parameter_serializer(self, definitions: list[dict[str, Any]]) -> Callable | None:
        return serialization.serialize_openapi3_parameters(definitions)

    def get_request_payload_content_types(self, operation: APIOperation) -> list[str]:
        return [body.media_type for body in operation.body]

    def prepare_multipart(
        self, form_data: dict[str, Any], operation: APIOperation
    ) -> tuple[list | None, dict[str, Any] | None]:
        files = []
        # Open API 3.0 requires media types to be present. We can get here only if the schema defines
        # the "multipart/form-data" media type, or any other more general media type that matches it (like `*/*`)
        schema = {}
        for body in operation.body:
            main, sub = media_types.parse(body.media_type)
            if main in ("*", "multipart") and sub in ("*", "form-data", "mixed"):
                schema = body.definition.get("schema")
                break
        for name, value in form_data.items():
            property_schema = (schema or {}).get("properties", {}).get(name)
            if property_schema:
                if isinstance(value, list):
                    files.extend([(name, item) for item in value])
                elif property_schema.get("format") in ("binary", "base64"):
                    files.append((name, value))
                else:
                    files.append((name, (None, value)))
            elif isinstance(value, list):
                files.extend([(name, item) for item in value])
            else:
                files.append((name, (None, value)))
        # `None` is the default value for `files` and `data` arguments in `requests.request`
        return files or None, None


def _get_resource_name(reference: str) -> str:
    return reference.rsplit("/", maxsplit=1)[1]
