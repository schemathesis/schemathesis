from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator

    from hypothesis.strategies import SearchStrategy
    from requests.structures import CaseInsensitiveDict

    from schemathesis.auths import AuthContext, AuthProvider, AuthStorage
    from schemathesis.checks import CheckContext
    from schemathesis.config import GenerationConfig, ProjectConfig
    from schemathesis.core import Body, Specification
    from schemathesis.core.error_feedback import ErrorFeedbackStore
    from schemathesis.core.error_feedback.store import Observation
    from schemathesis.core.errors import InvalidSchema
    from schemathesis.core.jsonschema.types import JsonSchemaObject, JsonValue
    from schemathesis.core.result import Result
    from schemathesis.core.schema_analysis import SchemaWarning
    from schemathesis.core.statistic import ApiStatistic, StatefulInference
    from schemathesis.core.transport import CallOutcome, HttpMethod, Response
    from schemathesis.engine.link_calibration import LinkCalibrationState
    from schemathesis.engine.observations import Observations
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation import GenerationMode
    from schemathesis.generation.case import Case
    from schemathesis.generation.feedback import FeedbackSources
    from schemathesis.generation.meta import CaseMetadata, TestPhase
    from schemathesis.generation.stateful.state_machine import APIStateMachine, StepInput
    from schemathesis.hooks import HookDispatcher
    from schemathesis.resources import ExtraDataSource, ResourcePool
    from schemathesis.schemas import APIOperation


class SchemaMetadata(Protocol):
    """Shared schema-level state and identity: source, location, configuration, and hook surface."""

    config: ProjectConfig
    hooks: HookDispatcher
    auth: AuthStorage
    raw_schema: JsonSchemaObject
    location: str | None

    @property
    def specification(self) -> Specification: ...  # pragma: no cover

    @property
    def title(self) -> str | None: ...  # pragma: no cover

    def validate(self) -> None: ...  # pragma: no cover

    def get_base_url(self) -> str: ...  # pragma: no cover

    def get_local_hook_dispatcher(self) -> HookDispatcher | None: ...  # pragma: no cover


class Scheduler(Protocol):
    """Decides which operation a unit-phase worker picks up next."""

    def next_operation(self) -> Result[APIOperation, InvalidSchema] | None: ...  # pragma: no cover

    def release(self) -> None: ...  # pragma: no cover


class AuthFlowSteps(Protocol):
    """Operations a sign-up and login flow calls, and where the login response carries the token."""

    @property
    def register_operation(self) -> str: ...  # pragma: no cover

    @property
    def login_operation(self) -> str: ...  # pragma: no cover

    @property
    def login_media_type(self) -> str: ...  # pragma: no cover

    @property
    def credentials(self) -> tuple[str, ...]: ...  # pragma: no cover

    @property
    def token_pointer(self) -> str: ...  # pragma: no cover


class AuthFlow(Protocol):
    """A sign-up and login flow the engine runs to authenticate the requests that follow."""

    @property
    def spec(self) -> AuthFlowSteps: ...  # pragma: no cover

    def is_supplied(self) -> bool: ...  # pragma: no cover

    # `schemathesis.toml` lines that authenticate through this flow.
    def config_lines(self) -> list[str]: ...  # pragma: no cover

    def sign_up_body(self, operation: APIOperation) -> tuple[dict[str, JsonValue], str] | None: ...  # pragma: no cover

    def authenticate(self, credentials: dict[str, JsonValue], token: str) -> None: ...  # pragma: no cover


