from __future__ import annotations

from collections.abc import Callable, Generator, Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from difflib import get_close_matches
from functools import cached_property
from typing import TYPE_CHECKING, Any, NoReturn, cast

from packaging import version
from requests.structures import CaseInsensitiveDict
from typing_extensions import override

from schemathesis.config import (
    CoveragePhaseConfig,
    ExamplesPhaseConfig,
    FuzzingPhaseConfig,
    OperationOrdering,
)
from schemathesis.core import NOT_SET, Body, Specification
from schemathesis.core.errors import (
    InvalidSchema,
    OperationNotFound,
)
from schemathesis.core.jsonschema import Bundler
from schemathesis.core.jsonschema.bundler import BundleCache
from schemathesis.core.jsonschema.resolver import Resolver, make_root_resolver, resolve_reference
from schemathesis.core.result import Err, Ok, Result
from schemathesis.core.spec import CoverageCapabilities
from schemathesis.core.statistic import ApiStatistic
from schemathesis.core.transport import HttpMethod, HttpMethodSchema, Response, restful_method_priority
from schemathesis.engine.link_calibration import LinkCalibrationState
from schemathesis.generation.case import Case
from schemathesis.generation.meta import CaseMetadata, ComponentInfo
from schemathesis.resources import ExtraDataSource
from schemathesis.specs.openapi import adapter
from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter
from schemathesis.specs.openapi.adapter.security import OpenApiSecurity
from schemathesis.specs.openapi.analysis import OpenAPIAnalysis

from ...generation import GenerationMode
from ...hooks import (
    HookContext,
    HookDispatcher,
    dispatch_before_process_path,
)
from ...schemas import APIOperation, APIOperationMap, BaseSchema
from ._hypothesis import openapi_cases
from ._operation_lookup import OperationLookup
from .examples import get_strategies_from_examples
from .operations import HTTP_METHODS, SCHEMA_PARSING_ERRORS, OperationLoader
from .stateful import create_state_machine
from .validation import ResponseValidator

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import TypeAlias

    from hypothesis.strategies import SearchStrategy

    from schemathesis.auths import AuthContext, AuthStorage
    from schemathesis.config import GenerationConfig
    from schemathesis.core.adapter import OperationParameter
    from schemathesis.core.cache import CacheWriter
    from schemathesis.core.error_feedback import ErrorFeedbackStore
    from schemathesis.core.schema_analysis import SchemaWarning
    from schemathesis.core.spec import ApiSchema
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.engine.run import Phase
    from schemathesis.engine.run.unit._layered_scheduler import LayeredScheduler
    from schemathesis.engine.run.unit._pool import DefaultScheduler
    from schemathesis.generation.stateful import APIStateMachine
    from schemathesis.specs.openapi.adapter import OpenApiResponses
    from schemathesis.specs.openapi.adapter.parameters import OpenApiParameter
    from schemathesis.specs.openapi.adapter.security import OpenApiSecurityParameters, SecurityRequirements
    from schemathesis.specs.openapi.types import OperationObject

    OpenApiOperation: TypeAlias = APIOperation[
        OpenApiParameter, OpenApiResponses, OpenApiSecurityParameters, "OpenApiSchema"
    ]
    OpenApiCase: TypeAlias = Case[OpenApiOperation]

_V3_1 = version.parse("3.1")
_V3_2 = version.parse("3.2")


