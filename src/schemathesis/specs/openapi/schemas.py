from __future__ import annotations

import itertools
from collections import defaultdict
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field
from difflib import get_close_matches
from hashlib import sha1
from json import JSONDecodeError
from threading import RLock
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Generator,
    Iterable,
    NoReturn,
    Sequence,
    TypeVar,
)
from urllib.parse import urlsplit

import jsonschema
from hypothesis.strategies import SearchStrategy
from packaging import version
from requests.structures import CaseInsensitiveDict

from ... import experimental, failures
from ..._compat import MultipleFailures
from ..._override import CaseOverride, set_override_mark, check_no_override_mark
from ...auths import AuthStorage
from ...generation import DataGenerationMethod, GenerationConfig
from ...constants import HTTP_METHODS, NOT_SET
from ...exceptions import (
    InternalError,
    OperationSchemaError,
    UsageError,
    get_missing_content_type_error,
    get_response_parsing_error,
    get_schema_validation_error,
    SchemaError,
    SchemaErrorType,
    OperationNotFound,
)
from ...hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher, should_skip_operation
from ...internal.copy import fast_deepcopy
from ...internal.jsonschema import traverse_schema
from ...internal.result import Err, Ok, Result
from ...models import APIOperation, Case, OperationDefinition
from ...schemas import BaseSchema, APIOperationMap
from ...stateful import Stateful, StatefulTest
from ...stateful.state_machine import APIStateMachine
from ...transports.content_types import is_json_media_type, parse_content_type
from ...transports.responses import get_json
from ...types import Body, Cookies, FormData, Headers, NotSet, PathParameters, Query, GenericTest
from . import links, serialization
from ._hypothesis import get_case_strategy
from .converter import to_json_schema, to_json_schema_recursive
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
from .references import RECURSION_DEPTH_LIMIT, ConvertingResolver, InliningResolver, resolve_pointer, UNRESOLVABLE
from .security import BaseSecurityProcessor, OpenAPISecurityProcessor, SwaggerSecurityProcessor
from .stateful import create_state_machine

if TYPE_CHECKING:
    from ...transports.responses import GenericResponse

SCHEMA_ERROR_MESSAGE = "Ensure that the definition complies with the OpenAPI specification"
SCHEMA_PARSING_ERRORS = (KeyError, AttributeError, jsonschema.exceptions.RefResolutionError)