class ApiSchema(SchemaMetadata, Protocol):
    """Full schema contract."""

    # Static-analysis warnings collected from the schema.
    def iter_schema_warnings(self) -> list[SchemaWarning]: ...  # pragma: no cover

    # Operation enumeration, lookup, statistics, and unit-phase scheduling.
    @property
    def statistic(self) -> ApiStatistic: ...  # pragma: no cover

    def get_all_operations(self) -> Generator[Result[APIOperation, InvalidSchema], None, None]: ...  # pragma: no cover

    def find_operation_by_label(self, label: str) -> APIOperation | None: ...  # pragma: no cover

    def on_missing_operation(self, item: str, exc: KeyError) -> NoReturn: ...  # pragma: no cover

    def get_tags(self, operation: APIOperation) -> list[str] | None: ...  # pragma: no cover

    def get_operation_layers(self, operations: list[APIOperation]) -> list[list[APIOperation]]: ...  # pragma: no cover

    # Test-case construction for an operation.
    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: HookDispatcher | None = ...,
        auth_storage: AuthStorage | None = ...,
        generation_mode: GenerationMode = ...,
        feedback: FeedbackSources = ...,
        *,
        path_parameters: dict[str, Any] | None = ...,
        headers: dict[str, Any] | None = ...,
        cookies: dict[str, Any] | None = ...,
        query: dict[str, Any] | None = ...,
        body: Any = ...,
        media_type: str | None = ...,
        phase: TestPhase = ...,
    ) -> SearchStrategy[Case]: ...  # pragma: no cover

    def make_case(
        self,
        *,
        operation: APIOperation,
        method: HttpMethod | None = ...,
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
        self, operation: APIOperation, feedback: FeedbackSources = ..., **kwargs: Any
    ) -> list[SearchStrategy[Case]]: ...  # pragma: no cover

    def revalidate_case_metadata(self, case: Case) -> None: ...  # pragma: no cover

    # Coverage-phase generation.
    def iter_coverage_cases(
        self,
        operation: APIOperation,
        *,
        generation_modes: list[GenerationMode],
        generation_config: GenerationConfig,
        extra_data_source: ResourcePool | None = ...,
        error_feedback: ErrorFeedbackStore | None = ...,
    ) -> Iterator[Case]: ...  # pragma: no cover

    # Stateful phase: state machine, link candidates, fuzz weights.
    def as_state_machine(self) -> type[APIStateMachine]: ...  # pragma: no cover

    def build_state_machine(
        self,
        *,
        feedback: FeedbackSources,
        link_calibration: LinkCalibrationState | None,
    ) -> type[APIStateMachine]: ...  # pragma: no cover

    def apply_stateful_inference(self, observations: Observations | None) -> StatefulInference: ...  # pragma: no cover

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

    def record_link_outcome(
        self,
        *,
        calibration: LinkCalibrationState,
        response: Response,
        observations: tuple[Observation, ...],
        step_input: StepInput,
        recorder: ScenarioRecorder,
    ) -> None: ...  # pragma: no cover

    # Wire-level transformations: URL construction, body preparation, response validation.
    def validate_response(
        self,
        operation: APIOperation,
        response: Response,
        *,
        case: Case | None = ...,
    ) -> bool | None: ...  # pragma: no cover

    def build_request_url(self, case: Case, base_url: str) -> str: ...  # pragma: no cover

    def prepare_request_body(self, case: Case) -> Body: ...  # pragma: no cover

    def evaluate_server_error(self, case: Case, response: Response) -> None: ...  # pragma: no cover

    def evaluate_auth(self, ctx: CheckContext, response: Response, case: Case) -> bool | None: ...  # pragma: no cover
    def explain_server_error(self, case: Case, response: Response) -> None: ...  # pragma: no cover

    def evaluate_negative_data(self, case: Case, response: Response) -> None: ...  # pragma: no cover

    def evaluate_positive_data(self, case: Case, response: Response) -> None: ...  # pragma: no cover

    def classify_call_outcome(self, case: Case, response: Response) -> CallOutcome: ...  # pragma: no cover

    def status_name(self, response: Response) -> str | None: ...  # pragma: no cover

    def prepare_multipart(
        self,
        form_data: dict[str, Any],
        operation: APIOperation,
        media_type: str,
        selected_content_types: dict[str, str] | None = ...,
    ) -> tuple[list | None, dict[str, Any] | None]: ...  # pragma: no cover

    def get_parameter_serializer(
        self, operation: APIOperation, location: str
    ) -> Callable | None: ...  # pragma: no cover

    def declared_base_path(self) -> str: ...  # pragma: no cover

    # Adaptations the engine invokes after probes uncover server behavior.
    def create_extra_data_source(self) -> ExtraDataSource | None: ...  # pragma: no cover

    def adapt_to_null_byte_in_header_failure(self) -> None: ...  # pragma: no cover

    def adapt_to_path_decoder_rejection(self) -> None: ...  # pragma: no cover

    # Spec-aware authentication for generated cases.
    def apply_auth(self, case: Case, context: AuthContext) -> bool: ...  # pragma: no cover

    def auth_flow(self) -> AuthFlow | None: ...  # pragma: no cover

    @property
    def reauth_retry_statuses(self) -> frozenset[int]: ...  # pragma: no cover

    def security_auth_providers(self) -> Iterator[AuthProvider]: ...  # pragma: no cover
