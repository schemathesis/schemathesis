from __future__ import annotations

import itertools
from collections import defaultdict
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field
from difflib import get_close_matches
from hashlib import sha1
from json import JSONDecodeError
from threading import RLock
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

from schemathesis.core import NOT_SET, NotSet, Specification, deserialization, media_types
from schemathesis.core.compat import RefResolutionError
from schemathesis.core.errors import InternalError, InvalidSchema, LoaderError, LoaderErrorKind, OperationNotFound
from schemathesis.core.failures import Failure, FailureGroup, MalformedJson
from schemathesis.core.result import Err, Ok, Result
from schemathesis.core.transforms import UNRESOLVABLE, deepclone, resolve_pointer, transform
from schemathesis.core.transport import Response
from schemathesis.core.validation import INVALID_HEADER_RE
from schemathesis.generation.case import Case
from schemathesis.generation.meta import CaseMetadata
from schemathesis.openapi.checks import JsonSchemaError, MissingContentType
from schemathesis.specs.openapi.stateful import links
from schemathesis.specs.openapi.utils import expand_status_code

from ...generation import GenerationMode
from ...hooks import HookContext, HookDispatcher
from ...schemas import APIOperation, APIOperationMap, ApiStatistic, BaseSchema, OperationDefinition
from . import serialization
from ._cache import OperationCache
from ._hypothesis import openapi_cases
from .converter import to_json_schema, to_json_schema_recursive
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
from .references import RECURSION_DEPTH_LIMIT, ConvertingResolver, InliningResolver
from .security import BaseSecurityProcessor, OpenAPISecurityProcessor, SwaggerSecurityProcessor
from .stateful import create_state_machine

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

    from schemathesis.auths import AuthStorage
    from schemathesis.generation.stateful import APIStateMachine

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
SCHEMA_ERROR_MESSAGE = "Ensure that the definition complies with the OpenAPI specification"
SCHEMA_PARSING_ERRORS = (KeyError, AttributeError, RefResolutionError, InvalidSchema)


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
    component_locations: ClassVar[tuple[tuple[str, ...], ...]] = ()

    @property
    def specification(self) -> Specification:
        raise NotImplementedError

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
        with in_scope(self.resolver, self.location or ""):
            scope, path_item = self._resolve_path_item(path_item)
        self.dispatch_hook("before_process_path", HookContext(), path, path_item)
        map = APIOperationMap(self, {})
        map._data = MethodMap(map, scope, path, CaseInsensitiveDict(path_item))
        cache.insert_map(path, map)
        return map

    def find_operation_by_label(self, label: str) -> APIOperation | None:
        method, path = label.split(" ", maxsplit=1)
        return self[path][method]

    def on_missing_operation(self, item: str, exc: KeyError) -> NoReturn:
        matches = get_close_matches(item, list(self))
        self._on_missing_operation(item, exc, matches)

    def _on_missing_operation(self, item: str, exc: KeyError, matches: list[str]) -> NoReturn:
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
                definition=OperationDefinition(raw=None, resolved=None, scope=""),
                schema=None,  # type: ignore
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
        operation.definition.resolved = definition
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

    def _operation_iter(self) -> Generator[dict[str, Any], None, None]:
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
                # Straightforward iteration is faster than converting to a set & calculating length.
                for method, definition in path_item.items():
                    if should_skip(path, method, definition):
                        continue
                    yield definition
            except SCHEMA_PARSING_ERRORS:
                # Ignore errors
                continue

    def _resolve_until_no_references(self, value: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in value:
            _, value = self.resolver.resolve(value["$ref"])
        return value

    def _resolve_shared_parameters(self, path_item: Mapping[str, Any]) -> list[dict[str, Any]]:
        return self.resolver.resolve_all(path_item.get("parameters", []), RECURSION_DEPTH_LIMIT - 8)

    def _resolve_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        return self.resolver.resolve_all(operation, RECURSION_DEPTH_LIMIT - 8)

    def _collect_operation_parameters(
        self, path_item: Mapping[str, Any], operation: dict[str, Any]
    ) -> list[OpenAPIParameter]:
        shared_parameters = self._resolve_shared_parameters(path_item)
        parameters = operation.get("parameters", ())
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
        resolve_shared_parameters = self._resolve_shared_parameters
        resolve_operation = self._resolve_operation
        should_skip = self._should_skip
        collect_parameters = self.collect_parameters
        make_operation = self.make_operation
        for path, path_item in paths.items():
            method = None
            try:
                dispatch_hook("before_process_path", context, path, path_item)
                scope, path_item = resolve_path_item(path_item)
                with in_scope(self.resolver, scope):
                    shared_parameters = resolve_shared_parameters(path_item)
                    for method, entry in path_item.items():
                        if method not in HTTP_METHODS:
                            continue
                        try:
                            resolved = resolve_operation(entry)
                            if should_skip(path, method, resolved):
                                continue
                            parameters = resolved.get("parameters", ())
                            parameters = collect_parameters(itertools.chain(parameters, shared_parameters), resolved)
                            operation = make_operation(
                                path,
                                method,
                                parameters,
                                entry,
                                resolved,
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
        raw: dict[str, Any],
        resolved: dict[str, Any],
        scope: str,
    ) -> APIOperation:
        """Create JSON schemas for the query, body, etc from Swagger parameters definitions."""
        __tracebackhide__ = True
        base_url = self.get_base_url()
        operation: APIOperation[OpenAPIParameter] = APIOperation(
            path=path,
            method=method,
            definition=OperationDefinition(raw, resolved, scope),
            base_url=base_url,
            app=self.app,
            schema=self,
        )
        for parameter in parameters:
            operation.add_parameter(parameter)
        config = self.config.generation_for(operation=operation)
        if config.with_security_parameters:
            self.security.process_definitions(self.raw_schema, operation, self.resolver)
        self.dispatch_hook("before_init_operation", HookContext(operation=operation), operation)
        return operation

    @property
    def resolver(self) -> InliningResolver:
        if not hasattr(self, "_resolver"):
            self._resolver = InliningResolver(self.location or "", self.raw_schema)
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

    def get_response_schema(self, definition: dict[str, Any], scope: str) -> tuple[list[str], dict[str, Any] | None]:
        """Extract response schema from `responses`."""
        raise NotImplementedError

    def get_operation_by_id(self, operation_id: str) -> APIOperation:
        """Get an `APIOperation` instance by its `operationId`."""
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
        resolved = self._resolve_operation(entry.operation)
        parameters = self._collect_operation_parameters(entry.path_item, resolved)
        initialized = self.make_operation(entry.path, entry.method, parameters, entry.operation, resolved, entry.scope)
        cache.insert_operation(initialized, traversal_key=traversal_key, operation_id=operation_id)
        return initialized

    def _populate_operation_id_cache(self, cache: OperationCache) -> None:
        """Collect all operation IDs from the schema."""
        resolve = self.resolver.resolve
        default_scope = self.resolver.resolution_scope
        for path, path_item in self.raw_schema.get("paths", {}).items():
            # If the path is behind a reference we have to keep the scope
            # The scope is used to resolve nested components later on
            if "$ref" in path_item:
                scope, path_item = resolve(path_item["$ref"])
            else:
                scope = default_scope
            for key, entry in path_item.items():
                if key not in HTTP_METHODS:
                    continue
                if "operationId" in entry:
                    cache.insert_definition_by_id(
                        entry["operationId"],
                        path=path,
                        method=key,
                        scope=scope,
                        path_item=path_item,
                        operation=entry,
                    )

    def get_operation_by_reference(self, reference: str) -> APIOperation:
        """Get local or external `APIOperation` instance by reference.

        Reference example: #/paths/~1users~1{user_id}/patch
        """
        cache = self._operation_cache
        cached = cache.get_operation_by_reference(reference)
        if cached is not None:
            return cached
        scope, operation = self.resolver.resolve(reference)
        path, method = scope.rsplit("/", maxsplit=2)[-2:]
        path = path.replace("~1", "/").replace("~0", "~")
        # Check the traversal cache as it could've been populated in other places
        traversal_key = (self.resolver.resolution_scope, path, method)
        cached = cache.get_operation_by_traversal_key(traversal_key)
        if cached is not None:
            return cached
        with in_scope(self.resolver, scope):
            resolved = self._resolve_operation(operation)
        parent_ref, _ = reference.rsplit("/", maxsplit=1)
        _, path_item = self.resolver.resolve(parent_ref)
        parameters = self._collect_operation_parameters(path_item, resolved)
        initialized = self.make_operation(path, method, parameters, operation, resolved, scope)
        cache.insert_operation(initialized, traversal_key=traversal_key, reference=reference)
        return initialized

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

    def _get_response_definitions(
        self, operation: APIOperation, response: Response
    ) -> tuple[list[str], dict[str, Any]] | None:
        try:
            responses = operation.definition.raw["responses"]
        except KeyError as exc:
            path = operation.path
            self._raise_invalid_schema(exc, path, operation.method)
        definition = _get_response_definition_by_status(response.status_code, responses)
        if definition is None:
            return None
        return self.resolver.resolve_in_scope(definition, operation.definition.scope)

    def get_headers(
        self, operation: APIOperation, response: Response
    ) -> tuple[list[str], dict[str, dict[str, Any]] | None] | None:
        resolved = self._get_response_definitions(operation, response)
        if not resolved:
            return None
        scopes, definitions = resolved
        return scopes, definitions.get("headers")

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

    @property
    def validator_cls(self) -> type[jsonschema.Validator]:
        if self.specification.version.startswith("3.1"):
            return jsonschema.Draft202012Validator
        return jsonschema.Draft4Validator

    def validate_response(self, operation: APIOperation, response: Response) -> bool | None:
        __tracebackhide__ = True
        responses = {str(key): value for key, value in operation.definition.raw.get("responses", {}).items()}
        definition = _get_response_definition_by_status(response.status_code, responses)
        if definition is None:
            # No response defined for the received response status code
            return None
        scopes, schema = self.get_response_schema(definition, operation.definition.scope)
        if not schema:
            # No schema to check against
            return None
        content_types = response.headers.get("content-type")
        failures: list[Failure] = []
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
        with self._validating_response(scopes) as resolver:
            try:
                jsonschema.validate(
                    data,
                    schema,
                    cls=self.validator_cls,
                    resolver=resolver,
                    # Use a recent JSON Schema format checker to get most of formats checked for older drafts as well
                    format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
                )
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
            self.location or "", self.raw_schema, nullable_name=self.nullable_name, is_response_schema=True
        )
        with in_scopes(resolver, scopes):
            yield resolver

    @property
    def rewritten_components(self) -> dict[str, Any]:
        if not hasattr(self, "_rewritten_components"):

            def callback(_schema: dict[str, Any], nullable_name: str) -> dict[str, Any]:
                _schema = to_json_schema(_schema, nullable_name=nullable_name, copy=False)
                return self._rewrite_references(_schema, self.resolver)

            # Different spec versions allow different keywords to store possible reference targets
            components: dict[str, Any] = {}
            for path in self.component_locations:
                schema = self.raw_schema
                target = components
                for chunk in path:
                    if chunk in schema:
                        schema = schema[chunk]
                        target = target.setdefault(chunk, {})
                    else:
                        break
                else:
                    target.update(transform(deepclone(schema), callback, self.nullable_name))
            if self._inline_reference_cache:
                components[INLINED_REFERENCES_KEY] = self._inline_reference_cache
            self._rewritten_components = components
        return self._rewritten_components

    def prepare_schema(self, schema: Any) -> Any:
        """Inline Open API definitions.

        Inlining components helps `hypothesis-jsonschema` generate data that involves non-resolved references.
        """
        schema = deepclone(schema)
        schema = transform(schema, self._rewrite_references, self.resolver)
        # Only add definitions that are reachable from the schema via references
        stack = [schema]
        seen = set()
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if "$ref" in item:
                    reference = item["$ref"]
                    if isinstance(reference, str) and reference.startswith("#/") and reference not in seen:
                        seen.add(reference)
                        # Resolve the component and add it to the proper place in the schema
                        pointer = reference[1:]
                        resolved = resolve_pointer(self.rewritten_components, pointer)
                        if resolved is UNRESOLVABLE:
                            raise LoaderError(
                                LoaderErrorKind.OPEN_API_INVALID_SCHEMA,
                                message=f"Unresolvable JSON pointer in the schema: {pointer}",
                            )
                        if isinstance(resolved, dict):
                            container = schema
                            for key in pointer.split("/")[1:]:
                                container = container.setdefault(key, {})
                            container.update(resolved)
                        # Explore the resolved value too
                        stack.append(resolved)
                # Still explore other values as they may have nested references in other keys
                for value in item.values():
                    if isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(item, list):
                for sub_item in item:
                    if isinstance(sub_item, (dict, list)):
                        stack.append(sub_item)
        return schema

    def _rewrite_references(self, schema: dict[str, Any], resolver: InliningResolver) -> dict[str, Any]:
        """Rewrite references present in the schema.

        The idea is to resolve references, cache the result and replace these references with new ones
        that point to a local path which is populated from this cache later on.
        """
        reference = schema.get("$ref")
        # If `$ref` is not a property name and should be processed
        if reference is not None and isinstance(reference, str) and not reference.startswith("#/"):
            key = _make_reference_key(resolver._scopes_stack, reference)
            with self._inline_reference_cache_lock:
                if key not in self._inline_reference_cache:
                    with resolver.resolving(reference) as resolved:
                        # Resolved object also may have references
                        self._inline_reference_cache[key] = transform(
                            resolved, lambda s: self._rewrite_references(s, resolver)
                        )
            # Rewrite the reference with the new location
            schema["$ref"] = f"#/{INLINED_REFERENCES_KEY}/{key}"
        return schema


