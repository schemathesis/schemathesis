from __future__ import annotations

import threading
from collections.abc import Generator, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, NoReturn, cast
from urllib.parse import unquote

import jsonschema_rs
from packaging import version

from schemathesis.core import INJECTED_PATH_PARAMETER_KEY, media_types
from schemathesis.core.adapter import OperationParameter, ParsedParameters
from schemathesis.core.errors import (
    SCHEMA_ERROR_SUGGESTION,
    HookExecutionError,
    InvalidSchema,
    MalformedMediaType,
    OperationNotFound,
    RefResolutionError,
    SchemaLocation,
)
from schemathesis.core.jsonschema.resolver import Resolver, resolve_reference
from schemathesis.core.parameters import ParameterLocation, SkippedParameter
from schemathesis.core.result import Err, Ok, Result
from schemathesis.core.statistic import ApiStatistic
from schemathesis.core.transforms import decode_pointer, get_template_fields
from schemathesis.core.transport import HTTP_METHODS_SCHEMA, is_http_method_schema
from schemathesis.filters import FilterUsage, OperationContext
from schemathesis.hooks import HookContext, dispatch_before_init_operation, dispatch_before_process_path
from schemathesis.schemas import APIOperation, OperationDefinition
from schemathesis.specs.openapi.adapter import OpenApiResponses
from schemathesis.specs.openapi.adapter.parameters import OpenApiBody, OpenApiParameter, OpenApiParameterSet
from schemathesis.specs.openapi.adapter.references import maybe_resolve_with_resolver
from schemathesis.specs.openapi.adapter.security import OpenApiSecurityParameters, has_optional_auth
from schemathesis.specs.openapi.adapter.servers import resolve_operation_base_url
from schemathesis.specs.openapi.utils import parse_spec_version

if TYPE_CHECKING:
    from schemathesis.core.adapter import ResponsesContainer
    from schemathesis.core.transport import HttpMethodSchema
    from schemathesis.specs.openapi.schemas import OpenApiSchema
    from schemathesis.specs.openapi.types import OperationObject

SCHEMA_PARSING_ERRORS = (KeyError, RefResolutionError, InvalidSchema)


def _is_utf8_encodable(value: str) -> bool:
    # Lone surrogates have no UTF-8 form, so such a path can not be sent over the wire.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


_V3_1 = version.parse("3.1")


def _is_specification_extension(path: object) -> bool:
    """Whether a `paths` key is a Specification Extension rather than a path template."""
    return isinstance(path, str) and path.startswith("x-")


def is_parsable_operation(definition: object) -> bool:
    """Whether an operation node carries something the adapters can read."""
    return isinstance(definition, dict) and bool(definition)


def parse_operation_reference(reference: str) -> tuple[str, str]:
    """Path and method an `operationRef` like `#/paths/~1users~1%7Bid%7D/get` points to."""
    _, separator, suffix = reference.partition("#/paths/")
    if not separator:
        raise OperationNotFound(f"Operation '{reference}' not found", reference)
    encoded_path, method = suffix.rsplit("/", maxsplit=1)
    # A URI fragment is percent-decoded before its JSON Pointer escapes (RFC 6901, section 6).
    return decode_pointer(unquote(encoded_path)), method


def _drop_malformed_media_types(
    items: list[OperationParameter], path: str, method: str
) -> tuple[list[OperationParameter], list[InvalidSchema]]:
    """Leave out request bodies with malformed media types while another body is still usable."""
    kept: list[OperationParameter] = []
    errors: list[InvalidSchema] = []
    has_usable_body = False
    for item in items:
        if isinstance(item, OpenApiBody):
            try:
                media_types.parse(item.media_type)
            except MalformedMediaType as exc:
                errors.append(InvalidSchema.from_malformed_media_type(exc, item.media_type, path=path, method=method))
                continue
            has_usable_body = True
        kept.append(item)
    if not has_usable_body:
        # Without a usable body the operation fails to load with the same error.
        return items, []
    return kept, errors