@dataclass(eq=False, repr=False)
class OpenApiSchema(BaseSchema):
    adapter: SpecificationAdapter = None  # type: ignore[assignment]
    _spec_version: str = field(init=False)

    @override
    def __post_init__(self) -> None:
        self._initialize_adapter()
        super().__post_init__()
        self.analysis = OpenAPIAnalysis(self)
        self._bundler = Bundler()
        self._bundle_cache: BundleCache = {}
        self._operation_lookup = OperationLookup(self, HTTP_METHODS)
        self._operations = OperationLoader(self)
        self._response_validator = ResponseValidator(self)
        # Path-level dedup of undeclared-method coverage probes; cleared per coverage phase via
        # `reset_coverage_state`.
        self.coverage_unexpected_methods_seen: set[tuple[str, str]] = set()
        # Per-operation security overlays populated by runtime auth inference. Empty when the server
        # never enforces auth on a declared-public operation; otherwise generations consult this
        # instead of mutating the parsed spec.
        self._inferred_security: dict[str, SecurityRequirements] = {}

    def _initialize_adapter(self) -> None:
        swagger_version = self.raw_schema.get("swagger")
        if swagger_version is not None:
            self._spec_version = swagger_version or "2.0"
            self.adapter = adapter.v2
            return

        openapi_version = self.raw_schema.get("openapi")
        if openapi_version is not None:
            self._spec_version = openapi_version
            parsed_version = version.parse(openapi_version)
            if parsed_version >= _V3_2:
                self.adapter = adapter.v3_2
            elif parsed_version >= _V3_1:
                self.adapter = adapter.v3_1
            else:
                self.adapter = adapter.v3_0
            return

        raise InvalidSchema("Unable to determine Open API version for this schema.")

    @override
    @cached_property
    def specification(self) -> Specification:
        return Specification.openapi(version=self._spec_version)

    @cached_property
    def security(self) -> OpenApiSecurity:
        return OpenApiSecurity(raw_schema=self.raw_schema, adapter=self.adapter, resolver=self.root_resolver)

    @override
    def is_security_param_negated(self, case: Case) -> bool:
        return self.security.is_security_param_negated(case)

    @override
    def apply_auth(self, case: Case, context: AuthContext) -> bool:
        """Apply OpenAPI-aware authentication to a test case.

        Returns True if authentication was applied, False otherwise.
        """
        all_schemes = self.config.auth.all_openapi_schemes
        if not all_schemes:
            return False
        return self.security.apply_auth(case, context, all_schemes)

    @override
    def create_extra_data_source(self) -> ExtraDataSource | None:
        """Create an extra data source for augmenting test generation with real data.

        Returns:
            OpenApiExtraDataSource if resource descriptors are available, None otherwise.

        """
        return self.analysis.extra_data_source

    @override
    def get_coverage_capabilities(self) -> CoverageCapabilities:
        from schemathesis.specs.openapi.formats import STRING_FORMATS, get_default_format_strategies
        from schemathesis.specs.openapi.patterns import update_quantifier

        return CoverageCapabilities(
            format_strategies={**get_default_format_strategies(), **STRING_FORMATS},
            update_pattern=update_quantifier,
            validator_cls=self.adapter.jsonschema_validator_cls,
        )

    @override
    def reset_coverage_state(self) -> None:
        self.coverage_unexpected_methods_seen.clear()

    @override
    def record_runtime_observations(
        self,
        *,
        store: ErrorFeedbackStore,
        recorder: ScenarioRecorder,
        case: Case,
        response: Response,
        transport_kwargs: dict[str, Any],
        cache_writer: CacheWriter | None = None,
    ) -> None:
        from schemathesis.specs.openapi.auth_inference import record_auth_inference

        record_auth_inference(
            store=store,
            recorder=recorder,
            case=case,
            response=response,
            transport_kwargs=transport_kwargs,
            cache_writer=cache_writer,
        )

    @override
    def iter_coverage_cases(
        self,
        operation: APIOperation,
        *,
        generation_modes: list[GenerationMode],
        generation_config: GenerationConfig,
        extra_data_source: ExtraDataSource | None = None,
        error_feedback: ErrorFeedbackStore | None = None,
    ) -> Iterator[Case]:
        from schemathesis.specs.openapi.coverage._operation import iter_coverage_cases

        phases_config = self.config.phases_for(operation=operation)
        return iter_coverage_cases(
            operation=operation,
            generation_modes=generation_modes,
            generate_duplicate_query_parameters=phases_config.coverage.generate_duplicate_query_parameters,
            unexpected_methods=phases_config.coverage.unexpected_methods,
            generation_config=generation_config,
            extra_data_source=extra_data_source,
            unexpected_methods_seen=self.coverage_unexpected_methods_seen,
            error_feedback=error_feedback,
        )

    @override
    def revalidate_case_metadata(self, case: Case) -> None:
        meta = case._meta
        if meta is None or not meta.is_dirty():
            return
        validator_cls = self.adapter.jsonschema_validator_cls
        for location in list(meta._dirty):
            value = getattr(case, location.container_name)
            current_hash = case._hash_container(value)
            raw = meta.raw_containers.get(location)
            # When the container still equals its generated form, validate the typed
            # snapshot — coverage stringifies query/path values for the wire and the
            # validation schema is expressed in the typed form.
            if current_hash == meta._initial_hashes.get(location) and raw is not None:
                validation_value = raw
            elif isinstance(value, Mapping) and isinstance(raw, Mapping):
                # Auth/overrides add keys after generation; validate generated keys against the typed
                # snapshot (query/path serialize to strings) and only added keys against the live value.
                validation_value = {key: raw[key] if key in raw else current for key, current in value.items()}
            else:
                validation_value = value
            is_valid = case._validate_component(location, validation_value, validator_cls)
            if location in meta.components:
                new_mode = GenerationMode.POSITIVE if is_valid else GenerationMode.NEGATIVE
                meta.components[location] = ComponentInfo(mode=new_mode)
            meta.update_validated_hash(location, current_hash)
            meta.clear_dirty(location)
        if meta.components:
            if all(info.mode.is_positive for info in meta.components.values()):
                meta.generation.mode = GenerationMode.POSITIVE
            else:
                meta.generation.mode = GenerationMode.NEGATIVE

    @override
    def as_state_machine(self) -> type[APIStateMachine]:
        return self._build_state_machine(error_feedback=None, link_calibration=None, extra_data_source=None)

    @override
    def _build_state_machine(
        self,
        *,
        error_feedback: ErrorFeedbackStore | None,
        link_calibration: LinkCalibrationState | None,
        extra_data_source: ExtraDataSource | None,
    ) -> type[APIStateMachine]:
        # Apply dependency inference if configured and not already done
        if self.analysis.should_inject_links():
            self.analysis.inject_links()
        return create_state_machine(
            self,
            error_feedback=error_feedback,
            link_calibration=link_calibration,
            extra_data_source=extra_data_source,
        )

    @override
    def get_unit_scheduler(
        self,
        operations: list[Result[APIOperation, InvalidSchema]],
        phase: Phase,
    ) -> DefaultScheduler | LayeredScheduler:
        from schemathesis.engine.run.unit._layered_scheduler import LayeredScheduler
        from schemathesis.engine.run.unit._pool import DefaultScheduler, split_results
        from schemathesis.specs.openapi._ordering import compute_operation_layers

        phase_config = self.config.phases.get_by_name(name=phase.name.name)
        assert isinstance(phase_config, FuzzingPhaseConfig | CoveragePhaseConfig | ExamplesPhaseConfig)
        if phase_config.operation_ordering == OperationOrdering.NONE:
            return DefaultScheduler(operations=operations)

        successes, errors = split_results(operations)
        if not successes:
            return DefaultScheduler(operations=operations)

        layers = compute_operation_layers(self, successes)

        if not layers:
            return DefaultScheduler(operations=operations)

        if len(layers) == 1:
            # Stable-sort by RESTful priority so producers dispatch before consumers
            # without reordering same-priority operations against each other.
            ordered_successes = sorted(successes, key=lambda op: restful_method_priority(op.method))
            ordered: list[Result[APIOperation, InvalidSchema]] = [Ok(op) for op in ordered_successes]
            ordered.extend(Err(err) for err in errors)
            return DefaultScheduler(operations=ordered)

        return LayeredScheduler(layers, errors=errors)

    @override
    def apply_stateful_inference(self, ctx: EngineContext) -> int:
        injected = 0
        if ctx.observations is not None and ctx.observations.location_headers:
            for operation, entries in ctx.observations.location_headers.items():
                injected += self.analysis.inferencer.inject_links(operation.responses, entries)
        if self.analysis.should_inject_links():
            injected += self.analysis.inject_links()
        return injected

    @override
    def compute_fuzz_operation_weights(self, operations: list[APIOperation]) -> dict[str, int]:
        layers = self.analysis.dependency_layers
        if layers is None:
            return {op.label: 1 for op in operations}

        layer_0_labels = set(layers[0])
        graph = self.analysis.dependency_graph

        weights: dict[str, int] = {}
        for op in operations:
            if op.label not in layer_0_labels:
                weights[op.label] = 1
            else:
                node = graph.operations.get(op.label)
                # Path-keyed and body-keyed outputs don't contribute response-body
                # values to the resource pool, so they shouldn't bias fuzz scheduling weights.
                out_degree = (
                    sum(1 for output in node.outputs if output.path_parameter is None and output.body_field is None)
                    if node is not None
                    else 0
                )
                weights[op.label] = 2 + out_degree
        return weights

    @override
    def iter_link_candidates(
        self,
        *,
        operation: APIOperation,
        case: Case,
        response: Response,
        operations_by_label: dict[str, APIOperation],
        excluded_labels: set[str],
    ) -> list[tuple[APIOperation, dict[str, Any]]]:
        from schemathesis.specs.openapi.stateful._link_chooser import collect_link_candidates

        return collect_link_candidates(
            operation=operation,
            case=case,
            response=response,
            operations_by_label=operations_by_label,
            excluded_labels=excluded_labels,
        )

    @override
    def iter_schema_warnings(self) -> list[SchemaWarning]:
        return list(self.analysis.iter_warnings())

    @override
    def adapt_to_null_byte_in_header_failure(self) -> None:
        from schemathesis.specs.openapi import formats
        from schemathesis.specs.openapi.formats import (
            DEFAULT_HEADER_EXCLUDE_CHARACTERS,
            HEADER_FORMAT,
            header_values,
        )

        formats.register_string_format(
            HEADER_FORMAT, header_values(exclude_characters=DEFAULT_HEADER_EXCLUDE_CHARACTERS + "\x00")
        )

    @override
    def get_custom_format_strategies(
        self, generation_config: GenerationConfig, mode: GenerationMode
    ) -> dict[str, SearchStrategy]:
        from schemathesis.specs.openapi._hypothesis import _build_custom_formats

        return _build_custom_formats(generation_config, mode)

    def __repr__(self) -> str:
        info = self.raw_schema["info"]
        return f"<{self.__class__.__name__} for {info['title']} {info['version']}>"

    @override
    def __iter__(self) -> Iterator[str]:
        paths = self._get_paths()
        if paths is None:
            return iter(())
        return iter(paths)

    @cached_property
    def default_media_types(self) -> list[str]:
        return self.adapter.get_default_media_types(self.raw_schema)

    @override
    def _get_base_path(self) -> str:
        return self.adapter.get_base_path(self.raw_schema)

    def _get_paths(self) -> Mapping[str, Any] | None:
        paths = self.raw_schema.get("paths")
        if paths is None:
            return None
        assert isinstance(paths, Mapping)
        return cast(Mapping[str, Any], paths)

    @override
    def _get_operation_map(self, path: str) -> APIOperationMap:
        paths = self._get_paths()
        if paths is None:
            raise KeyError(path)
        path_item = paths[path]
        if "$ref" in path_item:
            path_resolver, path_item = resolve_reference(self.root_resolver, path_item["$ref"])
            scope = path_resolver.base_uri
        else:
            path_resolver = self.root_resolver
            scope = path_resolver.base_uri
        dispatch_before_process_path(self, HookContext(), path, path_item)
        map = APIOperationMap(self, {})
        map._data = MethodMap(map, path_resolver, scope, path, CaseInsensitiveDict(path_item))
        return map

    @override
    def find_operation_by_label(self, label: str) -> APIOperation | None:
        try:
            method, path = label.split(" ", maxsplit=1)
            return self[path][method]
        except (OperationNotFound, ValueError):
            return None

    @override
    def on_missing_operation(self, item: str, exc: KeyError) -> NoReturn:
        matches = get_close_matches(item, list(self))
        self._on_missing_operation(item, exc, matches)

    def _on_missing_operation(self, item: str, exc: KeyError | None, matches: list[str]) -> NoReturn:
        message = f"`{item}` not found"
        if matches:
            message += f". Did you mean `{matches[0]}`?"
        raise OperationNotFound(message=message, item=item) from exc

    def _should_skip(self, path: str, method: str, definition: OperationObject) -> bool:
        return self._operations._should_skip(path, method, definition)

    @override
    def _measure_statistic(self) -> ApiStatistic:
        return self._operations.measure_statistic()

    @override
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
        yield from self._operations.iter_all()

    def _raise_invalid_schema(
        self,
        error: Exception,
        path: str | None = None,
        method: str | None = None,
    ) -> NoReturn:
        __tracebackhide__ = True
        self._operations._raise_invalid_schema(error, path, method)

    @override
    def validate(self) -> None:
        with suppress(TypeError):
            self._validate()

    def _validate(self) -> None:
        self.adapter.validate_schema(self.raw_schema)

    def _iter_parameters(
        self,
        definition: OperationObject,
        shared_parameters: Sequence[dict[str, Any]],
        resolver: Resolver | None = None,
    ) -> list[OperationParameter]:
        return self._operations._iter_parameters(definition, shared_parameters, resolver=resolver)

    def _parse_responses(
        self, definition: OperationObject, scope: str, resolver: Resolver | None = None
    ) -> OpenApiResponses:
        return self._operations._parse_responses(definition, scope, resolver=resolver)

    def _parse_security(self, definition: OperationObject) -> OpenApiSecurityParameters:
        return self._operations._parse_security(definition)

    def make_operation(
        self,
        path: str,
        method: HttpMethodSchema,
        parameters: list[OperationParameter],
        definition: OperationObject,
        scope: str,
        resolver: Resolver | None = None,
        path_item: Mapping[str, Any] | None = None,
    ) -> APIOperation:
        __tracebackhide__ = True
        return self._operations.make_operation(
            path, method, parameters, definition, scope, resolver=resolver, path_item=path_item
        )

    @cached_property
    def root_resolver(self) -> Resolver:
        return make_root_resolver(self.raw_schema, location=self.location)

    def get_content_types(self, operation: APIOperation, response: Response) -> list[str]:
        """Content types available for this API operation."""
        return self.adapter.get_response_content_types(operation, response)

    @override
    def get_request_payload_content_types(self, operation: APIOperation) -> list[str]:
        return self.adapter.get_request_payload_content_types(operation)

    @override
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

    @override
    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        generation_mode: GenerationMode = GenerationMode.POSITIVE,
        extra_data_source: ExtraDataSource | None = None,
        error_feedback: ErrorFeedbackStore | None = None,
        **kwargs: Any,
    ) -> SearchStrategy:
        return openapi_cases(
            operation=operation,
            hooks=hooks,
            auth_storage=auth_storage,
            generation_mode=generation_mode,
            extra_data_source=extra_data_source,
            error_feedback=error_feedback,
            **kwargs,
        )

    @override
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

    @override
    def get_tags(self, operation: APIOperation) -> list[str] | None:
        return operation.definition.raw.get("tags")

    @override
    def prepare_multipart(
        self, form_data: dict[str, Any], operation: APIOperation, selected_content_types: dict[str, str] | None = None
    ) -> tuple[list | None, dict[str, Any] | None]:
        return self.adapter.prepare_multipart(operation, form_data, selected_content_types)

    @override
    def make_case(
        self,
        *,
        operation: APIOperation,
        method: HttpMethod | None = None,
        path: str | None = None,
        path_parameters: dict[str, Any] | None = None,
        headers: dict[str, Any] | CaseInsensitiveDict | None = None,
        cookies: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: Body = NOT_SET,
        media_type: str | None = None,
        multipart_content_types: dict[str, str] | None = None,
        meta: CaseMetadata | None = None,
    ) -> Case:
        if body is not NOT_SET and media_type is None:
            media_type = operation._get_default_media_type()
        return Case(
            operation=operation,
            method=method or cast("HttpMethod", operation.method.upper()),
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

    @override
    def validate_response(
        self,
        operation: APIOperation,
        response: Response,
        *,
        case: Case | None = None,
    ) -> bool | None:
        __tracebackhide__ = True
        return self._response_validator.validate(operation, response, case=case)


@dataclass
class MethodMap(Mapping):
    """Container for accessing API operations.

    Provides a more specific error message if API operation is not found.
    """

    _parent: APIOperationMap
    _resolver: Resolver
    # Reference resolution scope
    _scope: str
    # Methods are stored for this path
    _path: str
    # Storage for definitions
    _path_item: CaseInsensitiveDict

    __slots__ = ("_parent", "_resolver", "_scope", "_path", "_path_item")

    def __len__(self) -> int:
        return len(self._path_item)

    def __iter__(self) -> Iterator[str]:
        return iter(self._path_item)

    def _init_operation(self, method: str) -> APIOperation:
        method = method.lower()
        operation = self._path_item[method]
        schema = cast(OpenApiSchema, self._parent._schema)
        path = self._path
        try:
            parameters = schema._iter_parameters(
                operation, self._path_item.get("parameters", []), resolver=self._resolver
            )
        except SCHEMA_PARSING_ERRORS as exc:
            schema._raise_invalid_schema(exc, path, method)
        return schema.make_operation(
            path,
            cast("HttpMethodSchema", method),
            parameters,
            operation,
            self._scope,
            resolver=self._resolver,
            path_item=self._path_item,
        )

    def __getitem__(self, item: str) -> APIOperation:
        try:
            return self._init_operation(item)
        except LookupError as exc:
            available_methods = ", ".join(key.upper() for key in self if key in HTTP_METHODS)
            message = f"Method `{item.upper()}` not found."
            if available_methods:
                message += f" Available methods: {available_methods}"
            raise LookupError(message) from exc


if TYPE_CHECKING:
    # Verify structural conformance to the spec-agnostic protocol; mypy fails here
    # if a method is renamed or its signature drifts from `ApiSchema`.
    def _verify_api_schema_protocol(schema: OpenApiSchema) -> ApiSchema:
        return schema
