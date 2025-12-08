from __future__ import annotations

import string
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from difflib import get_close_matches
from functools import cached_property, lru_cache
from json import JSONDecodeError
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NoReturn, cast

import jsonschema
from packaging import version
from requests.structures import CaseInsensitiveDict

from schemathesis.core import INJECTED_PATH_PARAMETER_KEY, NOT_SET, NotSet, Specification, deserialization
from schemathesis.core.adapter import OperationParameter, ResponsesContainer
from schemathesis.core.compat import RefResolutionError
from schemathesis.core.errors import (
    SCHEMA_ERROR_SUGGESTION,
    InfiniteRecursiveReference,
    InvalidSchema,
    OperationNotFound,
    SchemaLocation,
)
from schemathesis.core.failures import Failure, FailureGroup, MalformedJson
from schemathesis.core.jsonschema import Bundler
from schemathesis.core.jsonschema.bundler import BundleCache
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.result import Err, Ok, Result
from schemathesis.core.transport import Response
from schemathesis.generation.case import Case
from schemathesis.generation.meta import CaseMetadata
from schemathesis.openapi.checks import JsonSchemaError, MissingContentType
from schemathesis.resources import ExtraDataSource
from schemathesis.specs.openapi import adapter
from schemathesis.specs.openapi.adapter import OpenApiResponses
from schemathesis.specs.openapi.adapter.parameters import OpenApiParameter, OpenApiParameterSet
from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter
from schemathesis.specs.openapi.adapter.security import OpenApiSecurity, OpenApiSecurityParameters
from schemathesis.specs.openapi.analysis import OpenAPIAnalysis

from ...generation import GenerationMode
from ...hooks import HookContext, HookDispatcher
from ...schemas import APIOperation, APIOperationMap, ApiStatistic, BaseSchema, OperationDefinition
from ._hypothesis import openapi_cases
from ._operation_lookup import OperationLookup
from .examples import get_strategies_from_examples
from .references import ReferenceResolver
from .stateful import create_state_machine

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

    from schemathesis.auths import AuthContext, AuthStorage
    from schemathesis.generation.stateful import APIStateMachine

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
SCHEMA_PARSING_ERRORS = (KeyError, AttributeError, RefResolutionError, InvalidSchema, InfiniteRecursiveReference)


