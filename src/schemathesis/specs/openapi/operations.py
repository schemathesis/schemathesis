from __future__ import annotations

from collections.abc import Generator, Iterator, Sequence
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NoReturn

import jsonschema_rs
from packaging import version

from schemathesis.core import INJECTED_PATH_PARAMETER_KEY
from schemathesis.core.adapter import OperationParameter
from schemathesis.core.compat import RefResolutionError
from schemathesis.core.errors import (
    SCHEMA_ERROR_SUGGESTION,
    HookExecutionError,
    InfiniteRecursiveReference,
    InvalidSchema,
    SchemaLocation,
)
from schemathesis.core.jsonschema.resolver import Resolver, resolve_reference
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.result import Err, Ok, Result
from schemathesis.core.statistic import ApiStatistic
from schemathesis.core.transforms import get_template_fields
from schemathesis.hooks import HookContext, dispatch_before_init_operation, dispatch_before_process_path
from schemathesis.schemas import APIOperation, OperationDefinition
from schemathesis.specs.openapi.adapter import OpenApiResponses
from schemathesis.specs.openapi.adapter.parameters import OpenApiParameter, OpenApiParameterSet
from schemathesis.specs.openapi.adapter.security import OpenApiSecurityParameters

if TYPE_CHECKING:
    from schemathesis.core.adapter import ResponsesContainer
    from schemathesis.specs.openapi.schemas import OpenApiSchema

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace", "query"})
SCHEMA_PARSING_ERRORS = (KeyError, AttributeError, RefResolutionError, InvalidSchema, InfiniteRecursiveReference)

_V3_1 = version.parse("3.1")


