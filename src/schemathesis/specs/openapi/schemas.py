from __future__ import annotations

import string
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from difflib import get_close_matches
from functools import cached_property, lru_cache
from json import JSONDecodeError
from types import SimpleNamespace
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Generator,
    Iterator,
    Mapping,
    NoReturn,
    Sequence,
    cast,
)
from urllib.parse import urlsplit

import jsonschema
from packaging import version
from requests.structures import CaseInsensitiveDict

from schemathesis.core import INJECTED_PATH_PARAMETER_KEY, NOT_SET, NotSet, Specification, deserialization, media_types
from schemathesis.core.adapter import OperationParameter, ResponsesContainer
from schemathesis.core.compat import RefResolutionError
from schemathesis.core.errors import InfiniteRecursiveReference, InvalidSchema, OperationNotFound
from schemathesis.core.failures import Failure, FailureGroup, MalformedJson
from schemathesis.core.result import Err, Ok, Result
from schemathesis.core.transport import Response
from schemathesis.generation.case import Case
from schemathesis.generation.meta import CaseMetadata
from schemathesis.openapi.checks import JsonSchemaError, MissingContentType
from schemathesis.specs.openapi import adapter
from schemathesis.specs.openapi.adapter import OpenApiResponses
from schemathesis.specs.openapi.adapter.parameters import (
    COMBINED_FORM_DATA_MARKER,
    OpenApiParameter,
    OpenApiParameterSet,
)
from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter
from schemathesis.specs.openapi.adapter.security import OpenApiSecurityParameters

from ...generation import GenerationMode
from ...hooks import HookContext, HookDispatcher
from ...schemas import APIOperation, APIOperationMap, ApiStatistic, BaseSchema, OperationDefinition
from . import serialization
from ._hypothesis import openapi_cases
from .definitions import OPENAPI_30_VALIDATOR, OPENAPI_31_VALIDATOR, SWAGGER_20_VALIDATOR
from .examples import get_strategies_from_examples
from .references import ReferenceResolver
from .stateful import create_state_machine

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

    from schemathesis.auths import AuthStorage
    from schemathesis.generation.stateful import APIStateMachine

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
SCHEMA_ERROR_MESSAGE = "Ensure that the definition complies with the OpenAPI specification"
SCHEMA_PARSING_ERRORS = (KeyError, AttributeError, RefResolutionError, InvalidSchema, InfiniteRecursiveReference)