def _named_after_placeholder(parameters: list[OperationParameter], path: str) -> list[OperationParameter]:
    """Give a path parameter whose name is not text the placeholder it stands for.

    A document converted from YAML 1.1 spells a parameter named `on` as the boolean `true`, and the
    path template is the only place the original name survives.
    """
    unnamed = [
        parameter
        for parameter in parameters
        # A body is the other kind of parameter here, and it carries no `in`.
        if isinstance(parameter, OpenApiParameter)
        and parameter.location is ParameterLocation.PATH
        and not isinstance(parameter.definition.get("name"), str)
    ]
    if len(unnamed) != 1:
        # With several of them, no placeholder can be told from another.
        return parameters
    target = unnamed[0]
    claimed = {
        parameter.name
        for parameter in parameters
        if parameter.definition.get("in") == "path" and parameter is not target
    }
    unclaimed = [name for name in get_template_fields(path) if name not in claimed]
    if len(unclaimed) != 1:
        return parameters
    recovered = OpenApiParameter.from_definition(
        definition={**target.definition, "name": next(iter(unclaimed))},
        name_to_uri=target.name_to_uri,
        adapter=target.adapter,
    )
    return [recovered if parameter is target else parameter for parameter in parameters]


class RawSchemaVersion:
    """Bumped whenever the raw schema changes; shared by a schema and its clones, which share the raw schema."""

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = 0