def _get_response_definition_by_status(status_code: int, responses: dict[str, Any]) -> dict[str, Any] | None:
    # Cast to string, as integers are often there due to YAML deserialization
    responses = {str(status): definition for status, definition in responses.items()}
    if str(status_code) in responses:
        return responses[str(status_code)]
    # More specific should go first
    keys = sorted(responses, key=lambda k: k.count("X"))
    for key in keys:
        if key == "default":
            continue
        status_codes = expand_status_code(key)
        if status_code in status_codes:
            return responses[key]
    if "default" in responses:
        return responses["default"]
    return None


def _maybe_raise_one_or_more(failures: list[Failure]) -> None:
    if not failures:
        return
    if len(failures) == 1:
        raise failures[0] from None
    raise FailureGroup(failures) from None


def _make_reference_key(scopes: list[str], reference: str) -> str:
    """A name under which the resolved reference data will be stored."""
    # Using a hexdigest is the simplest way to associate practically unique keys with each reference
    digest = sha1()
    for scope in scopes:
        digest.update(scope.encode("utf-8"))
        # Separator to avoid collisions like this: ["a"], "bc" vs. ["ab"], "c". Otherwise, the resulting digest
        # will be the same for both cases
        digest.update(b"#")
    digest.update(reference.encode("utf-8"))
    return digest.hexdigest()


