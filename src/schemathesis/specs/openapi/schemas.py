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
    Dict,
    Iterable,
    Iterator,
    Mapping,
    NoReturn,
    Sequence,
    TypeVar,
    cast,
    overload,
)
from urllib.parse import urldefrag, urljoin, urlsplit

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
from . import links, serialization, types as t
from ._cache import OperationCache
from ._hypothesis import get_case_strategy
from ._jsonschema import (
    ObjectSchema,
    Resolver,
    Schema,
    TransformConfig,
    dynamic_scope,
    get_remote_schema_retriever,
    to_jsonschema,
)
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
SCHEMA_PARSING_ERRORS = (ValueError,)

P = TypeVar("P")


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
        initialize_shared_parameters = self.initialize_shared_parameters
        update_shared_parameters = self.update_shared_parameters
        initialize_local_parameters = self.initialize_local_parameters
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
                shared_parameters = initialize_shared_parameters(path_item.get("parameters", []), resolver)
                for method, entry in path_item.items():
                    if method not in HTTP_METHODS:
                        continue
                    try:
                        if should_skip(method, entry):
                            continue
                        local_parameters = initialize_local_parameters(entry, resolver)
                        shared_parameters = update_shared_parameters(shared_parameters, entry)
                        parameters = shared_parameters + local_parameters
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

    def _maybe_resolve(self, value_or_ref: P | t.Reference, resolver: Resolver) -> tuple[Resolver, P]:
        if "$ref" in value_or_ref:
            resolved = resolver.lookup(value_or_ref["$ref"])
            return resolved.resolver, resolved.contents
        return resolver, value_or_ref

    def initialize_shared_parameters(
        self, parameters: list[t.Parameter | t.Reference], resolver: Resolver
    ) -> list[OpenAPIParameter]:
        raise NotImplementedError

    def update_shared_parameters(
        self, parameters: list[OpenAPIParameter], operation: t.Operation
    ) -> list[OpenAPIParameter]:
        raise NotImplementedError

    def initialize_local_parameters(self, operation: t.Operation, resolver: Resolver) -> list[OpenAPIParameter]:
        raise NotImplementedError

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
        # TODO: fix
        # self.security.process_definitions(self.raw_schema, operation, self.resolver)
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
        shared_parameters = self.initialize_shared_parameters(entry.path_item.get("parameters", []), entry.resolver)
        local_parameters = self.initialize_local_parameters(entry.operation, entry.resolver)
        shared_parameters = self.update_shared_parameters(shared_parameters, entry.operation)
        parameters = shared_parameters + local_parameters
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
        operation_resolver = resolved.resolver
        operation = resolved.contents
        if reference.startswith("#"):
            fragment = reference[1:]
        else:
            _, fragment = urldefrag(urljoin(self.location or "", reference))
        path, method = fragment.rsplit("/", maxsplit=2)[-2:]
        path = path.replace("~1", "/").replace("~0", "~")
        # Check the traversal cache as it could've been populated in other places
        scope = dynamic_scope(operation_resolver)
        traversal_key = (scope, path, method)
        cached = cache.get_operation_by_traversal_key(traversal_key)
        if cached is not None:
            return cached
        parent_ref, _ = reference.rsplit("/", maxsplit=1)
        resolved = self.resolver.lookup(parent_ref)
        path_item = resolved.contents
        shared_parameters = self.initialize_shared_parameters(path_item.get("parameters", []), resolved.resolver)
        local_parameters = self.initialize_local_parameters(operation, operation_resolver)
        shared_parameters = self.update_shared_parameters(shared_parameters, operation)
        parameters = shared_parameters + local_parameters
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
        # TODO: Evaluate it eagerly
        # definitions = [item.definition for item in operation.iter_parameters() if item.location == location]
        definitions = []
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
        """Validate the response against the schema."""
        # First, get the high-level response definition
        responses = {str(key): value for key, value in operation.definition.value.get("responses", {}).items()}
        status_code = str(response.status_code)
        if status_code in responses:
            definition = responses[status_code]
        elif "default" in responses:
            definition = responses["default"]
        else:
            # No response defined for the received response status code
            return None
        # Then the actual schema may be behind a reference, so we need to resolve it
        # and take into account the differences in response definitions between different Open API versions
        resolver, schema = self.get_response_schema(definition, operation.definition.resolver)
        if not schema:
            # No schema to check against
            return None
        # Reject responses with unknown content types
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
        # Deserialize the response into JSON
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
        # At this point, the response schema should be converted to a valid JSON schema.
        # There are a few things requiring special attention:
        #
        # 1. Open API specific keywords like `nullable` or `writeOnly` should be properly processed.
        # 2. The schema may contain references to other parts of the root schema, so we need to make them resolvable.
        # 3. The schema itself may already be loaded from an external location, therefore the proper resolving scope
        #    should be used.
        # TODO: Cache response schema
        # TODO: prepare the schema
        if self.spec_version.startswith("3.1") and experimental.OPEN_API_3_1.is_enabled:
            cls = jsonschema.Draft202012Validator
            id_key = "$id"
        else:
            cls = jsonschema.Draft4Validator
            id_key = "id"
        if resolver._base_uri != self.resolver._base_uri:
            schema[id_key] = resolver._base_uri
        try:
            jsonschema.validate(data, schema, cls=cls, registry=resolver._registry)
        except jsonschema.ValidationError as exc:
            if exc.schema == schema and id_key in exc.schema:
                del exc.schema[id_key]
            exc_class = get_schema_validation_error(exc)
            ctx = failures.ValidationErrorContext.from_exception(exc)
            try:
                raise exc_class(ctx.title, context=ctx) from exc
            except Exception as exc:
                errors.append(exc)
        _maybe_raise_one_or_more(errors)
        return None

    def convert_schema_to_jsonschema(
        self, schema: Schema, resolver: Resolver, remove_write_only: bool, remove_read_only: bool
    ) -> dict[str, Any]:
        """Convert the given schema to a JSON schema.

        The Open API specification adds an extra layer of complexity on top of JSON schemas by introducing
        additional keywords and structuring the spcification in a way so it requires extra processing in order
        to be used for data generation or response validation. Additionally, the underlying data generation library,
        `hypothesis-jsonschema` does not support remote or recursive references.

        Generally, all use cases require all references to be resolvable and Open API specific keywords to be replaced
        with their JSON schema counterparts. Specifically, this method moves referenced data to the root of the schema
        as components and replaces references with local ones.
        """
        # Fast path for boolean schemas
        if schema is True:
            return {}
        elif schema is False:
            return {"not": {}}
        config = TransformConfig(
            nullable_key=self.nullable_name,
            remove_write_only=remove_write_only,
            remove_read_only=remove_read_only,
            # If the schema is local in respect to the root schema, then it may contain references to the root schema
            # components. Therefore they should be visible to the schema so references can be resolved.
            components=self._components_cache,
            moved_references=self._moved_references_cache,
        )
        converted = to_jsonschema(resolver._base_uri, schema, self._registry, self._draft, config)
        return cast(dict[str, Any], converted)

    @property
    def _components_cache(self) -> dict[str, ObjectSchema]:
        """A mutable copy of components defined in the schema."""
        if not hasattr(self, "_components_cache_"):
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
                    target.update(fast_deepcopy(source))
            self._components_cache_ = components
            return components
        return self._components_cache_

    @property
    def _moved_references_cache(self) -> dict[str, ObjectSchema]:
        if not hasattr(self, "_moved_references_cache_"):
            cache = {}
            self._moved_references_cache_ = cache
            return cache
        return self._moved_references_cache_


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
        shared_parameters = schema.initialize_shared_parameters(self._path_item.get("parameters", []), resolver)
        local_parameters = schema.initialize_local_parameters(operation, resolver)
        shared_parameters = schema.update_shared_parameters(shared_parameters, operation)
        parameters = shared_parameters + local_parameters
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

    def initialize_shared_parameters(
        self, parameters: list[t.v2.Parameter | t.Reference], path_item_resolver: Resolver
    ) -> list[OpenAPIParameter]:
        initialized: list[OpenAPIParameter] = []
        form_parameters = []
        for parameter_or_ref in parameters:
            resolver, parameter = self._maybe_resolve(parameter_or_ref, path_item_resolver)

            if parameter["in"] == "formData":
                # We need to gather form parameters first before creating a composite parameter for them
                form_parameters.append(parameter_or_ref)
            elif parameter["in"] == "body":
                initialized.append(OpenAPI20Body(definition=parameter_or_ref, media_type=""))
            else:
                initialized.append(OpenAPI20Parameter(definition=parameter_or_ref))

        if form_parameters:
            initialized.append(
                # Individual `formData` parameters are joined into a single "composite" one.
                OpenAPI20CompositeBody.from_parameters(*form_parameters, media_type="")
            )
        return initialized

    def update_shared_parameters(
        self, parameters: list[OpenAPIParameter], operation: t.Operation
    ) -> list[OpenAPIParameter]:
        if not parameters:
            return parameters

        updated: list[OpenAPIParameter] = []

        media_types = self._get_consumes_for_operation(operation)
        body_media_types = media_types or (OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE,)
        form_data_media_types = media_types or (OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE,)
        for parameter in parameters:
            if isinstance(parameter, OpenAPI20Body):
                for media_type in body_media_types:
                    updated.append(
                        OpenAPI20Body(definition=parameter.definition, schema=parameter.schema, media_type=media_type)
                    )
            else:
                # TODO: What about form parameters?
                updated.append(parameter)
        return updated

    def initialize_local_parameters(
        self, operation: t.v2.Operation, operation_resolver: Resolver
    ) -> list[OpenAPIParameter]:
        initialized: list[OpenAPIParameter] = []
        media_types = self._get_consumes_for_operation(operation)
        # For `in=body` parameters, we imply `application/json` as the default media type because it is the most common.
        body_media_types = media_types or (OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE,)
        # If an API operation has parameters with `in=formData`, Schemathesis should know how to serialize it.
        # We can't be 100% sure what media type is expected by the server and chose `multipart/form-data` as
        # the default because it is broader since it allows us to upload files.
        form_data_media_types = media_types or (OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE,)

        form_parameters = []
        parameters = operation.get("parameters", [])
        for parameter_or_ref in parameters:
            resolver, parameter = self._maybe_resolve(parameter_or_ref, operation_resolver)
            if parameter["in"] == "body":
                schema_or_ref = parameter["schema"]
                resolver, schema = self._maybe_resolve(schema_or_ref, resolver)
                cleaned_schema = OpenAPI20Body.clean_schema(schema)
                converted_schema = self.convert_schema_to_jsonschema(
                    cleaned_schema, resolver, remove_write_only=False, remove_read_only=True
                )
                for media_type in body_media_types:
                    initialized.append(
                        OpenAPI20Body(definition=parameter, schema=converted_schema, media_type=media_type)
                    )
            else:
                # Open API 2.0 non-body parameters define schema keywords directly in the parameter object
                cleaned_schema = OpenAPI20Parameter.clean_schema(parameter)
                converted_schema = self.convert_schema_to_jsonschema(
                    cleaned_schema, resolver, remove_write_only=False, remove_read_only=True
                )
                if parameter["in"] == "formData":
                    # We need to gather form parameters first before creating a composite parameter for them
                    form_parameters.append((parameter, converted_schema))
                    continue
                initialized.append(
                    OpenAPI20Parameter(
                        name=parameter["name"],
                        location=parameter["in"],
                        required=parameter.get("required", False),
                        definition=parameter,
                        schema=converted_schema,
                    )
                )

        if form_parameters:
            properties = {}
            required = []
            for parameter, schema in form_parameters:
                name = parameter["name"]
                properties[name] = schema
                # If parameter names are duplicated, we need to avoid duplicate entries in `required` anyway
                if parameter.get("required", False) and name not in required:
                    required.append(name)
            schema = {"properties": properties, "additionalProperties": False, "type": "object", "required": required}
            print(schema)
            for media_type in form_data_media_types:
                # TODO: properly collect it - maybe remove "composite" at all?
                # Remove `definition` from params as well?
                initialized.append(OpenAPI20CompositeBody(definition={}, schema=schema, media_type=media_type))
        return initialized

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
        return resolver, definition.get("schema")

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

    def initialize_shared_parameters(
        self,
        parameters: list[t.v3.Parameter | t.Reference],
        path_item_resolver: Resolver,
    ) -> list[OpenAPIParameter]:
        initialized: list[OpenAPIParameter] = []
        for parameter_or_ref in parameters:
            resolver, parameter = self._maybe_resolve(parameter_or_ref, path_item_resolver)
            schema_or_ref = parameter["schema"]
            resolver, schema = self._maybe_resolve(schema_or_ref, resolver)
            cleaned_schema = OpenAPI30Parameter.clean_schema(schema)
            converted_schema = self.convert_schema_to_jsonschema(
                cleaned_schema, resolver, remove_write_only=False, remove_read_only=True
            )
            initialized.append(
                OpenAPI30Parameter(
                    name=parameter["name"],
                    location=parameter["in"],
                    required=parameter.get("required", False),
                    definition=parameter,
                    schema=converted_schema,
                )
            )
        return initialized

    def update_shared_parameters(
        self, parameters: list[OpenAPIParameter], operation: t.Operation
    ) -> list[OpenAPIParameter]:
        return parameters

    def initialize_local_parameters(
        self,
        operation: t.v3.Operation,
        operation_resolver: Resolver,
    ) -> list[OpenAPIParameter]:
        """Initialize all parameters for an API operation.

        Parameters contain metadata that is needed for data generation.
        Initialization happens eagerly to avoid schema transformations during the generation phase.
        """
        initialized: list[OpenAPIParameter] = []
        parameters = operation.get("parameters", [])
        for parameter_or_ref in parameters:
            resolver, parameter = self._maybe_resolve(parameter_or_ref, operation_resolver)
            schema_or_ref = parameter["schema"]
            resolver, schema = self._maybe_resolve(schema_or_ref, resolver)
            cleaned_schema = OpenAPI30Parameter.clean_schema(schema)
            converted_schema = self.convert_schema_to_jsonschema(
                cleaned_schema, resolver, remove_write_only=False, remove_read_only=True
            )
            initialized.append(
                OpenAPI30Parameter(
                    name=parameter["name"],
                    location=parameter["in"],
                    required=parameter.get("required", False),
                    definition=parameter,
                    schema=converted_schema,
                )
            )
        if "requestBody" in operation:
            resolver, body = self._maybe_resolve(operation["requestBody"], operation_resolver)
            required = body.get("required", False)
            for media_type, content in body["content"].items():
                schema_or_ref = content.get("schema", {})
                resolver, schema = self._maybe_resolve(schema_or_ref, resolver)
                cleaned_schema = OpenAPI30Body.clean_schema(schema)
                converted_schema = self.convert_schema_to_jsonschema(
                    cleaned_schema, resolver, remove_write_only=False, remove_read_only=True
                )
                initialized.append(
                    OpenAPI30Body(
                        name="body",
                        required=required,
                        location="body",
                        definition=content,
                        schema=converted_schema,
                        media_type=media_type,
                    )
                )
        return initialized

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
        return resolver, (option or {}).get("schema")

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