class OperationLoader:
    """OpenAPI operation iteration, construction, and the statistic walk over `paths`."""

    __slots__ = ("schema", "_filter_context", "_built", "_built_at", "_lock")

    def __init__(self, schema: OpenApiSchema) -> None:
        self.schema = schema
        # Every lookup returns the same operation, so its hooks run once and its caches outlive phases.
        self._built: dict[tuple[str, str, str], APIOperation] = {}
        self._built_at = schema.raw_schema_version.value
        # Re-entrant: hooks run during a build may look up other operations.
        self._lock = threading.RLock()
        # Filter hot path mutates the shared operation's identity fields instead of allocating a fresh one per call.
        # `method` is a placeholder that gets overwritten on every `_should_skip` call.
        filter_operation: APIOperation = APIOperation(
            method=cast("HttpMethodSchema", ""),
            path="",
            label="",
            definition=OperationDefinition(raw=None),
            schema=schema,
            responses=None,
            security=None,
        )
        self._filter_context = OperationContext(operation=filter_operation)

    def iter_all(self) -> Generator[Result[APIOperation, InvalidSchema], None, None]:
        """Yield every operation as `Ok` or `Err`.

        Three `Err` shapes are possible: missing required `paths` (neither path nor method known),
        unresolved path-level reference (path known, methods unknown) and per-operation parsing failure
        (path and method known).
        """
        __tracebackhide__ = True
        schema = self.schema
        paths = schema._get_paths()
        if paths is None:
            if parse_spec_version(schema.specification.version) < _V3_1:
                yield self._into_err(KeyError("paths"), None, None)
            return

        context = HookContext()
        filters_active = not schema.filter_set.is_empty()
        should_skip = self._should_skip
        serves_schema_document = self._serves_schema_document
        iter_parameters = self._iter_parameters
        make_operation = self.make_operation
        cached = self.cached
        root_resolver = schema.root_resolver
        for path, path_item in paths.items():
            if _is_specification_extension(path):
                continue
            method = None
            try:
                if not _is_utf8_encodable(path):
                    raise InvalidSchema("Path is not valid UTF-8", path=path)
                dispatch_before_process_path(schema, context, path, path_item)
                if isinstance(path_item, dict) and "$ref" in path_item:
                    path_resolver, path_item = resolve_reference(root_resolver, path_item["$ref"])
                    scope = path_resolver.base_uri
                else:
                    path_resolver = root_resolver
                    scope = path_resolver.base_uri
                if not isinstance(path_item, dict):
                    raise InvalidSchema("Path item must be an object", path=path)
                shared_parameters = path_item.get("parameters", [])
                for method, entry in path_item.items():
                    if not is_http_method_schema(method):
                        continue
                    try:
                        if filters_active and should_skip(path, method, entry):
                            continue
                        if serves_schema_document(path, method, entry):
                            continue
                        operation = cached(scope, path, method)
                        if operation is None:
                            parameters = iter_parameters(entry, shared_parameters, resolver=path_resolver)
                            operation = make_operation(
                                path,
                                method,
                                parameters,
                                entry,
                                scope,
                                resolver=path_resolver,
                                path_item=path_item,
                            )
                        yield Ok(operation)
                    except SCHEMA_PARSING_ERRORS as exc:
                        yield self._into_err(exc, path, method)
            except SCHEMA_PARSING_ERRORS as exc:
                yield self._into_err(exc, path, method)

    def iter_operations(self) -> Iterator[tuple[str, str, dict[str, Any]]]:
        """Yield `(method, path, definition)` for each selected operation."""
        schema = self.schema
        paths = schema._get_paths()
        if paths is None:
            return
        root_resolver = schema.root_resolver
        filters_active = not schema.filter_set.is_empty()
        should_skip = self._should_skip
        for path, path_item in paths.items():
            if _is_specification_extension(path):
                continue
            try:
                if isinstance(path_item, dict) and "$ref" in path_item:
                    _, path_item = resolve_reference(root_resolver, path_item["$ref"])
                if not isinstance(path_item, dict):
                    continue
                for method, definition in path_item.items():
                    if method not in HTTP_METHODS_SCHEMA:
                        continue
                    if filters_active and should_skip(path, method, definition):
                        continue
                    if self._serves_schema_document(path, method, definition):
                        continue
                    yield method, path, definition
            except SCHEMA_PARSING_ERRORS:
                continue

    def measure_statistic(self) -> ApiStatistic:
        schema = self.schema
        statistic = ApiStatistic()
        paths = schema._get_paths()
        if paths is None:
            return statistic

        # Hoist the filter check out of the per-operation loop: when no filters are
        # configured, every operation is selected and we skip the per-call dispatch.
        filters_active = not schema.filter_set.is_empty()
        should_skip = self._should_skip
        usage = FilterUsage(schema.filter_set) if filters_active else None
        # This walk skips what `iter_all` still yields, so a filter can look dead while its operation runs.
        complete_walk = not schema.hooks.get_all_by_name("before_process_path")
        links_keyword = schema.adapter.links_keyword
        root_resolver = schema.root_resolver

        selected_operations_by_id: set[str] = set()
        selected_operations_by_path: set[tuple[str, str]] = set()
        collected_links: list[dict] = []

        for path, path_item in paths.items():
            if _is_specification_extension(path):
                continue
            try:
                if isinstance(path_item, dict) and "$ref" in path_item:
                    path_resolver, path_item = resolve_reference(root_resolver, path_item["$ref"])
                else:
                    path_resolver = root_resolver
                if not isinstance(path_item, dict):
                    complete_walk = False
                    continue
                for method, definition in path_item.items():
                    if method not in HTTP_METHODS_SCHEMA:
                        continue
                    # A malformed node is still an operation the document declares; it just cannot be parsed.
                    if not is_parsable_operation(definition):
                        complete_walk = False
                        statistic.operations.total += 1
                        continue
                    if self._serves_schema_document(path, method, definition):
                        # Keep a filter that targets it from being reported as matching nothing.
                        if usage is not None:
                            usage.record(self._filter_context)
                        continue
                    statistic.operations.total += 1
                    is_selected = not should_skip(path, method, definition) if filters_active else True
                    if usage is not None:
                        usage.record(self._filter_context)
                    if is_selected:
                        statistic.operations.selected += 1
                        if "operationId" in definition:
                            selected_operations_by_id.add(definition["operationId"])
                        selected_operations_by_path.add((method, path))
                    responses = definition.get("responses", {})
                    if not isinstance(responses, dict):
                        complete_walk = False
                        continue
                    for response in responses.values():
                        # A vendor extension key inside `responses` may carry any JSON value.
                        if not isinstance(response, dict):
                            continue
                        _, response = maybe_resolve_with_resolver(response, path_resolver)
                        defined_links = response.get(links_keyword)
                        if defined_links is not None:
                            statistic.transitions.total += len(defined_links)
                            if is_selected:
                                collected_links.extend(defined_links.values())
            except SCHEMA_PARSING_ERRORS:
                complete_walk = False
                continue

        def is_link_selected(link: object) -> bool:
            if not isinstance(link, dict):
                return False
            try:
                _, link = maybe_resolve_with_resolver(link, root_resolver)
            except (RefResolutionError, InvalidSchema):
                return False

            if "operationId" in link:
                return link["operationId"] in selected_operations_by_id
            try:
                resolve_reference(root_resolver, link["operationRef"])
                path, method = parse_operation_reference(link["operationRef"])
                return (method, path) in selected_operations_by_path
            except Exception:
                return False

        for link in collected_links:
            if is_link_selected(link):
                statistic.transitions.selected += 1

        # Hook/schema errors raised by `analyze` re-fire during engine iteration where
        # they're handled; defer to that path so the loader doesn't crash.
        try:
            descriptors = schema.analysis.resource_descriptors
            graph = schema.analysis.dependency_graph
        except (HookExecutionError, *SCHEMA_PARSING_ERRORS):
            return statistic
        selected_labels = {f"{method.upper()} {path}" for method, path in selected_operations_by_path}
        producer_labels: set[str] = set()
        resource_names: set[str] = set()
        for descriptor in descriptors:
            if descriptor.operation in selected_labels:
                producer_labels.add(descriptor.operation)
                resource_names.add(descriptor.resource_name)
        consumer_labels: set[str] = set()
        for label, operation in graph.operations.items():
            if label not in selected_labels:
                continue
            resource_bound = [slot for slot in operation.inputs if slot.resource_field is not None]
            if resource_bound:
                consumer_labels.add(label)
                resource_names.update(slot.resource.name for slot in resource_bound)
        statistic.resource_pool.producer_labels = sorted(producer_labels)
        statistic.resource_pool.consumer_labels = sorted(consumer_labels)
        statistic.resource_pool.resources = len(resource_names)

        if usage is not None and complete_walk:
            statistic.unmatched_filters = usage.results()
        return statistic

    def _should_skip(self, path: str, method: str, definition: OperationObject) -> bool:
        if not is_http_method_schema(method):
            return True
        schema = self.schema
        if schema.filter_set.is_empty():
            return False
        context = self._filter_context
        operation = context.operation
        operation.method = method
        operation.path = path
        operation.label = f"{method.upper()} {path}"
        operation.definition.raw = definition
        return not schema.filter_set.match(context)

    def _serves_schema_document(self, path: str, method: str, definition: OperationObject) -> bool:
        """Whether this operation served the schema document, and so stays out unless a filter selects it."""
        schema = self.schema
        document_path = schema._schema_document_path
        if (
            document_path is None
            or method != "get"
            or not _is_utf8_encodable(path)
            or schema.get_full_path(path).rstrip("/") != document_path
        ):
            return False
        context = self._filter_context
        operation = context.operation
        operation.method = "get"
        operation.path = path
        operation.label = f"GET {path}"
        operation.definition.raw = definition
        return not schema.filter_set.is_explicitly_included(operation)

    def _iter_parameters(
        self,
        definition: OperationObject,
        shared_parameters: Sequence[dict[str, Any]],
        resolver: Resolver | None = None,
    ) -> ParsedParameters:
        schema = self.schema
        skipped: list[SkippedParameter] = []
        items = list(
            schema.adapter.iter_parameters(
                definition,
                shared_parameters,
                schema.default_media_types,
                schema.root_resolver if resolver is None else resolver,
                schema.adapter,
                schema._bundler,
                schema._bundle_cache,
                skipped,
            )
        )
        return ParsedParameters(items=items, skipped=skipped)

    def _parse_responses(
        self, definition: OperationObject, scope: str, resolver: Resolver | None = None
    ) -> OpenApiResponses:
        schema = self.schema
        responses = definition.get("responses", {})
        return OpenApiResponses.from_definition(
            definition=responses,
            resolver=schema.root_resolver if resolver is None else resolver,
            scope=scope,
            adapter=schema.adapter,
            cache=schema.response_schema_cache,
        )

    def _parse_security(self, definition: OperationObject) -> OpenApiSecurityParameters:
        # Security schemes live at the schema root; refs in `securitySchemes` resolve relative
        # to the root document, not to whichever path-scoped resolver the operation was loaded with.
        schema = self.schema
        return OpenApiSecurityParameters.from_definition(
            schema=schema.raw_schema,
            operation=definition,
            resolver=schema.root_resolver,
            adapter=schema.adapter,
        )

    def make_operation(
        self,
        path: str,
        method: HttpMethodSchema,
        parameters: ParsedParameters,
        definition: OperationObject,
        scope: str,
        resolver: Resolver | None = None,
        path_item: Mapping[str, Any] | None = None,
    ) -> APIOperation:
        __tracebackhide__ = True
        with self._lock:
            cached = self.cached(scope, path, method)
            if cached is None:
                cached = self._built[(scope, path, method)] = self._build_operation(
                    path, method, parameters, definition, scope, resolver=resolver, path_item=path_item
                )
            return cached

    def cached(self, scope: str, path: str, method: str) -> APIOperation | None:
        """The operation built earlier, so callers can skip parsing its parameters."""
        with self._lock:
            version = self.schema.raw_schema_version.value
            if self._built_at != version:
                self._built.clear()
                self._built_at = version
            return self._built.get((scope, path, method))

    def _build_operation(
        self,
        path: str,
        method: HttpMethodSchema,
        parameters: ParsedParameters,
        definition: OperationObject,
        scope: str,
        resolver: Resolver | None = None,
        path_item: Mapping[str, Any] | None = None,
    ) -> APIOperation:
        __tracebackhide__ = True
        schema = self.schema
        if schema.config.base_url is not None:
            base_url = schema.get_base_url()
        else:
            variables = schema.config.servers.variables or None
            base_url = resolve_operation_base_url(
                operation=definition,
                path_item=path_item,
                fallback_base_url=schema.get_base_url(),
                location=schema.location,
                variables=variables,
            )
        responses = self._parse_responses(definition, scope, resolver=resolver)
        security = self._parse_security(definition)
        items, schema_errors = _drop_malformed_media_types(parameters.items, path, method)
        operation: APIOperation[OperationParameter, ResponsesContainer, OpenApiSecurityParameters, OpenApiSchema] = (
            APIOperation(
                path=path,
                method=method,
                definition=OperationDefinition(definition),
                base_url=base_url,
                app=schema.app,
                schema=schema,
                responses=responses,
                security=security,
                path_parameters=OpenApiParameterSet(ParameterLocation.PATH, adapter=schema.adapter),
                query=OpenApiParameterSet(ParameterLocation.QUERY, adapter=schema.adapter),
                headers=OpenApiParameterSet(ParameterLocation.HEADER, adapter=schema.adapter),
                cookies=OpenApiParameterSet(ParameterLocation.COOKIE, adapter=schema.adapter),
                skipped_parameters=parameters.skipped,
                schema_errors=schema_errors,
            )
        )
        for parameter in _named_after_placeholder(items, path):
            operation.add_parameter(parameter)
        declared_parameter_names = {parameter.name for parameter in operation.path_parameters}
        for name in get_template_fields(operation.path):
            if name in declared_parameter_names:
                continue
            operation.add_parameter(
                schema.adapter.build_path_parameter({"name": name, INJECTED_PATH_PARAMETER_KEY: True})
            )
        config = schema.config.generation_for(operation=operation)
        # An empty `{}` requirement documents unauthenticated access as a valid alternative. A synthesized
        # credential can only ever be rejected, so such requests are sent without one.
        if config.with_security_parameters and not has_optional_auth(schema.raw_schema, definition):
            for param in operation.security.iter_parameters():
                if operation.get_parameter(name=param.name, location=param.location) is not None:
                    continue
                operation.add_parameter(
                    OpenApiParameter.from_definition(
                        definition=dict(param.definition), name_to_uri={}, adapter=schema.adapter
                    )
                )
        dispatch_before_init_operation(schema, HookContext(operation=operation), operation)
        # After the hooks: a body they converted keeps its own copies, so their changes stay with this operation.
        for body in operation.body:
            if isinstance(body, OpenApiBody):
                body.share_converted_schemas(schema.request_body_schema_cache)
        return operation

    def clear_cache(self) -> None:
        """Forget operations built by this schema and its clones after the raw schema changed under them."""
        with self._lock:
            self.schema.raw_schema_version.value += 1

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
        schema = self.schema
        if isinstance(error, RefResolutionError):
            raise InvalidSchema.from_reference_resolution_error(error, path=path, method=method) from None
        try:
            schema.validate()
        except jsonschema_rs.ValidationError as exc:
            raise InvalidSchema.from_jsonschema_error(
                exc,
                path=path,
                method=method,
                config=schema.config.output,
                location=SchemaLocation.maybe_from_error_path(exc.instance_path, schema.specification.version),
            ) from None
        if isinstance(error, InvalidSchema):
            error.path = error.path or path
            error.method = error.method or method
            raise error
        raise InvalidSchema(SCHEMA_ERROR_SUGGESTION, path=path, method=method) from error