INLINED_REFERENCES_KEY = "x-inlined"


@contextmanager
def in_scope(resolver: jsonschema.RefResolver, scope: str) -> Generator[None, None, None]:
    resolver.push_scope(scope)
    try:
        yield
    finally:
        resolver.pop_scope()


@contextmanager
def in_scopes(resolver: jsonschema.RefResolver, scopes: list[str]) -> Generator[None, None, None]:
    """Push all available scopes into the resolver.

    There could be an additional scope change during a schema resolving in `get_response_schema`, so in total there
    could be a stack of two scopes maximum. This context manager handles both cases (1 or 2 scope changes) in the same
    way.
    """
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
        cache = schema._operation_cache
        path = self._path
        scope = self._scope
        traversal_key = (scope, path, method)
        cached = cache.get_operation_by_traversal_key(traversal_key)
        if cached is not None:
            return cached
        schema.resolver.push_scope(scope)
        try:
            resolved = schema._resolve_operation(operation)
        finally:
            schema.resolver.pop_scope()
        parameters = schema._collect_operation_parameters(self._path_item, resolved)
        initialized = schema.make_operation(path, method, parameters, operation, resolved, scope)
        cache.insert_operation(initialized, traversal_key=traversal_key, operation_id=resolved.get("operationId"))
        return initialized

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
    nullable_name = "x-nullable"
    example_field = "x-example"
    examples_field = "x-examples"
    header_required_field = "x-required"
    security = SwaggerSecurityProcessor()
    component_locations: ClassVar[tuple[tuple[str, ...], ...]] = (("definitions",),)
    links_field = "x-links"

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
                for media_type in body_media_types:
                    collected.append(OpenAPI20Body(definition=parameter, media_type=media_type))
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

    def get_response_schema(self, definition: dict[str, Any], scope: str) -> tuple[list[str], dict[str, Any] | None]:
        scopes, definition = self.resolver.resolve_in_scope(definition, scope)
        schema = definition.get("schema")
        if not schema:
            return scopes, None
        # Extra conversion to JSON Schema is needed here if there was one $ref in the input
        # because it is not converted
        return scopes, to_json_schema_recursive(
            schema, self.nullable_name, is_response_schema=True, update_quantifiers=False
        )

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

    def _get_payload_schema(self, definition: dict[str, Any], media_type: str) -> dict[str, Any] | None:
        for parameter in definition.get("parameters", []):
            if "$ref" in parameter:
                _, parameter = self.resolver.resolve(parameter["$ref"])
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

        for parameter in parameters:
            if parameter["in"] in ("header", "cookie"):
                check_header(parameter)
            collected.append(OpenAPI30Parameter(definition=parameter))
        if "requestBody" in definition:
            required = definition["requestBody"].get("required", False)
            description = definition["requestBody"].get("description")
            for media_type, content in definition["requestBody"]["content"].items():
                collected.append(
                    OpenAPI30Body(content, description=description, media_type=media_type, required=required)
                )
        return collected

    def get_response_schema(self, definition: dict[str, Any], scope: str) -> tuple[list[str], dict[str, Any] | None]:
        scopes, definition = self.resolver.resolve_in_scope(definition, scope)
        options = iter(definition.get("content", {}).values())
        option = next(options, None)
        # "schema" is an optional key in the `MediaType` object
        if option and "schema" in option:
            # Extra conversion to JSON Schema is needed here if there was one $ref in the input
            # because it is not converted
            return scopes, to_json_schema_recursive(
                option["schema"], self.nullable_name, is_response_schema=True, update_quantifiers=False
            )
        return scopes, None

    def get_strategies_from_examples(self, operation: APIOperation, **kwargs: Any) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        return get_strategies_from_examples(operation, **kwargs)

    def get_content_types(self, operation: APIOperation, response: Response) -> list[str]:
        resolved = self._get_response_definitions(operation, response)
        if not resolved:
            return []
        _, definitions = resolved
        return list(definitions.get("content", {}).keys())

    def _get_parameter_serializer(self, definitions: list[dict[str, Any]]) -> Callable | None:
        return serialization.serialize_openapi3_parameters(definitions)

    def get_request_payload_content_types(self, operation: APIOperation) -> list[str]:
        request_body = self._resolve_until_no_references(operation.definition.raw["requestBody"])
        return list(request_body["content"])

    def prepare_multipart(
        self, form_data: dict[str, Any], operation: APIOperation
    ) -> tuple[list | None, dict[str, Any] | None]:
        files = []
        definition = operation.definition.raw
        if "$ref" in definition["requestBody"]:
            self.resolver.push_scope(operation.definition.scope)
            try:
                body = self.resolver.resolve_all(definition["requestBody"], RECURSION_DEPTH_LIMIT)
            finally:
                self.resolver.pop_scope()
        else:
            body = definition["requestBody"]
        content = body["content"]
        # Open API 3.0 requires media types to be present. We can get here only if the schema defines
        # the "multipart/form-data" media type, or any other more general media type that matches it (like `*/*`)
        for media_type, entry in content.items():
            main, sub = media_types.parse(media_type)
            if main in ("*", "multipart") and sub in ("*", "form-data", "mixed"):
                schema = entry.get("schema")
                break
        else:
            raise InternalError("No 'multipart/form-data' media type found in the schema")
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

    def _get_payload_schema(self, definition: dict[str, Any], media_type: str) -> dict[str, Any] | None:
        if "requestBody" in definition:
            if "$ref" in definition["requestBody"]:
                body = self.resolver.resolve_all(definition["requestBody"], RECURSION_DEPTH_LIMIT)
            else:
                body = definition["requestBody"]
            if "content" in body:
                main, sub = media_types.parse(media_type)
                for defined_media_type, item in body["content"].items():
                    if media_types.parse(defined_media_type) == (main, sub):
                        return item["schema"]
        return None
