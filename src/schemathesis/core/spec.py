from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator

    import jsonschema_rs
    from hypothesis.strategies import SearchStrategy
    from requests.structures import CaseInsensitiveDict

    from schemathesis.auths import AuthContext, AuthStorage
    from schemathesis.config import GenerationConfig, ProjectConfig
    from schemathesis.core import Body, Specification
    from schemathesis.core.error_feedback import ErrorFeedbackStore
    from schemathesis.core.errors import InvalidSchema
    from schemathesis.core.jsonschema.types import JsonSchemaObject
    from schemathesis.core.result import Result
    from schemathesis.core.schema_analysis import SchemaWarning
    from schemathesis.core.transport import Response
    from schemathesis.generation import GenerationMode
    from schemathesis.generation.case import Case
    from schemathesis.generation.meta import CaseMetadata
    from schemathesis.generation.stateful.state_machine import APIStateMachine
    from schemathesis.hooks import HookDispatcher
    from schemathesis.resources import ExtraDataSource
    from schemathesis.schemas import APIOperation


@dataclass
class CoverageCapabilities:
    """Coverage-phase data the engine asks of a specification.

    Specs that do not participate in coverage generation can return an empty instance
    (`format_strategies={}`, `update_pattern=None`, `validator_cls=None`).
    """

    format_strategies: dict[str, SearchStrategy[Any]]
    update_pattern: Callable[[str, int | None, int | None], str] | None
    validator_cls: type[jsonschema_rs.Validator] | None


class SchemaMetadata(Protocol):
    """Shared schema-level state and identity: source, location, configuration, and hook surface."""

    config: ProjectConfig
    hooks: HookDispatcher
    auth: AuthStorage
    raw_schema: JsonSchemaObject
    location: str | None

    @property
    def specification(self) -> Specification: ...  # pragma: no cover

    def validate(self) -> None: ...  # pragma: no cover

    def get_base_url(self) -> str: ...  # pragma: no cover

    def get_local_hook_dispatcher(self) -> HookDispatcher | None: ...  # pragma: no cover


class SchemaWarnings(Protocol):
    """Static-analysis warnings collected from the schema."""

    def iter_schema_warnings(self) -> list[SchemaWarning]: ...  # pragma: no cover


class OperationsProvider(Protocol):
    """Operation enumeration and lookup."""

    def get_all_operations(self) -> Generator[Result[APIOperation, InvalidSchema], None, None]: ...  # pragma: no cover

    def find_operation_by_label(self, label: str) -> APIOperation | None: ...  # pragma: no cover

    def on_missing_operation(self, item: str, exc: KeyError) -> NoReturn: ...  # pragma: no cover

    def get_tags(self, operation: APIOperation) -> list[str] | None: ...  # pragma: no cover


class CaseFactory(Protocol):
    """Test-case construction for an operation."""

    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: HookDispatcher | None = ...,
        auth_storage: AuthStorage | None = ...,
        generation_mode: GenerationMode = ...,
        **kwargs: Any,
    ) -> SearchStrategy[Case]: ...  # pragma: no cover

    def make_case(
        self,
        *,
        operation: APIOperation,
        method: str | None = ...,
        path: str | None = ...,
        path_parameters: dict[str, Any] | None = ...,
        headers: dict[str, Any] | CaseInsensitiveDict | None = ...,
        cookies: dict[str, Any] | None = ...,
        query: dict[str, Any] | None = ...,
        body: Body = ...,
        media_type: str | None = ...,
        multipart_content_types: dict[str, str] | None = ...,
        meta: CaseMetadata | None = ...,
    ) -> Case: ...  # pragma: no cover

    def get_strategies_from_examples(
        self, operation: APIOperation, **kwargs: Any
    ) -> list[SearchStrategy[Case]]: ...  # pragma: no cover

    def get_custom_format_strategies(
        self, generation_config: GenerationConfig, mode: GenerationMode
    ) -> dict[str, SearchStrategy]: ...  # pragma: no cover

    def revalidate_case_metadata(self, case: Case) -> None: ...  # pragma: no cover


class CoverageBackend(Protocol):
    """Coverage-phase generation surface."""

    def get_coverage_capabilities(self) -> CoverageCapabilities: ...  # pragma: no cover

    def reset_coverage_state(self) -> None: ...  # pragma: no cover

    def iter_coverage_cases(
        self,
        operation: APIOperation,
        *,
        generation_modes: list[GenerationMode],
        generation_config: GenerationConfig,
        extra_data_source: ExtraDataSource | None = ...,
        error_feedback: ErrorFeedbackStore | None = ...,
    ) -> Iterator[Case]: ...  # pragma: no cover


class StatefulBackend(Protocol):
    """Stateful-phase surface: state machine, link candidates, fuzz weights."""

    def as_state_machine(self) -> type[APIStateMachine]: ...  # pragma: no cover

    def iter_link_candidates(
        self,
        *,
        operation: APIOperation,
        case: Case,
        response: Response,
        operations_by_label: dict[str, APIOperation],
        excluded_labels: set[str],
    ) -> list[tuple[APIOperation, dict[str, Any]]]: ...  # pragma: no cover

    def compute_fuzz_operation_weights(self, operations: list[APIOperation]) -> dict[str, int]: ...  # pragma: no cover


class TransportShape(Protocol):
    """Wire-level transformations: URL construction, body preparation, response validation."""

    def validate_response(
        self,
        operation: APIOperation,
        response: Response,
        *,
        case: Case | None = ...,
    ) -> bool | None: ...  # pragma: no cover

    def build_request_url(self, case: Case, base_url: str) -> str: ...  # pragma: no cover

    def prepare_request_body(self, body: Body) -> Body: ...  # pragma: no cover

    def prepare_multipart(
        self,
        form_data: dict[str, Any],
        operation: APIOperation,
        selected_content_types: dict[str, str] | None = ...,
    ) -> tuple[list | None, dict[str, Any] | None]: ...  # pragma: no cover

    def get_parameter_serializer(
        self, operation: APIOperation, location: str
    ) -> Callable | None: ...  # pragma: no cover

    def get_request_payload_content_types(self, operation: APIOperation) -> list[str]: ...  # pragma: no cover


class ProbeAdapter(Protocol):
    """Hooks the engine invokes after probes uncover server behavior."""

    def create_extra_data_source(self) -> ExtraDataSource | None: ...  # pragma: no cover

    def adapt_to_null_byte_in_header_failure(self) -> None: ...  # pragma: no cover

    def adapt_to_path_decoder_rejection(self) -> None: ...  # pragma: no cover


class AuthBackend(Protocol):
    """Spec-aware authentication for generated cases."""

    def apply_auth(self, case: Case, context: AuthContext) -> bool: ...  # pragma: no cover


class ApiSchema(
    SchemaMetadata,
    SchemaWarnings,
    OperationsProvider,
    CaseFactory,
    CoverageBackend,
    StatefulBackend,
    TransportShape,
    ProbeAdapter,
    AuthBackend,
    Protocol,
):
    """The full contract a concrete schema implementation satisfies.

    Composed of role-specific protocols so callers can declare narrow contracts where applicable.
    """