@dataclass(eq=False, repr=False)
class BaseOpenAPISchema(BaseSchema):
    nullable_name: ClassVar[str] = ""
    links_field: ClassVar[str] = ""
    header_required_field: ClassVar[str] = ""
    security: ClassVar[BaseSecurityProcessor] = None  # type: ignore
    _operations_by_id: dict[str, APIOperation] = field(init=False)
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
        self, response: GenericResponse, operation: APIOperation, stateful: Stateful | None
    ) -> Sequence[StatefulTest]:
        if stateful == Stateful.links:
            return links.get_links(response, operation, field=self.links_field)
        return []

    def __repr__(self) -> str:
        info = self.raw_schema["info"]
        return f"<{self.__class__.__name__} for {info['title']} {info['version']}>"

    def _store_operations(
        self, operations: Generator[Result[APIOperation, OperationSchemaError], None, None]
    ) -> dict[str, APIOperationMap]:
        return operations_to_dict(operations)

    def on_missing_operation(self, item: str, exc: KeyError) -> NoReturn:
        matches = get_close_matches(item, list(self.operations))
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
        resolve = self.resolver.resolve
        for path, methods in paths.items():
            full_path = self.get_full_path(path)
            if should_skip_endpoint(full_path, self.endpoint):
                continue
            try:
                if "$ref" in methods:
                    _, resolved_methods = resolve(methods["$ref"])
                else:
                    resolved_methods = methods
                # Straightforward iteration is faster than converting to a set & calculating length.
                for method, definition in resolved_methods.items():
                    if self._should_skip(method, definition):
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
        for definition in self._operation_iter():
            for response in definition.get("responses", {}).values():
                if "$ref" in response:
                    _, response = self.resolver.resolve(response["$ref"])
                defined_links = response.get(self.links_field)
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

    def get_all_operations(
        self, hooks: HookDispatcher | None = None
    ) -> Generator[Result[APIOperation, OperationSchemaError], None, None]:
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
        for path, methods in paths.items():
            method = None
            try:
                full_path = self.get_full_path(path)  # Should be available for later use
                if should_skip_endpoint(full_path, self.endpoint):
                    continue
                self.dispatch_hook("before_process_path", context, path, methods)
                scope, raw_methods = self._resolve_methods(methods)
                common_parameters = self.resolver.resolve_all(methods.get("parameters", []), RECURSION_DEPTH_LIMIT - 8)
                for method, definition in raw_methods.items():
                    try:
                        # Setting a low recursion limit doesn't solve the problem with recursive references & inlining
                        # too much but decreases the number of cases when Schemathesis stuck on this step.
                        self.resolver.push_scope(scope)
                        try:
                            resolved_definition = self.resolver.resolve_all(definition, RECURSION_DEPTH_LIMIT - 8)
                        finally:
                            self.resolver.pop_scope()
                        # Only method definitions are parsed
                        if self._should_skip(method, resolved_definition):
                            continue
                        parameters = self.collect_parameters(
                            itertools.chain(resolved_definition.get("parameters", ()), common_parameters),
                            resolved_definition,
                        )
                        # To prevent recursion errors we need to pass not resolved schema as well
                        # It could be used for response validation
                        raw_definition = OperationDefinition(raw_methods[method], resolved_definition, scope)
                        operation = self.make_operation(path, method, parameters, raw_definition)
                        context = HookContext(operation=operation)
                        if (
                            should_skip_operation(GLOBAL_HOOK_DISPATCHER, context)
                            or should_skip_operation(self.hooks, context)
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
        if isinstance(error, jsonschema.exceptions.RefResolutionError):
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

    def _resolve_methods(self, methods: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        # We need to know a proper scope in what methods are.
        # It will allow us to provide a proper reference resolving in `response_schema_conformance` and avoid
        # recursion errors
        if "$ref" in methods:
            return fast_deepcopy(self.resolver.resolve(methods["$ref"]))
        return self.resolver.resolution_scope, fast_deepcopy(methods)

    def make_operation(
        self,
        path: str,
        method: str,
        parameters: list[OpenAPIParameter],
        raw_definition: OperationDefinition,
    ) -> APIOperation:
        """Create JSON schemas for the query, body, etc from Swagger parameters definitions."""
        __tracebackhide__ = True
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
        self.dispatch_hook("before_init_operation", HookContext(operation=operation), operation)
        return operation

    @property
    def resolver(self) -> InliningResolver:
        if not hasattr(self, "_resolver"):
            self._resolver = InliningResolver(self.location or "", self.raw_schema)
        return self._resolver

    def get_content_types(self, operation: APIOperation, response: GenericResponse) -> list[str]:
        """Content types available for this API operation."""
        raise NotImplementedError

    def get_strategies_from_examples(self, operation: APIOperation) -> list[SearchStrategy[Case]]:
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
        if not hasattr(self, "_operations_by_id"):
            self._operations_by_id = dict(self._group_operations_by_id())
        try:
            return self._operations_by_id[operation_id]
        except KeyError as exc:
            matches = get_close_matches(operation_id, list(self._operations_by_id))
            self._on_missing_operation(operation_id, exc, matches)

    def _group_operations_by_id(self) -> Generator[tuple[str, APIOperation], None, None]:
        for path, methods in self.raw_schema["paths"].items():
            scope, raw_methods = self._resolve_methods(methods)
            common_parameters = self.resolver.resolve_all(methods.get("parameters", []), RECURSION_DEPTH_LIMIT - 8)
            for method, definition in methods.items():
                if method not in HTTP_METHODS or "operationId" not in definition:
                    continue
                self.resolver.push_scope(scope)
                try:
                    resolved_definition = self.resolver.resolve_all(definition, RECURSION_DEPTH_LIMIT - 8)
                finally:
                    self.resolver.pop_scope()
                parameters = self.collect_parameters(
                    itertools.chain(resolved_definition.get("parameters", ()), common_parameters), resolved_definition
                )
                raw_definition = OperationDefinition(raw_methods[method], resolved_definition, scope)
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
        common_parameters = self.resolver.resolve_all(methods.get("parameters", []), RECURSION_DEPTH_LIMIT - 8)
        parameters = self.collect_parameters(
            itertools.chain(resolved_definition.get("parameters", ()), common_parameters), resolved_definition
        )
        raw_definition = OperationDefinition(data, resolved_definition, scope)
        return self.make_operation(path, method, parameters, raw_definition)

    def get_case_strategy(
        self,
        operation: APIOperation,
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

    def get_parameter_serializer(self, operation: APIOperation, location: str) -> Callable | None:
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

    def _get_response_definitions(self, operation: APIOperation, response: GenericResponse) -> dict[str, Any] | None:
        try:
            responses = operation.definition.resolved["responses"]
        except KeyError as exc:
            # Possible to get if `validate_schema=False` is passed during schema creation
            path = operation.path
            full_path = self.get_full_path(path) if isinstance(path, str) else None
            self._raise_invalid_schema(exc, full_path, path, operation.method)
        status_code = str(response.status_code)
        if status_code in responses:
            return responses[status_code]
        if "default" in responses:
            return responses["default"]
        return None

    def get_headers(self, operation: APIOperation, response: GenericResponse) -> dict[str, dict[str, Any]] | None:
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
        source: APIOperation,
        target: str | APIOperation,
        status_code: str | int,
        parameters: dict[str, str] | None = None,
        request_body: Any = None,
        name: str | None = None,
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
        if hasattr(self, "_operations"):
            delattr(self, "_operations")
        for operation, methods in self.raw_schema["paths"].items():
            if operation == source.path:
                # Methods should be completely resolved now, otherwise they might miss a resolving scope when
                # they will be fully resolved later
                methods = self.resolver.resolve_all(methods, RECURSION_DEPTH_LIMIT - 8)
                found = False
                for method, definition in methods.items():
                    if method.upper() == source.method.upper():
                        found = True
                        links.add_link(
                            responses=definition["responses"],
                            links_field=self.links_field,
                            parameters=parameters,
                            request_body=request_body,
                            status_code=status_code,
                            target=target,
                            name=name,
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

    def get_links(self, operation: APIOperation) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = defaultdict(dict)
        for status_code, link in links.get_all_links(operation):
            result[status_code][link.name] = link
        return result

    def get_tags(self, operation: APIOperation) -> list[str] | None:
        return operation.definition.resolved.get("tags")

    def validate_response(self, operation: APIOperation, response: GenericResponse) -> bool | None:
        responses = {str(key): value for key, value in operation.definition.raw.get("responses", {}).items()}
        status_code = str(response.status_code)
        if status_code in responses:
            definition = responses[status_code]
        elif "default" in responses:
            definition = responses["default"]
        else:
            # No response defined for the received response status code
            return None
        scopes, schema = self.get_response_schema(definition, operation.definition.scope)
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
        resolver = ConvertingResolver(
            self.location or "", self.raw_schema, nullable_name=self.nullable_name, is_response_schema=True
        )
        if self.spec_version.startswith("3.1") and experimental.OPEN_API_3_1.is_enabled:
            cls = jsonschema.Draft202012Validator
        else:
            cls = jsonschema.Draft4Validator
        with in_scopes(resolver, scopes):
            try:
                jsonschema.validate(data, schema, cls=cls, resolver=resolver)
            except jsonschema.ValidationError as exc:
                exc_class = get_schema_validation_error(exc)
                ctx = failures.ValidationErrorContext.from_exception(exc)
                try:
                    raise exc_class(ctx.title, context=ctx) from exc
                except Exception as exc:
                    errors.append(exc)
        _maybe_raise_one_or_more(errors)
        return None  # explicitly return None for mypy

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
                    target.update(traverse_schema(fast_deepcopy(schema), callback, self.nullable_name))
            if self._inline_reference_cache:
                components[INLINED_REFERENCES_KEY] = self._inline_reference_cache
            self._rewritten_components = components
        return self._rewritten_components

    def prepare_schema(self, schema: Any) -> Any:
        """Inline Open API definitions.

        Inlining components helps `hypothesis-jsonschema` generate data that involves non-resolved references.
        """
        schema = fast_deepcopy(schema)
        schema = traverse_schema(schema, self._rewrite_references, self.resolver)
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
                            raise SchemaError(
                                SchemaErrorType.OPEN_API_INVALID_SCHEMA,
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
                        self._inline_reference_cache[key] = traverse_schema(
                            resolved, lambda s: self._rewrite_references(s, resolver)
                        )
            # Rewrite the reference with the new location
            schema["$ref"] = f"#/{INLINED_REFERENCES_KEY}/{key}"
        return schema


def _maybe_raise_one_or_more(errors: list[Exception]) -> None:
    if not errors:
        return
    elif len(errors) == 1:
        raise errors[0]
    else:
        raise MultipleFailures("\n\n".join(str(error) for error in errors), errors)


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


def operations_to_dict(
    operations: Generator[Result[APIOperation, OperationSchemaError], None, None],
) -> dict[str, APIOperationMap]:
    output: dict[str, APIOperationMap] = {}
    for result in operations:
        if isinstance(result, Ok):
            operation = result.ok()
            output.setdefault(operation.path, APIOperationMap(MethodMap()))
            output[operation.path][operation.method] = operation
    return output


class MethodMap(CaseInsensitiveDict):
    """Container for accessing API operations.

    Provides a more specific error message if API operation is not found.
    """

    def __getitem__(self, item: str) -> APIOperation:
        try:
            return super().__getitem__(item)
        except KeyError as exc:
            available_methods = ", ".join(map(str.upper, self))
            message = f"Method `{item}` not found. Available methods: {available_methods}"
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

    def get_strategies_from_examples(self, operation: APIOperation) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        return get_strategies_from_examples(operation, self.examples_field)

    def get_response_schema(self, definition: dict[str, Any], scope: str) -> tuple[list[str], dict[str, Any] | None]:
        scopes, definition = self.resolver.resolve_in_scope(fast_deepcopy(definition), scope)
        schema = definition.get("schema")
        if not schema:
            return scopes, None
        # Extra conversion to JSON Schema is needed here if there was one $ref in the input
        # because it is not converted
        return scopes, to_json_schema_recursive(schema, self.nullable_name, is_response_schema=True)

    def get_content_types(self, operation: APIOperation, response: GenericResponse) -> list[str]:
        produces = operation.definition.raw.get("produces", None)
        if produces:
            return produces
        return self.raw_schema.get("produces", [])

    def _get_parameter_serializer(self, definitions: list[dict[str, Any]]) -> Callable | None:
        return serialization.serialize_swagger2_parameters(definitions)

    def prepare_multipart(
        self, form_data: FormData, operation: APIOperation
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

    def get_request_payload_content_types(self, operation: APIOperation) -> list[str]:
        return self._get_consumes_for_operation(operation.definition.resolved)

    def make_case(
        self,
        *,
        case_cls: type[C],
        operation: APIOperation,
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

    def _get_payload_schema(self, definition: dict[str, Any], media_type: str) -> dict[str, Any] | None:
        for parameter in definition.get("parameters", []):
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
        collected: list[OpenAPIParameter] = [OpenAPI30Parameter(definition=parameter) for parameter in parameters]
        if "requestBody" in definition:
            required = definition["requestBody"].get("required", False)
            description = definition["requestBody"].get("description")
            for media_type, content in definition["requestBody"]["content"].items():
                collected.append(
                    OpenAPI30Body(content, description=description, media_type=media_type, required=required)
                )
        return collected

    def get_response_schema(self, definition: dict[str, Any], scope: str) -> tuple[list[str], dict[str, Any] | None]:
        scopes, definition = self.resolver.resolve_in_scope(fast_deepcopy(definition), scope)
        options = iter(definition.get("content", {}).values())
        option = next(options, None)
        # "schema" is an optional key in the `MediaType` object
        if option and "schema" in option:
            # Extra conversion to JSON Schema is needed here if there was one $ref in the input
            # because it is not converted
            return scopes, to_json_schema_recursive(option["schema"], self.nullable_name, is_response_schema=True)
        return scopes, None

    def get_strategies_from_examples(self, operation: APIOperation) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        return get_strategies_from_examples(operation, self.examples_field)

    def get_content_types(self, operation: APIOperation, response: GenericResponse) -> list[str]:
        definitions = self._get_response_definitions(operation, response)
        if not definitions:
            return []
        return list(definitions.get("content", {}).keys())

    def _get_parameter_serializer(self, definitions: list[dict[str, Any]]) -> Callable | None:
        return serialization.serialize_openapi3_parameters(definitions)

    def get_request_payload_content_types(self, operation: APIOperation) -> list[str]:
        return list(operation.definition.resolved["requestBody"]["content"].keys())

    def prepare_multipart(
        self, form_data: FormData, operation: APIOperation
    ) -> tuple[list | None, dict[str, Any] | None]:
        """Prepare form data for sending with `requests`.

        :param form_data: Raw generated data as a dictionary.
        :param operation: The tested API operation for which the data was generated.
        :return: `files` and `data` values for `requests.request`.
        """
        files = []
        content = operation.definition.resolved["requestBody"]["content"]
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

    def _get_payload_schema(self, definition: dict[str, Any], media_type: str) -> dict[str, Any] | None:
        if "requestBody" in definition:
            if "$ref" in definition["requestBody"]:
                body = self.resolver.resolve_all(definition["requestBody"], RECURSION_DEPTH_LIMIT)
            else:
                body = definition["requestBody"]
            if "content" in body:
                main, sub = parse_content_type(media_type)
                for defined_media_type, item in body["content"].items():
                    if parse_content_type(defined_media_type) == (main, sub):
                        return item["schema"]
        return None
