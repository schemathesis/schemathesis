from __future__ import annotations

import itertools
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass, field
from difflib import get_close_matches
from json import JSONDecodeError
from threading import RLock
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
    Sequence,
    TypeVar,
    cast,
)
from urllib.parse import urlsplit, urljoin, urldefrag

import jsonschema
from hypothesis.strategies import SearchStrategy
from packaging import version
from referencing import Registry, Resource, Specification
from referencing.exceptions import PointerToNowhere, Unresolvable
from referencing.jsonschema import DRAFT4, DRAFT202012
from requests.structures import CaseInsensitiveDict

from ... import experimental, failures
from ..._compat import MultipleFailures
from ..._override import CaseOverride, check_no_override_mark, set_override_mark
from ...auths import AuthStorage
from ...constants import HTTP_METHODS, NOT_SET
from ...exceptions import (
    InternalError,
    OperationNotFound,
    OperationSchemaError,
    SchemaError,
    SchemaErrorType,
    UsageError,
    get_missing_content_type_error,
    get_response_parsing_error,
    get_schema_validation_error,
)
from ...generation import DataGenerationMethod, GenerationConfig
from ...hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher, should_skip_operation
from ...internal.copy import fast_deepcopy
from ...internal.result import Err, Ok, Result
from ...models import APIOperation, Case, OperationDefinition
from ...schemas import APIOperationMap, BaseSchema
from ...stateful import Stateful, StatefulTest
from ...stateful.state_machine import APIStateMachine
from ...transports.content_types import is_json_media_type, parse_content_type
from ...transports.responses import get_json
from ...types import Body, Cookies, FormData, GenericTest, Headers, NotSet, PathParameters, Query
from . import links, serialization
from ._cache import OperationCache
from ._hypothesis import get_case_strategy
from ._jsonschema import (
    TransformConfig,
    Resolver,
    dynamic_scope,
    get_remote_schema_retriever,
    to_jsonschema,
)
from .converter import to_json_schema_recursive
from .definitions import OPENAPI_30_VALIDATOR, OPENAPI_31_VALIDATOR, SWAGGER_20_VALIDATOR
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
from .security import BaseSecurityProcessor, OpenAPISecurityProcessor, SwaggerSecurityProcessor
from .stateful import create_state_machine

if TYPE_CHECKING:
    from ...transports.responses import GenericResponse

SCHEMA_ERROR_MESSAGE = "Ensure that the definition complies with the OpenAPI specification"
SCHEMA_PARSING_ERRORS = (KeyError, AttributeError, PointerToNowhere)