@lru_cache()
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
    adapter: SpecificationAdapter = None  # type: ignore

    @property
    def specification(self) -> Specification:
        raise NotImplementedError

    def __repr__(self) -> str:
        info = self.raw_schema["info"]
        return f"<{self.__class__.__name__} for {info['title']} {info['version']}>"

    def __iter__(self) -> Iterator[str]:
        return iter(self.raw_schema.get("paths", {}))

    @cached_property
    def default_media_types(self) -> list[str]:
        raise NotImplementedError

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
                definition=OperationDefinition(raw=None),
                schema=None,  # type: ignore
                responses=None,  # type: ignore
                security=None,  # type: ignore
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
        links_keyword = self.adapter.links_keyword

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
                            defined_links = response.get(links_keyword)
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
        iter_parameters = self._iter_parameters
        make_operation = self.make_operation
        for path, path_item in paths.items():
            method = None
            try:
                dispatch_hook("before_process_path", context, path, path_item)
                scope, path_item = resolve_path_item(path_item)
                with in_scope(self.resolver, scope):
                    shared_parameters = path_item.get("parameters", [])
                    for method, entry in path_item.items():
                        if method not in HTTP_METHODS:
                            continue
                        try:
                            if should_skip(path, method, entry):
                                continue
                            parameters = iter_parameters(entry, shared_parameters)
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

    def _iter_parameters(
        self, definition: dict[str, Any], shared_parameters: Sequence[dict[str, Any]]
    ) -> list[OperationParameter]:
        return list(
            self.adapter.iter_parameters(
                definition, shared_parameters, self.default_media_types, self.resolver, self.adapter
            )
        )

    def _parse_responses(self, definition: dict[str, Any], scope: str) -> OpenApiResponses:
        responses = definition.get("responses", {})
        return OpenApiResponses.from_definition(
            definition=responses, resolver=self.resolver, scope=scope, adapter=self.adapter
        )

    def _parse_security(self, definition: dict[str, Any]) -> OpenApiSecurityParameters:
        return OpenApiSecurityParameters.from_definition(
            schema=self.raw_schema,
            operation=definition,
            resolver=self.resolver,
            adapter=self.adapter,
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
        parameters: list[OperationParameter],
        definition: dict[str, Any],
        scope: str,
    ) -> APIOperation:
        __tracebackhide__ = True
        base_url = self.get_base_url()
        responses = self._parse_responses(definition, scope)
        security = self._parse_security(definition)
        operation: APIOperation[OperationParameter, ResponsesContainer, OpenApiSecurityParameters] = APIOperation(
            path=path,
            method=method,
            definition=OperationDefinition(definition),
            base_url=base_url,
            app=self.app,
            schema=self,
            responses=responses,
            security=security,
            path_parameters=OpenApiParameterSet(),
            query=OpenApiParameterSet(),
            headers=OpenApiParameterSet(),
            cookies=OpenApiParameterSet(),
        )
        for parameter in parameters:
            operation.add_parameter(parameter)
        # Inject unconstrained path parameters if any is missing
        missing_parameter_names = get_template_fields(operation.path) - {
            parameter.name for parameter in operation.path_parameters
        }
        for name in missing_parameter_names:
            operation.add_parameter(
                self.adapter.build_path_parameter({"name": name, INJECTED_PATH_PARAMETER_KEY: True})
            )
        config = self.config.generation_for(operation=operation)
        if config.with_security_parameters:
            for param in operation.security.iter_parameters():
                param_name = param.get("name")
                param_location = param.get("in")
                if (
                    param_name is not None
                    and param_location is not None
                    and operation.get_parameter(name=param_name, location=param_location) is not None
                ):
                    continue
                operation.add_parameter(OpenApiParameter.from_definition(definition=param, adapter=self.adapter))
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
                    parameters = self._iter_parameters(operation, path_item.get("parameters", []))
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
            parameters = self._iter_parameters(operation, path_item.get("parameters", []))
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
            security_parameters = [param for param in operation.security.iter_parameters() if param["in"] == location]
            if security_parameters:
                definitions.extend(security_parameters)
        if definitions:
            return self._get_parameter_serializer(definitions)
        return None

    def _get_parameter_serializer(self, definitions: list[dict[str, Any]]) -> Callable | None:
        raise NotImplementedError

    def as_state_machine(self) -> type[APIStateMachine]:
        return create_state_machine(self)

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
                parameters = schema._iter_parameters(operation, self._path_item.get("parameters", []))
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


class SwaggerV20(BaseOpenAPISchema):
    def __post_init__(self) -> None:
        self.adapter = adapter.v2
        super().__post_init__()

    @property
    def specification(self) -> Specification:
        version = self.raw_schema.get("swagger", "2.0")
        return Specification.openapi(version=version)

    @cached_property
    def default_media_types(self) -> list[str]:
        return self.raw_schema.get("consumes", [])

    def _validate(self) -> None:
        SWAGGER_20_VALIDATOR.validate(self.raw_schema)

    def _get_base_path(self) -> str:
        return self.raw_schema.get("basePath", "/")

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
            if COMBINED_FORM_DATA_MARKER in parameter.definition:
                known_fields.update(parameter.definition["schema"].get("properties", {}))

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
    def __post_init__(self) -> None:
        if self.specification.version.startswith("3.1"):
            self.adapter = adapter.v3_1
        else:
            self.adapter = adapter.v3_0
        BaseOpenAPISchema.__post_init__(self)

    @property
    def specification(self) -> Specification:
        version = self.raw_schema["openapi"]
        return Specification.openapi(version=version)

    @cached_property
    def default_media_types(self) -> list[str]:
        return []

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