@lru_cache
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
class OpenApiSchema(BaseSchema):
    adapter: SpecificationAdapter = None  # type: ignore[assignment]
    _spec_version: str = field(init=False)

    def __post_init__(self) -> None:
        self._initialize_adapter()
        super().__post_init__()
        self.analysis = OpenAPIAnalysis(self)
        self._bundler = Bundler()
        self._bundle_cache: BundleCache = {}
        self._operation_lookup = OperationLookup(self, HTTP_METHODS)

    def _initialize_adapter(self) -> None:
        swagger_version = self.raw_schema.get("swagger")
        if swagger_version is not None:
            self._spec_version = swagger_version or "2.0"
            self.adapter = adapter.v2
            return

        openapi_version = self.raw_schema.get("openapi")
        if openapi_version is not None:
            self._spec_version = openapi_version
            if openapi_version.startswith("3.1"):
                self.adapter = adapter.v3_1
            else:
                self.adapter = adapter.v3_0
            return

        raise InvalidSchema("Unable to determine Open API version for this schema.")

    @cached_property
    def specification(self) -> Specification:
        return Specification.openapi(version=self._spec_version)

    @cached_property
    def security(self) -> OpenApiSecurity:
        return OpenApiSecurity(raw_schema=self.raw_schema, adapter=self.adapter, resolver=self.resolver)

    def apply_auth(self, case: Case, context: AuthContext) -> bool:
        """Apply OpenAPI-aware authentication to a test case.

        Returns True if authentication was applied, False otherwise.
        """
        configured_schemes = self.config.auth.openapi.schemes
        if not configured_schemes:
            return False
        return self.security.apply_auth(case, context, configured_schemes)

    def create_extra_data_source(self) -> ExtraDataSource | None:
        """Create an extra data source for augmenting test generation with real data.

        Returns:
            OpenApiExtraDataSource if resource descriptors are available, None otherwise.

        """
        return self.analysis.extra_data_source

    def __repr__(self) -> str:
        info = self.raw_schema["info"]
        return f"<{self.__class__.__name__} for {info['title']} {info['version']}>"

    def __iter__(self) -> Iterator[str]:
        paths = self._get_paths()
        if paths is None:
            return iter(())
        return iter(paths)

    @cached_property
    def default_media_types(self) -> list[str]:
        return self.adapter.get_default_media_types(self.raw_schema)

    def _get_base_path(self) -> str:
        return self.adapter.get_base_path(self.raw_schema)

    def _get_paths(self) -> Mapping[str, Any] | None:
        paths = self.raw_schema.get("paths")
        if paths is None:
            return None
        assert isinstance(paths, Mapping)
        return cast(Mapping[str, Any], paths)

    def _get_operation_map(self, path: str) -> APIOperationMap:
        paths = self._get_paths()
        if paths is None:
            raise KeyError(path)
        path_item = paths[path]
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
                schema=None,  # type: ignore[arg-type]
                responses=None,
                security=None,
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
        paths = self._get_paths()
        if paths is None:
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
        paths = self._get_paths()
        if paths is None:
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
        paths = self._get_paths()
        if paths is None:
            if version.parse(self.specification.version) >= version.parse("3.1"):
                return
            self._raise_invalid_schema(KeyError("paths"))

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
                exc,
                path=path,
                method=method,
                config=self.config.output,
                location=SchemaLocation.maybe_from_error_path(list(exc.absolute_path), self.specification.version),
            ) from None
        raise InvalidSchema(SCHEMA_ERROR_SUGGESTION, path=path, method=method) from error

    def validate(self) -> None:
        with suppress(TypeError):
            self._validate()

    def _validate(self) -> None:
        self.adapter.validate_schema(self.raw_schema)

    def _iter_parameters(
        self, definition: dict[str, Any], shared_parameters: Sequence[dict[str, Any]]
    ) -> list[OperationParameter]:
        return list(
            self.adapter.iter_parameters(
                definition,
                shared_parameters,
                self.default_media_types,
                self.resolver,
                self.adapter,
                self._bundler,
                self._bundle_cache,
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
            path_parameters=OpenApiParameterSet(ParameterLocation.PATH),
            query=OpenApiParameterSet(ParameterLocation.QUERY),
            headers=OpenApiParameterSet(ParameterLocation.HEADER),
            cookies=OpenApiParameterSet(ParameterLocation.COOKIE),
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
                operation.add_parameter(
                    OpenApiParameter.from_definition(definition=param, name_to_uri={}, adapter=self.adapter)
                )
        self.dispatch_hook("before_init_operation", HookContext(operation=operation), operation)
        return operation

    @property
    def resolver(self) -> ReferenceResolver:
        if not hasattr(self, "_resolver"):
            self._resolver = ReferenceResolver(self.location or "", self.raw_schema)
        return self._resolver

    def get_content_types(self, operation: APIOperation, response: Response) -> list[str]:
        """Content types available for this API operation."""
        return self.adapter.get_response_content_types(operation, response)

    def get_request_payload_content_types(self, operation: APIOperation) -> list[str]:
        return self.adapter.get_request_payload_content_types(operation)

    def get_strategies_from_examples(self, operation: APIOperation, **kwargs: Any) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        return get_strategies_from_examples(operation, **kwargs)

    def find_operation_by_id(self, operation_id: str) -> APIOperation:
        """Find an `APIOperation` instance by its `operationId`."""
        return self._operation_lookup.find_by_id(operation_id)

    def find_operation_by_reference(self, reference: str) -> APIOperation:
        """Find local or external `APIOperation` instance by reference.

        Reference example: #/paths/~1users~1{user_id}/patch
        """
        return self._operation_lookup.find_by_reference(reference)

    def find_operation_by_path(self, method: str, path: str) -> APIOperation | None:
        """Find an `APIOperation` by matching an actual request path.

        Matches path templates with parameters, e.g., /users/42 matches /users/{user_id}.
        Returns None if no operation matches.
        """
        from werkzeug.exceptions import MethodNotAllowed, NotFound

        from schemathesis.specs.openapi.stateful.inference import OperationById

        # Match path and method using werkzeug router
        try:
            operation_ref, _ = self.analysis.inferencer._adapter.match(path, method=method.upper())
        except (NotFound, MethodNotAllowed):
            return None

        if isinstance(operation_ref, OperationById):
            return self.find_operation_by_id(operation_ref.value)
        return self.find_operation_by_reference(operation_ref.value)

    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        generation_mode: GenerationMode = GenerationMode.POSITIVE,
        extra_data_source: ExtraDataSource | None = None,
        **kwargs: Any,
    ) -> SearchStrategy:
        return openapi_cases(
            operation=operation,
            hooks=hooks,
            auth_storage=auth_storage,
            generation_mode=generation_mode,
            extra_data_source=extra_data_source,
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
        return self.adapter.get_parameter_serializer(definitions)

    def as_state_machine(self) -> type[APIStateMachine]:
        # Apply dependency inference if configured and not already done
        if self.analysis.should_inject_links():
            self.analysis.inject_links()
        return create_state_machine(self)

    def get_tags(self, operation: APIOperation) -> list[str] | None:
        return operation.definition.raw.get("tags")

    def prepare_multipart(
        self, form_data: dict[str, Any], operation: APIOperation, selected_content_types: dict[str, str] | None = None
    ) -> tuple[list | None, dict[str, Any] | None]:
        return self.adapter.prepare_multipart(operation, form_data, selected_content_types)

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
        multipart_content_types: dict[str, str] | None = None,
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
            multipart_content_types=multipart_content_types,
            meta=meta,
        )

    def validate_response(
        self,
        operation: APIOperation,
        response: Response,
        *,
        case: Case | None = None,
    ) -> bool | None:
        __tracebackhide__ = True
        definition = operation.responses.find_by_status_code(response.status_code)
        if definition is None:
            return None

        documented_media_types = self.get_content_types(operation, response)

        failures: list[Failure] = []

        content_types = response.headers.get("content-type")
        resolved_content_type = content_types[0] if content_types else None

        resolved = definition.get_schema(resolved_content_type)
        if resolved.schema is None:
            return None

        try:
            validator = definition.get_validator_for_schema(resolved.media_type, resolved.schema)
        except jsonschema.SchemaError as exc:
            raise InvalidSchema.from_jsonschema_error(
                exc,
                path=operation.path,
                method=operation.method,
                config=self.config.output,
                location=SchemaLocation.response_schema(self.specification.version),
            ) from exc
        if validator is None:
            return None

        if resolved_content_type is None:
            formatted_content_types = [f"\n- `{content_type}`" for content_type in documented_media_types]
            message = f"The following media types are documented in the schema:{''.join(formatted_content_types)}"
            failures.append(
                MissingContentType(operation=operation.label, message=message, media_types=documented_media_types)
            )
            content_type = resolved.media_type or "application/json"
        else:
            content_type = resolved_content_type

        context = deserialization.DeserializationContext(operation=operation, case=case)

        try:
            data = deserialization.deserialize_response(response, content_type, context=context)
        except JSONDecodeError as exc:
            failures.append(MalformedJson.from_exception(operation=operation.label, exc=exc))
            _maybe_raise_one_or_more(failures)
            return None
        except NotImplementedError:
            # No deserializer available for this media type - skip validation
            # This is expected for many media types (images, binary formats, etc.)
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
            validator.validate(data)
        except jsonschema.SchemaError as exc:
            raise InvalidSchema.from_jsonschema_error(
                exc,
                path=operation.path,
                method=operation.method,
                config=self.config.output,
                location=SchemaLocation.response_schema(self.specification.version),
            ) from exc
        except jsonschema.ValidationError as exc:
            failures.append(
                JsonSchemaError.from_exception(
                    operation=operation.label,
                    exc=exc,
                    config=operation.schema.config.output,
                    name_to_uri=resolved.name_to_uri,
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
        schema = cast(OpenApiSchema, self._parent._schema)
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