@dataclass(eq=False, repr=False)
class BaseOpenAPISchema(BaseSchema):
    nullable_name: ClassVar[str] = ""
    links_field: ClassVar[str] = ""
    header_required_field: ClassVar[str] = ""
    security: ClassVar[BaseSecurityProcessor] = None  # type: ignore
    _operation_cache: OperationCache = field(default_factory=OperationCache)
    _inline_reference_cache: dict[str, Any] = field(default_factory=dict)
    # Inline references cache can be populated from multiple threads, therefore we need some synchronisation to avoid
    # excessive resolving
    _inline_reference_cache_lock: RLock = field(default_factory=RLock)
    _override: CaseOverride | None = field(default=None)
    component_locations: ClassVar[tuple[tuple[str, ...], ...]] = ()

    @property
    def spec_version(self) -> str:
        raise NotImplementedError

    def get_stateful_tests(
        self, response: GenericResponse, operation: OpenAPIOperation, stateful: Stateful | None
    ) -> Sequence[StatefulTest]:
        if stateful == Stateful.links:
            return links.get_links(response, operation, field=self.links_field)
        return []

    def __repr__(self) -> str:
        info = self.raw_schema["info"]
        return f"<{self.__class__.__name__} for {info['title']} {info['version']}>"

    def __iter__(self) -> Iterator[str]:
        return iter(self.raw_schema.get("paths", {}))

    def _get_operation_map(self, path: str) -> APIOperationMap:
        cache = self._operation_cache
        map = cache.get_map(path)
        if map is not None:
            return map
        path_item = self.raw_schema.get("paths", {})[path]
        scope, path_item = self._resolve_path_item(path_item)
        self.dispatch_hook("before_process_path", HookContext(), path, path_item)
        map = APIOperationMap(self, {})
        map._data = MethodMap(map, scope, path, CaseInsensitiveDict(path_item))
        cache.insert_map(path, map)
        return map

    def on_missing_operation(self, item: str, exc: KeyError) -> NoReturn:
        matches = get_close_matches(item, list(self))
        self._on_missing_operation(item, exc, matches)

    def _on_missing_operation(self, item: str, exc: KeyError, matches: list[str]) -> NoReturn:
        message = f"`{item}` not found"
        if matches:
            message += f". Did you mean `{matches[0]}`?"
        raise OperationNotFound(message=message, item=item) from exc

    def _should_skip(self, method: str, definition: dict[str, Any]) -> bool:
        return (
            method not in HTTP_METHODS
            or should_skip_method(method, self.method)
            or should_skip_deprecated(definition.get("deprecated", False), self.skip_deprecated_operations)
            or should_skip_by_tag(definition.get("tags"), self.tag)
            or should_skip_by_operation_id(definition.get("operationId"), self.operation_id)
        )

    def _operation_iter(self) -> Generator[dict[str, Any], None, None]:
        try:
            paths = self.raw_schema["paths"]
        except KeyError:
            return
        get_full_path = self.get_full_path
        endpoint = self.endpoint
        resolve = self.resolver.lookup
        should_skip = self._should_skip
        for path, path_item in paths.items():
            full_path = get_full_path(path)
            if should_skip_endpoint(full_path, endpoint):
                continue
            try:
                if "$ref" in path_item:
                    path_item = resolve(path_item["$ref"]).contents
                # Straightforward iteration is faster than converting to a set & calculating length.
                for method, definition in path_item.items():
                    if should_skip(method, definition):
                        continue
                    yield definition
            except SCHEMA_PARSING_ERRORS:
                # Ignore errors
                continue

    @property
    def operations_count(self) -> int:
        total = 0
        # Do not build a list from it
        for _ in self._operation_iter():
            total += 1
        return total

    @property
    def links_count(self) -> int:
        total = 0
        resolve = self.resolver.lookup
        links_field = self.links_field
        for definition in self._operation_iter():
            for response in definition.get("responses", {}).values():
                if "$ref" in response:
                    response = resolve(response["$ref"]).contents
                defined_links = response.get(links_field)
                if defined_links is not None:
                    total += len(defined_links)
        return total

    def override(
        self,
        *,
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        path_parameters: dict[str, str] | None = None,
    ) -> Callable[[GenericTest], GenericTest]:
        """Override Open API parameters with fixed values."""

        def _add_override(test: GenericTest) -> GenericTest:
            check_no_override_mark(test)
            override = CaseOverride(
                query=query or {}, headers=headers or {}, cookies=cookies or {}, path_parameters=path_parameters or {}
            )
            set_override_mark(test, override)
            return test

        return _add_override

    def _resolve_until_no_references(self, value: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in value:
            value = self.resolver.lookup(value["$ref"]).contents
        return value

    def _collect_operation_parameters(
        self, path_item: Mapping[str, Any], operation: dict[str, Any]
    ) -> list[OpenAPIParameter]:
        shared_parameters = path_item.get("parameters", ())
        parameters = operation.get("parameters", ())
        return self.collect_parameters(itertools.chain(parameters, shared_parameters), operation)

    def get_all_operations(
        self, hooks: HookDispatcher | None = None
    ) -> Generator[Result[OpenAPIOperation, OperationSchemaError], None, None]:
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
            if version.parse(self.spec_version) >= version.parse("3.1"):
                return
            # Missing `paths` is not recoverable
            self._raise_invalid_schema(exc)

        context = HookContext()
        # Optimization: local variables are faster than attribute access
        get_full_path = self.get_full_path
        endpoint = self.endpoint
        dispatch_hook = self.dispatch_hook
        resolve_path_item = self._resolve_path_item
        should_skip = self._should_skip
        collect_parameters = self.collect_parameters
        make_operation = self.make_operation
        hooks = self.hooks
        for path, path_item in paths.items():
            method = None
            try:
                full_path = get_full_path(path)  # Should be available for later use
                if should_skip_endpoint(full_path, endpoint):
                    continue
                dispatch_hook("before_process_path", context, path, path_item)
                resolver, path_item = resolve_path_item(path_item)
                shared_parameters = path_item.get("parameters", ())
                for method, entry in path_item.items():
                    if method not in HTTP_METHODS:
                        continue
                    try:
                        if should_skip(method, entry):
                            continue
                        parameters = entry.get("parameters", ())
                        parameters = collect_parameters(itertools.chain(parameters, shared_parameters), entry)
                        operation = make_operation(path, method, parameters, entry, resolver)
                        context = HookContext(operation=operation)
                        if (
                            should_skip_operation(GLOBAL_HOOK_DISPATCHER, context)
                            or should_skip_operation(hooks, context)
                            or (hooks and should_skip_operation(hooks, context))
                        ):
                            continue
                        yield Ok(operation)
                    except SCHEMA_PARSING_ERRORS as exc:
                        yield self._into_err(exc, path, method)
            except SCHEMA_PARSING_ERRORS as exc:
                yield self._into_err(exc, path, method)

    def _into_err(self, error: Exception, path: str | None, method: str | None) -> Err[OperationSchemaError]:
        __tracebackhide__ = True
        try:
            full_path = self.get_full_path(path) if isinstance(path, str) else None
            self._raise_invalid_schema(error, full_path, path, method)
        except OperationSchemaError as exc:
            return Err(exc)

    def _raise_invalid_schema(
        self,
        error: Exception,
        full_path: str | None = None,
        path: str | None = None,
        method: str | None = None,
    ) -> NoReturn:
        __tracebackhide__ = True
        if isinstance(error, Unresolvable):
            raise OperationSchemaError.from_reference_resolution_error(
                error, path=path, method=method, full_path=full_path
            ) from None
        try:
            self.validate()
        except jsonschema.ValidationError as exc:
            raise OperationSchemaError.from_jsonschema_error(
                exc, path=path, method=method, full_path=full_path
            ) from None
        raise OperationSchemaError(SCHEMA_ERROR_MESSAGE, path=path, method=method, full_path=full_path) from error

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

    def _resolve_path_item(self, path_item: dict[str, Any]) -> tuple[Resolver, dict[str, Any]]:
        # The path item could be behind a reference
        # In this case, we need to resolve it to get the proper scope for reference inside the item.
        # It is mostly for validating responses.
        if "$ref" in path_item:
            resolved = self.resolver.lookup(path_item["$ref"])
            return resolved.resolver, resolved.contents
        return self.resolver, path_item

    def make_operation(
        self,
        path: str,
        method: str,
        parameters: list[OpenAPIParameter],
        raw: dict[str, Any],
        resolver: Resolver,
    ) -> OpenAPIOperation:
        """Create JSON schemas for the query, body, etc from Swagger parameters definitions."""
        __tracebackhide__ = True
        base_url = self.get_base_url()
        operation: OpenAPIOperation = APIOperation(
            path=path,
            method=method,
            definition=OpenAPIOperationDefinition(raw, resolver),
            base_url=base_url,
            app=self.app,
            schema=self,
        )
        for parameter in parameters:
            operation.add_parameter(parameter)
        self.security.process_definitions(self.raw_schema, operation, self.resolver)
        self.dispatch_hook("before_init_operation", HookContext(operation=operation), operation)
        return operation

    @property
    def _draft(self) -> Specification:
        if self.spec_version.startswith("3.1") and experimental.OPEN_API_3_1.is_enabled:
            return DRAFT202012
        return DRAFT4

    @property
    def _registry(self) -> Registry:
        if not hasattr(self, "_registry_cache"):
            retrieve = get_remote_schema_retriever(self._draft)
            self._registry_cache = Registry(retrieve=retrieve).with_resource(
                self.location or "", Resource(contents=self.raw_schema, specification=self._draft)
            )
        return self._registry_cache

    @property
    def resolver(self) -> Resolver:
        if not hasattr(self, "_resolver"):
            self._resolver = self._registry.resolver(base_uri=self.location or "")
        return self._resolver

    def get_content_types(self, operation: OpenAPIOperation, response: GenericResponse) -> list[str]:
        """Content types available for this API operation."""
        raise NotImplementedError

    def get_strategies_from_examples(self, operation: OpenAPIOperation) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        raise NotImplementedError

    def get_security_requirements(self, operation: OpenAPIOperation) -> list[str]:
        """Get applied security requirements for the given API operation."""
        return self.security.get_security_requirements(self.raw_schema, operation)

    def get_response_schema(
        self, definition: dict[str, Any], resolver: Resolver
    ) -> tuple[Resolver, dict[str, Any] | None]:
        """Extract response schema from `responses`."""
        raise NotImplementedError

    def get_operation_by_id(self, operation_id: str) -> OpenAPIOperation:
        """Get an `OpenAPIOperation` instance by its `operationId`."""
        cache = self._operation_cache
        cached = cache.get_operation_by_id(operation_id)
        if cached is not None:
            return cached
        # Operation has not been accessed yet, need to populate the cache
        if not cache.has_ids_to_definitions:
            self._populate_operation_id_cache(cache)
        try:
            entry = cache.get_definition_by_id(operation_id)
        except KeyError as exc:
            matches = get_close_matches(operation_id, cache.known_operation_ids)
            self._on_missing_operation(operation_id, exc, matches)
        # It could've been already accessed in a different place
        traversal_key = (entry.scope, entry.path, entry.method)
        instance = cache.get_operation_by_traversal_key(traversal_key)
        if instance is not None:
            return instance
        parameters = self._collect_operation_parameters(entry.path_item, entry.operation)
        initialized = self.make_operation(entry.path, entry.method, parameters, entry.operation, entry.resolver)
        cache.insert_operation(initialized, traversal_key=traversal_key, operation_id=operation_id)
        return initialized

    def _populate_operation_id_cache(self, cache: OperationCache) -> None:
        """Collect all operation IDs from the schema."""
        resolver = self.resolver
        lookup = resolver.lookup
        for path, path_item in self.raw_schema.get("paths", {}).items():
            # If the path is behind a reference we have to keep the scope
            # The scope is used to resolve nested components later on
            if "$ref" in path_item:
                resolved = lookup(path_item["$ref"])
                resolver = resolved.resolver
                path_item = resolved.contents
            else:
                resolver = self.resolver
            for key, entry in path_item.items():
                if key not in HTTP_METHODS:
                    continue
                if "operationId" in entry:
                    cache.insert_definition_by_id(
                        entry["operationId"],
                        path=path,
                        method=key,
                        resolver=resolver,
                        path_item=path_item,
                        operation=entry,
                    )

    def get_operation_by_reference(self, reference: str) -> OpenAPIOperation:
        """Get local or external `OpenAPIOperation` instance by reference.

        Reference example: #/paths/~1users~1{user_id}/patch
        """
        cache = self._operation_cache
        cached = cache.get_operation_by_reference(reference)
        if cached is not None:
            return cached
        resolved = self.resolver.lookup(reference)
        operation = resolved.contents
        if reference.startswith("#"):
            fragment = reference[1:]
        else:
            _, fragment = urldefrag(urljoin(self.location or "", reference))
        path, method = fragment.rsplit("/", maxsplit=2)[-2:]
        path = path.replace("~1", "/").replace("~0", "~")
        # Check the traversal cache as it could've been populated in other places
        scope = dynamic_scope(resolved.resolver)
        traversal_key = (scope, path, method)
        cached = cache.get_operation_by_traversal_key(traversal_key)
        if cached is not None:
            return cached
        parent_ref, _ = reference.rsplit("/", maxsplit=1)
        resolved = self.resolver.lookup(parent_ref)
        path_item = resolved.contents
        parameters = self._collect_operation_parameters(path_item, operation)
        initialized = self.make_operation(path, method, parameters, operation, resolved.resolver)
        cache.insert_operation(initialized, traversal_key=traversal_key, reference=reference)
        return initialized

    def get_case_strategy(
        self,
        operation: OpenAPIOperation,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
        generation_config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> SearchStrategy:
        return get_case_strategy(
            operation=operation,
            auth_storage=auth_storage,
            hooks=hooks,
            generator=data_generation_method,
            generation_config=generation_config or self.generation_config,
            **kwargs,
        )

    def get_parameter_serializer(self, operation: OpenAPIOperation, location: str) -> Callable | None:
        definitions = [item.definition for item in operation.iter_parameters() if item.location == location]
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

    def _get_response_definitions(
        self, operation: OpenAPIOperation, response: GenericResponse
    ) -> dict[str, Any] | None:
        try:
            responses = operation.definition.value["responses"]
        except KeyError as exc:
            # Possible to get if `validate_schema=False` is passed during schema creation
            path = operation.path
            full_path = self.get_full_path(path) if isinstance(path, str) else None
            self._raise_invalid_schema(exc, full_path, path, operation.method)
        status_code = str(response.status_code)
        if status_code in responses:
            return operation.definition.maybe_resolve(responses[status_code])
        if "default" in responses:
            return operation.definition.maybe_resolve(responses["default"])
        return None

    def get_headers(self, operation: OpenAPIOperation, response: GenericResponse) -> dict[str, dict[str, Any]] | None:
        definitions = self._get_response_definitions(operation, response)
        if not definitions:
            return None
        return definitions.get("headers")

    def as_state_machine(self) -> type[APIStateMachine]:
        try:
            return create_state_machine(self)
        except OperationNotFound as exc:
            raise SchemaError(
                type=SchemaErrorType.OPEN_API_INVALID_SCHEMA,
                message=f"Invalid Open API link definition: Operation `{exc.item}` not found",
            ) from exc

    def add_link(
        self,
        source: OpenAPIOperation,
        target: str | OpenAPIOperation,
        status_code: str | int,
        parameters: dict[str, str] | None = None,
        request_body: Any = None,
        name: str | None = None,
    ) -> None:
        """Add a new Open API link to the schema definition.

        :param OpenAPIOperation source: This operation is the source of data
        :param target: This operation will receive the data from this link.
            Can be an ``APIOperation`` instance or a reference like this - ``#/paths/~1users~1{userId}/get``
        :param str status_code: The link is triggered when the source API operation responds with this status code.
        :param parameters: A dictionary that describes how parameters should be extracted from the matched response.
            The key represents the parameter name in the target API operation, and the value is a runtime
            expression string.
        :param request_body: A literal value or runtime expression to use as a request body when
            calling the target operation.
        :param str name: Explicit link name.

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
        links.add_link(
            source=source,
            links_field=self.links_field,
            parameters=parameters,
            request_body=request_body,
            status_code=status_code,
            target=target,
            name=name,
        )

    def get_links(self, operation: OpenAPIOperation) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = defaultdict(dict)
        for status_code, link in links.get_all_links(operation):
            result[status_code][link.name] = link
        return result

    def get_tags(self, operation: OpenAPIOperation) -> list[str] | None:
        return operation.definition.value.get("tags")

    def validate_response(self, operation: OpenAPIOperation, response: GenericResponse) -> bool | None:
        responses = {str(key): value for key, value in operation.definition.value.get("responses", {}).items()}
        status_code = str(response.status_code)
        if status_code in responses:
            definition = responses[status_code]
        elif "default" in responses:
            definition = responses["default"]
        else:
            # No response defined for the received response status code
            return None
        resolver, schema = self.get_response_schema(definition, operation.definition.resolver)
        if not schema:
            # No schema to check against
            return None
        content_type = response.headers.get("Content-Type")
        errors = []
        if content_type is None:
            media_types = self.get_content_types(operation, response)
            formatted_content_types = [f"\n- `{content_type}`" for content_type in media_types]
            message = f"The following media types are documented in the schema:{''.join(formatted_content_types)}"
            try:
                raise get_missing_content_type_error()(
                    failures.MissingContentType.title,
                    context=failures.MissingContentType(message=message, media_types=media_types),
                )
            except Exception as exc:
                errors.append(exc)
        if content_type and not is_json_media_type(content_type):
            _maybe_raise_one_or_more(errors)
            return None
        try:
            data = get_json(response)
        except JSONDecodeError as exc:
            exc_class = get_response_parsing_error(exc)
            context = failures.JSONDecodeErrorContext.from_exception(exc)
            try:
                raise exc_class(context.title, context=context) from exc
            except Exception as exc:
                errors.append(exc)
                _maybe_raise_one_or_more(errors)
        # TODO: Cache response schema
        if self.spec_version.startswith("3.1") and experimental.OPEN_API_3_1.is_enabled:
            cls = jsonschema.Draft202012Validator
        else:
            cls = jsonschema.Draft4Validator
        registry = self._registry
        last_scope = next(iter(resolver.dynamic_scope()), None)
        if last_scope is not None:
            _, registry = last_scope
        try:
            jsonschema.validate(data, schema, cls=cls, registry=registry)
        except jsonschema.ValidationError as exc:
            exc_class = get_schema_validation_error(exc)
            ctx = failures.ValidationErrorContext.from_exception(exc)
            try:
                raise exc_class(ctx.title, context=ctx) from exc
            except Exception as exc:
                errors.append(exc)
        _maybe_raise_one_or_more(errors)
        return None  # explicitly return None for mypy

    def prepare_schema(self, operation: OpenAPIOperation, schema: Any) -> Any:
        """Adjust references in the schema to make them suitable for data generation.

        Hypothesis-jsonschema does not support remote or recursive references.
        Therefore, we have to move such referenced data to the root of the schema as components and replace
        references with local ones.
        """
        schema = fast_deepcopy(schema)
        components: dict[str, Any] = {}
        for path in self.component_locations:
            source = self.raw_schema
            target = components
            for segment in path:
                if segment in source:
                    source = source[segment]
                    target = target.setdefault(segment, {})
                else:
                    break
            else:
                # TODO: Bring back fast_deepcopy
                target.update(source)
        if isinstance(schema, dict):
            schema.update(components)
        # TODO: properly pass resolver
        uri = operation.definition.resolver._base_uri or self.location or ""
        registry = self._registry.with_resource(uri, Resource(contents=schema, specification=self._draft))
        resolver = registry.resolver(base_uri=uri)
        config = TransformConfig(nullable_key=self.nullable_name, component_names=list(components))
        return to_jsonschema(schema, resolver, config)


def _maybe_raise_one_or_more(errors: list[Exception]) -> None:
    if not errors:
        return
    elif len(errors) == 1:
        raise errors[0]
    else:
        raise MultipleFailures("\n\n".join(str(error) for error in errors), errors)


@dataclass
class OpenAPIOperationDefinition(OperationDefinition):
    value: dict[str, Any]
    resolver: Resolver

    def maybe_resolve(self, item: dict[str, Any], unlimited: bool = False) -> Any:
        if unlimited:
            while "$ref" in item:
                item = self.lookup(item["$ref"])
        elif "$ref" in item:
            return self.lookup(item["$ref"])
        return item

    def lookup(self, key: str) -> Any:
        return self.resolver.lookup(key).contents


OpenAPIOperation = APIOperation[OpenAPIParameter, Case, OpenAPIOperationDefinition]


@dataclass
class MethodMap(Mapping):
    """Container for accessing API operations.

    Provides a more specific error message if API operation is not found.
    """

    _parent: APIOperationMap
    # Reference resolver
    _resolver: Resolver
    # Methods are stored for this path
    _path: str
    # Storage for definitions
    _path_item: CaseInsensitiveDict

    __slots__ = ("_parent", "_resolver", "_path", "_path_item")

    def __len__(self) -> int:
        return len(self._path_item)

    def __iter__(self) -> Iterator[str]:
        return iter(self._path_item)

    def _init_operation(self, method: str) -> OpenAPIOperation:
        method = method.lower()
        operation = self._path_item[method]
        schema = cast(BaseOpenAPISchema, self._parent._schema)
        cache = schema._operation_cache
        resolver = self._resolver
        scope = tuple(uri for uri, _ in resolver.dynamic_scope())
        path = self._path
        traversal_key = (scope, path, method)
        cached = cache.get_operation_by_traversal_key(traversal_key)
        if cached is not None:
            return cached
        parameters = schema._collect_operation_parameters(self._path_item, operation)
        initialized = schema.make_operation(path, method, parameters, operation, resolver)
        cache.insert_operation(initialized, traversal_key=traversal_key, operation_id=operation.get("operationId"))
        return initialized

    def __getitem__(self, item: str) -> OpenAPIOperation:
        try:
            return self._init_operation(item)
        except KeyError as exc:
            available_methods = ", ".join(map(str.upper, self))
            message = f"Method `{item.upper()}` not found."
            if available_methods:
                message += f" Available methods: {available_methods}"
            raise KeyError(message) from exc


OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE = "application/json"
OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE = "multipart/form-data"
C = TypeVar("C", bound=Case)


class SwaggerV20(BaseOpenAPISchema):
    nullable_name = "x-nullable"
    example_field = "x-example"
    examples_field = "x-examples"
    header_required_field = "x-required"
    security = SwaggerSecurityProcessor()
    component_locations: ClassVar[tuple[tuple[str, ...], ...]] = (("definitions",),)
    links_field = "x-links"

    @property
    def spec_version(self) -> str:
        return self.raw_schema.get("swagger", "2.0")

    @property
    def verbose_name(self) -> str:
        return f"Swagger {self.spec_version}"

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
            if "$ref" in parameter:
                # TODO: use specific resolver for this operation
                parameter = self.resolver.lookup(parameter["$ref"]).contents
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

    def get_strategies_from_examples(self, operation: OpenAPIOperation) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        return get_strategies_from_examples(operation, self.examples_field)

    def get_response_schema(
        self, definition: dict[str, Any], resolver: Resolver
    ) -> tuple[Resolver, dict[str, Any] | None]:
        if "$ref" in definition:
            resolved = resolver.lookup(definition["$ref"])
            resolver = resolved.resolver
            definition = resolved.contents
        schema = definition.get("schema")
        if not schema:
            return resolver, None
        # Extra conversion to JSON Schema is needed here if there was one $ref in the input
        # because it is not converted
        return resolver, to_json_schema_recursive(schema, self.nullable_name, is_response_schema=True)

    def get_content_types(self, operation: OpenAPIOperation, response: GenericResponse) -> list[str]:
        produces = operation.definition.value.get("produces", None)
        if produces:
            return produces
        return self.raw_schema.get("produces", [])

    def _get_parameter_serializer(self, definitions: list[dict[str, Any]]) -> Callable | None:
        return serialization.serialize_swagger2_parameters(definitions)

    def prepare_multipart(
        self, form_data: FormData, operation: OpenAPIOperation
    ) -> tuple[list | None, dict[str, Any] | None]:
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

        for parameter in operation.body:
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

    def get_request_payload_content_types(self, operation: OpenAPIOperation) -> list[str]:
        return self._get_consumes_for_operation(operation.definition.value)

    def make_case(
        self,
        *,
        case_cls: type[C],
        operation: OpenAPIOperation,
        path_parameters: PathParameters | None = None,
        headers: Headers | None = None,
        cookies: Cookies | None = None,
        query: Query | None = None,
        body: Body | NotSet = NOT_SET,
        media_type: str | None = None,
    ) -> C:
        if body is not NOT_SET and media_type is None:
            # If the user wants to send payload, then there should be a media type, otherwise the payload is ignored
            media_types = operation.get_request_payload_content_types()
            if len(media_types) == 1:
                # The only available option
                media_type = media_types[0]
            else:
                media_types_repr = ", ".join(media_types)
                raise UsageError(
                    "Can not detect appropriate media type. "
                    "You can either specify one of the defined media types "
                    f"or pass any other media type available for serialization. Defined media types: {media_types_repr}"
                )
        return case_cls(
            operation=operation,
            path_parameters=path_parameters,
            headers=CaseInsensitiveDict(headers) if headers is not None else headers,
            cookies=cookies,
            query=query,
            body=body,
            media_type=media_type,
            generation_time=0.0,
        )

    def _get_consumes_for_operation(self, definition: dict[str, Any]) -> list[str]:
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

    def get_payload_schema(self, definition: OpenAPIOperationDefinition, media_type: str) -> dict[str, Any] | None:
        for parameter in definition.value.get("parameters", []):
            if parameter["in"] == "body":
                return parameter["schema"]
        return None


class OpenApi30(SwaggerV20):
    nullable_name = "nullable"
    example_field = "example"
    examples_field = "examples"
    header_required_field = "required"
    security = OpenAPISecurityProcessor()
    component_locations = (("components", "schemas"),)
    links_field = "links"

    @property
    def spec_version(self) -> str:
        return self.raw_schema["openapi"]

    @property
    def verbose_name(self) -> str:
        return f"Open API {self.spec_version}"

    def _validate(self) -> None:
        if self.spec_version.startswith("3.1"):
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
        for parameter in parameters:
            # TODO: use specific resolver for this operation
            if "$ref" in parameter:
                parameter = self.resolver.lookup(parameter["$ref"]).contents
            collected.append(OpenAPI30Parameter(definition=parameter))
        if "requestBody" in definition:
            body = definition["requestBody"]
            # TODO: use specific resolver for this operation
            while "$ref" in body:
                body = self.resolver.lookup(body["$ref"]).contents
            required = body.get("required", False)
            description = body.get("description")
            for media_type, content in body["content"].items():
                collected.append(
                    OpenAPI30Body(content, description=description, media_type=media_type, required=required)
                )
        return collected

    def get_response_schema(
        self, definition: dict[str, Any], resolver: Resolver
    ) -> tuple[Resolver, dict[str, Any] | None]:
        if "$ref" in definition:
            resolved = resolver.lookup(definition["$ref"])
            resolver = resolved.resolver
            definition = resolved.contents
        options = iter(definition.get("content", {}).values())
        option = next(options, None)
        # "schema" is an optional key in the `MediaType` object
        if option and "schema" in option:
            # Extra conversion to JSON Schema is needed here if there was one $ref in the input
            # because it is not converted
            return resolver, to_json_schema_recursive(option["schema"], self.nullable_name, is_response_schema=True)
        return resolver, None

    def get_strategies_from_examples(self, operation: OpenAPIOperation) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        return get_strategies_from_examples(operation, self.examples_field)

    def get_content_types(self, operation: OpenAPIOperation, response: GenericResponse) -> list[str]:
        definitions = self._get_response_definitions(operation, response)
        if not definitions:
            return []
        return list(definitions.get("content", {}).keys())

    def _get_parameter_serializer(self, definitions: list[dict[str, Any]]) -> Callable | None:
        return serialization.serialize_openapi3_parameters(definitions)

    def get_request_payload_content_types(self, operation: OpenAPIOperation) -> list[str]:
        request_body = operation.definition.maybe_resolve(operation.definition.value["requestBody"], unlimited=True)
        return list(request_body["content"])

    def prepare_multipart(
        self, form_data: FormData, operation: OpenAPIOperation
    ) -> tuple[list | None, dict[str, Any] | None]:
        """Prepare form data for sending with `requests`.

        :param form_data: Raw generated data as a dictionary.
        :param operation: The tested API operation for which the data was generated.
        :return: `files` and `data` values for `requests.request`.
        """
        files = []
        body = operation.definition.maybe_resolve(operation.definition.value["requestBody"], unlimited=True)
        content = body["content"]
        # Open API 3.0 requires media types to be present. We can get here only if the schema defines
        # the "multipart/form-data" media type, or any other more general media type that matches it (like `*/*`)
        for media_type, entry in content.items():
            main, sub = parse_content_type(media_type)
            if main in ("*", "multipart") and sub in ("*", "form-data", "mixed"):
                schema = entry.get("schema")
                break
        else:
            raise InternalError("No 'multipart/form-data' media type found in the schema")
        for name, property_schema in (schema or {}).get("properties", {}).items():
            if name in form_data:
                if isinstance(form_data[name], list):
                    files.extend([(name, item) for item in form_data[name]])
                elif property_schema.get("format") in ("binary", "base64"):
                    files.append((name, form_data[name]))
                else:
                    files.append((name, (None, form_data[name])))
        # `None` is the default value for `files` and `data` arguments in `requests.request`
        return files or None, None

    def get_payload_schema(self, definition: OpenAPIOperationDefinition, media_type: str) -> dict[str, Any] | None:
        if "requestBody" in definition.value:
            body = definition.maybe_resolve(definition.value["requestBody"], unlimited=True)
            if "content" in body:
                main, sub = parse_content_type(media_type)
                for defined_media_type, item in body["content"].items():
                    if parse_content_type(defined_media_type) == (main, sub):
                        return item["schema"]
        return None