class OperationLoader:
    """Owns OpenAPI operation iteration, construction, and the statistic walk over `paths`."""

    __slots__ = ("schema",)

    def __init__(self, schema: OpenApiSchema) -> None:
        self.schema = schema

    def iter_all(self) -> Generator[Result[APIOperation, InvalidSchema], None, None]:
        """Yield every operation as `Ok` or `Err`.

        Two `Err` shapes are possible: unresolved path-level reference (path known, methods unknown)
        and per-operation parsing failure (path and method known).
        """
        __tracebackhide__ = True
        schema = self.schema
        paths = schema._get_paths()
        if paths is None:
            if version.parse(schema.specification.version) >= _V3_1:
                return
            self._raise_invalid_schema(KeyError("paths"))

        context = HookContext()
        filters_active = not schema.filter_set.is_empty()
        should_skip = self._should_skip
        iter_parameters = self._iter_parameters
        make_operation = self.make_operation
        root_resolver = schema.root_resolver
        for path, path_item in paths.items():
            method = None
            try:
                dispatch_before_process_path(schema, context, path, path_item)
                if "$ref" in path_item:
                    path_resolver, path_item = resolve_reference(root_resolver, path_item["$ref"])
                    scope = path_resolver.base_uri
                else:
                    path_resolver = root_resolver
                    scope = path_resolver.base_uri
                shared_parameters = path_item.get("parameters", [])
                for method, entry in path_item.items():
                    if method not in HTTP_METHODS:
                        continue
                    try:
                        if filters_active and should_skip(path, method, entry):
                            continue
                        parameters = iter_parameters(entry, shared_parameters, resolver=path_resolver)
                        operation = make_operation(
                            path,
                            method,
                            parameters,
                            entry,
                            scope,
                            resolver=path_resolver,
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
            try:
                if "$ref" in path_item:
                    _, path_item = resolve_reference(root_resolver, path_item["$ref"])
                for method, definition in path_item.items():
                    if method not in HTTP_METHODS:
                        continue
                    if filters_active and should_skip(path, method, definition):
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
        links_keyword = schema.adapter.links_keyword
        root_resolver = schema.root_resolver

        selected_operations_by_id: set[str] = set()
        selected_operations_by_path: set[tuple[str, str]] = set()
        collected_links: list[dict] = []

        for path, path_item in paths.items():
            try:
                if "$ref" in path_item:
                    path_resolver, path_item = resolve_reference(root_resolver, path_item["$ref"])
                else:
                    path_resolver = root_resolver
                for method, definition in path_item.items():
                    if method not in HTTP_METHODS or not definition:
                        continue
                    statistic.operations.total += 1
                    is_selected = not should_skip(path, method, definition) if filters_active else True
                    if is_selected:
                        statistic.operations.selected += 1
                        if "operationId" in definition:
                            selected_operations_by_id.add(definition["operationId"])
                        selected_operations_by_path.add((method, path))
                    for response in definition.get("responses", {}).values():
                        if "$ref" in response:
                            _, response = resolve_reference(path_resolver, response["$ref"])
                        defined_links = response.get(links_keyword)
                        if defined_links is not None:
                            statistic.transitions.total += len(defined_links)
                            if is_selected:
                                collected_links.extend(defined_links.values())
            except SCHEMA_PARSING_ERRORS:
                continue

        def is_link_selected(link: dict) -> bool:
            if "$ref" in link:
                _, link = resolve_reference(root_resolver, link["$ref"])

            if "operationId" in link:
                return link["operationId"] in selected_operations_by_id
            try:
                resolve_reference(root_resolver, link["operationRef"])
                _, _, suffix = link["operationRef"].partition("#/paths/")
                path, method = suffix.rsplit("/", maxsplit=1)
                path = path.replace("~1", "/").replace("~0", "~")
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

        return statistic

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
                schema=None,
                responses=None,
                security=None,
            )
        ),
    ) -> bool:
        if method not in HTTP_METHODS:
            return True
        schema = self.schema
        if schema.filter_set.is_empty():
            return False
        # Attribute assignment is way faster than creating a new namespace every time
        operation = _ctx_cache.operation
        operation.method = method
        operation.path = path
        operation.label = f"{method.upper()} {path}"
        operation.definition.raw = definition
        operation.schema = schema
        return not schema.filter_set.match(_ctx_cache)

    def _iter_parameters(
        self,
        definition: dict[str, Any],
        shared_parameters: Sequence[dict[str, Any]],
        resolver: Resolver | None = None,
    ) -> list[OperationParameter]:
        schema = self.schema
        return list(
            schema.adapter.iter_parameters(
                definition,
                shared_parameters,
                schema.default_media_types,
                schema.root_resolver if resolver is None else resolver,
                schema.adapter,
                schema._bundler,
                schema._bundle_cache,
            )
        )

    def _parse_responses(
        self, definition: dict[str, Any], scope: str, resolver: Resolver | None = None
    ) -> OpenApiResponses:
        schema = self.schema
        responses = definition.get("responses", {})
        return OpenApiResponses.from_definition(
            definition=responses,
            resolver=schema.root_resolver if resolver is None else resolver,
            scope=scope,
            adapter=schema.adapter,
        )

    def _parse_security(self, definition: dict[str, Any]) -> OpenApiSecurityParameters:
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
        method: str,
        parameters: list[OperationParameter],
        definition: dict[str, Any],
        scope: str,
        resolver: Resolver | None = None,
    ) -> APIOperation:
        __tracebackhide__ = True
        schema = self.schema
        base_url = schema.get_base_url()
        responses = self._parse_responses(definition, scope, resolver=resolver)
        security = self._parse_security(definition)
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
            )
        )
        for parameter in parameters:
            operation.add_parameter(parameter)
        missing_parameter_names = get_template_fields(operation.path) - {
            parameter.name for parameter in operation.path_parameters
        }
        for name in missing_parameter_names:
            operation.add_parameter(
                schema.adapter.build_path_parameter({"name": name, INJECTED_PATH_PARAMETER_KEY: True})
            )
        config = schema.config.generation_for(operation=operation)
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
                    OpenApiParameter.from_definition(definition=param, name_to_uri={}, adapter=schema.adapter)
                )
        dispatch_before_init_operation(schema, HookContext(operation=operation), operation)
        return operation

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
        if isinstance(error, InfiniteRecursiveReference):
            raise InvalidSchema(str(error), path=path, method=method) from None
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
        raise InvalidSchema(SCHEMA_ERROR_SUGGESTION, path=path, method=method) from error
