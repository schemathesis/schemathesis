from __future__ import annotations

import base64
import json
import shlex
from collections.abc import Callable, Generator, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NoReturn, Self
from urllib.parse import urlsplit
from urllib.request import url2pathname

import jsonschema_rs
from google.protobuf import descriptor_pb2, descriptor_pool, field_mask_pb2, json_format, message, message_factory

# googleapis-common-protos ships no type information.
from google.rpc import status_pb2  # type: ignore[import-untyped]
from hypothesis import strategies as st
from typing_extensions import override

from schemathesis import auths
from schemathesis.core import NOT_SET, SCHEMATHESIS_TEST_CASE_HEADER, Body, NotSet, Specification
from schemathesis.core.control import SkipTest
from schemathesis.core.errors import InvalidSchema, OperationNotFound, SerializationError, not_found_message
from schemathesis.core.output.sanitization import sanitize_value
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.result import Ok, Result
from schemathesis.core.statistic import ApiStatistic, StatefulInference
from schemathesis.core.timing import Instant
from schemathesis.core.transport import CallOutcome, Response
from schemathesis.core.warnings import SchemathesisWarning
from schemathesis.filters import FilterUsage, OperationContext
from schemathesis.generation import GenerationMode
from schemathesis.generation.feedback import NO_FEEDBACK, FeedbackSources
from schemathesis.generation.jsonschema.builder import build
from schemathesis.generation.jsonschema.context import Alphabet
from schemathesis.generation.meta import (
    PhaseInfo,
    TestPhase,
    build_phase_data,
    uniform_metadata,
)
from schemathesis.grpc.checks import MalformedGrpcMessage, UndeclaredGrpcFields, UndecodableGrpcRequest
from schemathesis.hooks import HookContext, apply_to_all_dispatchers
from schemathesis.openapi.checks import AuthScenario, IgnoredAuth
from schemathesis.schemas import APIOperation, APIOperationMap, BaseSchema, OperationDefinition, UndeclaredResponses
from schemathesis.specs.grpc import coverage, error_feedback
from schemathesis.specs.grpc.converter import (
    message_schema,
    request_message,
    rewrite_json,
)
from schemathesis.specs.grpc.formats import grpc_formats
from schemathesis.specs.grpc.stateful import create_state_machine, infer_links
from schemathesis.specs.grpc.status import GRPC_STATUSES, INTERNAL, LIBRARY_DECODE_ERRORS, grpc_status, starts_with_verb

if TYPE_CHECKING:
    from google.protobuf.descriptor import Descriptor, MethodDescriptor, ServiceDescriptor
    from hypothesis.strategies import SearchStrategy

    from schemathesis.auths import AuthContext, AuthStorage
    from schemathesis.checks import CheckContext
    from schemathesis.config import GenerationConfig
    from schemathesis.core.error_feedback import ErrorFeedbackStore, Observation
    from schemathesis.core.schema_analysis import SchemaWarning
    from schemathesis.core.spec import ApiSchema
    from schemathesis.engine.link_calibration import LinkCalibrationState
    from schemathesis.engine.observations import Observations
    from schemathesis.filters import FilterSet
    from schemathesis.generation.case import Case
    from schemathesis.generation.coverage import GenerationSession
    from schemathesis.generation.stateful.state_machine import APIStateMachine
    from schemathesis.hooks import HookDispatcher
    from schemathesis.resources import ResourcePool
    from schemathesis.transport.grpc import GrpcTransport


MEDIA_TYPE = "application/grpc"
FIELD_MASK = "google.protobuf.FieldMask"
# Metadata that carries credentials, whether set by headers, `--auth` or an auth provider.
AUTH_METADATA = "authorization"
# HTTP/2 forbids connection-specific headers, and gRPC sets the rest itself.
EXCLUDED_METADATA = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "transfer-encoding",
        "upgrade",
        "te",
        "content-type",
        "content-length",
        "host",
    }
)


def is_unary(method: MethodDescriptor) -> bool:
    return not (method.client_streaming or method.server_streaming)


# Method name prefixes for reads and writes: API Improvement Proposals standard methods plus common custom verbs.
READ_VERBS = ("Get", "List", "BatchGet", "Search")
WRITE_VERBS = (
    "Create",
    "Update",
    "Delete",
    "Purge",
    "Undelete",
    "BatchCreate",
    "BatchUpdate",
    "BatchDelete",
    "Set",
    "Put",
    "Add",
    "Remove",
)


def side_effect_rank(method: MethodDescriptor) -> int:
    """Run order: reads first, methods of unknown effect next, writes last.

    A declared idempotency level wins over the method name.
    """
    level = method.GetOptions().idempotency_level
    if level == descriptor_pb2.MethodOptions.NO_SIDE_EFFECTS:
        return 0
    if level == descriptor_pb2.MethodOptions.IDEMPOTENT:
        return 2
    if starts_with_verb(method.name, READ_VERBS):
        return 0
    if starts_with_verb(method.name, WRITE_VERBS):
        return 2
    return 1


@dataclass(eq=False, repr=False)
class GrpcSchema(BaseSchema):
    # Built from the serialized descriptor set, which keeps extensions that `raw_schema` drops.
    pool: descriptor_pool.DescriptorPool = field(default_factory=descriptor_pool.DescriptorPool)
    # Tested services by full name, in declaration order.
    services: dict[str, ServiceDescriptor] = field(default_factory=dict)
    _transport: GrpcTransport | None = None

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"

    @override
    def clone(self, *, test_function: Callable | NotSet = NOT_SET, filter_set: FilterSet | NotSet = NOT_SET) -> Self:
        cloned = super().clone(test_function=test_function, filter_set=filter_set)
        # Descriptors never change, so clones share them.
        cloned.pool = self.pool
        cloned.services = self.services
        return cloned

    @property
    @override
    def specification(self) -> Specification:
        return Specification.grpc(version="")

    @override
    def get_transport(self, app: object) -> GrpcTransport:
        if self._transport is None:
            # grpcio has no free-threaded build, so loading a schema does not import it.
            from schemathesis.transport.grpc import GrpcTransport

            self._transport = GrpcTransport()
        return self._transport

    @override
    def build_request_url(self, case: Case, base_url: str) -> str:
        return f"{base_url}{case.path}"

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self.services)

    @override
    def _get_operation_map(self, key: str) -> APIOperationMap:
        service = self.services[key]
        return APIOperationMap(
            self, MethodMap(self, service, {method.name: method for method in service.methods if is_unary(method)})
        )

    @override
    def on_missing_operation(self, item: str, exc: KeyError) -> NoReturn:
        raise OperationNotFound(message=not_found_message(item, "service", self.services), item=item) from exc

    @override
    def find_operation_by_label(self, label: str) -> APIOperation | None:
        service_name, _, method_name = label.partition("/")
        service = self.services.get(service_name)
        if service is None:
            return None
        method = service.methods_by_name.get(method_name)
        if method is None or not is_unary(method):
            return None
        return self._build_operation(method)

    @property
    def descriptor_set_path(self) -> str | None:
        """File the descriptor set came from; schemas loaded through reflection have none."""
        location = urlsplit(self.location or "")
        return url2pathname(location.path) if location.scheme == "file" else None

    @override
    def declared_base_path(self) -> str:
        return ""

    @override
    def validate(self) -> None:
        return None

    @override
    def apply_auth(self, case: Case, context: AuthContext) -> bool:
        # Credentials travel as metadata, which the transport builds from the case headers.
        return False

    @override
    def as_reproduction_command(
        self, case: Case, *, headers: Mapping[str, Any] | None, verify: bool | str, base_url: str | None = None
    ) -> str:
        operation = case.operation
        scheme, _, address = (base_url or case._base_url or operation.base_url).partition("://")
        args = ["grpcurl"]
        if scheme == "grpc":
            args.append("-plaintext")
        # Without a descriptor set, `grpcurl` asks the server through reflection.
        if self.descriptor_set_path is not None:
            args.extend(["-protoset", self.descriptor_set_path])
        if scheme == "grpcs":
            # The CA usually comes from config, as callers only know whether the response was verified.
            ca = verify if isinstance(verify, str) else self.config.tls_verify_for(operation=operation)
            if isinstance(ca, str):
                args.extend(["-cacert", ca])
            cert = self.config.request_cert_for(operation=operation)
            if cert is not None:
                # A single file holds both the certificate and the key.
                certificate, key = (cert, cert) if isinstance(cert, str) else cert
                args.extend(["-cert", certificate, "-key", key])
        metadata = request_metadata(case, headers)
        sanitization = self.config.output.sanitization
        if sanitization.enabled:
            sanitize_value(metadata, config=sanitization)
        for key, value in metadata.items():
            args.extend(["-H", f"{key}: {value}"])
        input_type = operation.definition.raw.input_type
        body = {} if isinstance(case.body, NotSet) else case.body
        if isinstance(body, bytes):
            body = json_format.MessageToDict(
                message_factory.GetMessageClass(input_type).FromString(body), descriptor_pool=input_type.file.pool
            )
        args.extend(["-d", json.dumps(rewrite_json(body, input_type, _grpcurl_json)), address, operation.label])
        return " ".join(shlex.quote(arg) for arg in args)

    @override
    def status_name(self, response: Response) -> str | None:
        return GRPC_STATUSES[grpc_status(response)][0]

    @override
    def hash_case(self, case: Case) -> int:
        metadata = request_metadata(case, {SCHEMATHESIS_TEST_CASE_HEADER: "0"})
        payload: bytes | int
        try:
            payload = request_payload(case)
        except SerializationError:
            # A body that cannot be encoded fails when sent, so only its fields identify the case.
            payload = case._hash_container(case.body)
        return hash((case.path, payload, tuple(sorted(metadata.items()))))

    @override
    def classify_call_outcome(self, case: Case, response: Response) -> CallOutcome:
        return GRPC_STATUSES[grpc_status(response)][2]

    @override
    def explain_server_error(self, case: Case, response: Response) -> None:
        # Raw bytes are sent unchanged, so a decode failure there is the input's fault, not the message type's.
        if (
            grpc_status(response) == INTERNAL
            and not isinstance(case.body, bytes)
            and response.headers.get("grpc-message", [""])[0].startswith(LIBRARY_DECODE_ERRORS)
        ):
            raise UndecodableGrpcRequest(
                operation=case.operation.label, status_code=response.status_code, status_name=self.status_name(response)
            )

    @override
    def validate_response(self, operation: APIOperation, response: Response, *, case: Case | None = None) -> None:
        if grpc_status(response) == 0:
            output_type = operation.definition.raw.output_type
            decoded = _decode(operation, message_factory.GetMessageClass(output_type), response.content, "response")
            # A descriptor set can lag behind the server; reflection describes what the server runs.
            if self.descriptor_set_path is None:
                size = decoded.ByteSize()
                decoded.DiscardUnknownFields()
                if decoded.ByteSize() != size:
                    raise UndeclaredGrpcFields(
                        operation=operation.label,
                        message=f"The response carries fields that `{output_type.full_name}` does not declare",
                    )
        else:
            for details in response.headers.get("grpc-status-details-bin", []):
                _decode(operation, status_pb2.Status, base64.b64decode(details), "status details")

    @override
    def evaluate_auth(self, ctx: CheckContext, response: Response, case: Case) -> bool | None:
        if grpc_status(response) != 0 or AUTH_METADATA not in response.request.headers:
            return True
        label = case.operation.label
        enforced = ctx.auth_enforced_operations
        # Enforcement is a property of the method, so one confirmation per run is enough.
        if enforced is not None and label in enforced:
            return None
        headers = case.headers.copy()
        headers.pop(AUTH_METADATA, None)
        probe = case.derive(headers=headers)
        kwargs = dict(ctx.internals.transport_kwargs or {})
        kwargs["headers"] = {
            key: value for key, value in (kwargs.get("headers") or {}).items() if key.lower() != AUTH_METADATA
        }
        # The session carries `--auth` credentials.
        kwargs.pop("session", None)
        kwargs.pop("auth", None)
        ctx.internals.record_case(parent_id=case.id, case=probe)
        probe_response = self.transport.send(probe, **kwargs)
        ctx.internals.record_response(case_id=probe.id, response=probe_response)
        # Without declared security, other errors may come from the anonymous caller's data, not from ignored auth.
        if grpc_status(probe_response) == 0:
            raise IgnoredAuth(
                operation=label,
                message=f"Expected UNAUTHENTICATED or PERMISSION_DENIED, got `OK` for `{label}`",
                scenario=AuthScenario.NO_AUTH,
                case_id=probe.id,
            )
        if enforced is not None:
            enforced.add(label)
        return None

    @override
    def parse_rejection(self, operation: APIOperation, case: Case, response: Response) -> tuple[Observation, ...]:
        return error_feedback.parse_rejection(operation, case, response)

    @override
    def get_tags(self, operation: APIOperation) -> list[str] | None:
        return [operation.definition.raw.containing_service.full_name]

    @override
    def get_operation_layers(self, operations: list[APIOperation]) -> list[list[APIOperation]]:
        # Methods that change state run after those that only read it, so a destructive call cannot spoil the rest.
        layers: list[list[APIOperation]] = [[], [], []]
        for operation in operations:
            layers[side_effect_rank(operation.definition.raw)].append(operation)
        return [layer for layer in layers if layer]

    @override
    def get_operation_id(self, operation: APIOperation) -> str | None:
        return None

    @override
    def is_deprecated(self, operation: APIOperation) -> bool:
        method = operation.definition.raw
        service = method.containing_service
        return method.GetOptions().deprecated or service.GetOptions().deprecated or service.file.GetOptions().deprecated

    @override
    def _measure_statistic(self) -> ApiStatistic:
        statistic = ApiStatistic()
        usage = FilterUsage(self.filter_set) if not self.filter_set.is_empty() else None
        streaming_selected = False
        for service in self.services.values():
            for method in service.methods:
                statistic.operations.total += 1
                operation = self._build_operation(method)
                if not self._should_skip(operation):
                    if is_unary(method):
                        statistic.operations.selected += 1
                    else:
                        streaming_selected = True
                # A filter naming a streaming method matched something, even though it is not tested.
                if usage is not None:
                    usage.record(OperationContext(operation=operation))
        if usage is not None:
            statistic.unmatched_filters = usage.results()
        if streaming_selected and statistic.operations.selected == 0:
            statistic.unsupported_selection = "Only streaming methods are selected; streaming is not supported"
        return statistic

    @override
    def iter_schema_warnings(self) -> list[SchemaWarning]:
        return [
            UnsupportedFieldWarning(operation_label=operation.label, field=name)
            for operation in self._iter_operations()
            for name in message_schema(operation.definition.raw.input_type).any_fields
        ]

    @override
    def get_all_operations(self) -> Generator[Result[APIOperation, InvalidSchema], None, None]:
        for operation in self._iter_operations():
            yield Ok(operation)

    def _iter_operations(self) -> Iterator[APIOperation]:
        for operation in self._iter_all_operations():
            if not self._should_skip(operation):
                yield operation

    def _iter_all_operations(self) -> Iterator[APIOperation]:
        """Unary methods, filters aside."""
        for service in self.services.values():
            for method in service.methods:
                if is_unary(method):
                    yield self._build_operation(method)

    def _build_operation(self, method: MethodDescriptor) -> APIOperation:
        label = f"{method.containing_service.full_name}/{method.name}"
        return APIOperation(
            base_url=self.get_base_url(),
            path=f"/{label}",
            label=label,
            method="post",
            app=self.app,
            schema=self,
            responses=UndeclaredResponses(),
            security=None,
            definition=OperationDefinition(raw=method),
        )

    @override
    def _case_media_type(self, operation: APIOperation, body: Body, media_type: str | None) -> str | None:
        return MEDIA_TYPE

    @override
    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        generation_mode: GenerationMode = GenerationMode.POSITIVE,
        feedback: FeedbackSources = NO_FEEDBACK,
        *,
        path_parameters: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: Any = NOT_SET,
        media_type: str | None = None,
        phase: TestPhase = TestPhase.FUZZING,
    ) -> SearchStrategy[Case]:
        generation = self.config.generation_for(operation=operation, phase=phase.value)
        if isinstance(body, NotSet):
            input_type = operation.definition.raw.input_type
            schema = message_schema(input_type).schema
            if feedback.error_feedback is not None:
                error_feedback.require_fields(
                    schema,
                    input_type,
                    feedback.error_feedback.observations(
                        operation_label=operation.label, location=ParameterLocation.BODY
                    ),
                )
            bodies = build(
                schema,
                draft=jsonschema_rs.Draft202012,
                formats=grpc_formats(),
                alphabet=Alphabet(allow_x00=generation.allow_x00, codec=generation.codec),
            )
        else:
            bodies = st.just(body)
        return grpc_cases(
            operation=operation,
            bodies=bodies,
            hooks=hooks,
            auth_storage=auth_storage,
            # A body the message's JSON mapping rejects fails to encode on the client and never reaches the server.
            skip=generation_mode.is_negative and generation.modes == [GenerationMode.NEGATIVE],
            headers=headers,
            phase=phase,
        )

    @override
    def get_strategies_from_examples(
        self, operation: APIOperation, feedback: FeedbackSources = NO_FEEDBACK, **kwargs: Any
    ) -> list[SearchStrategy[Case]]:
        raise NotImplementedError

    @override
    def iter_coverage_cases(
        self,
        operation: APIOperation,
        *,
        generation_modes: list[GenerationMode],
        generation_config: GenerationConfig,
        extra_data_source: ResourcePool | None = None,
        error_feedback: ErrorFeedbackStore | None = None,
        session: GenerationSession | None = None,
    ) -> Iterator[Case]:
        return coverage.iter_coverage_cases(operation, generation_modes, generation_config)

    @override
    def as_state_machine(self) -> type[APIStateMachine]:
        return create_state_machine(self)

    @override
    def build_state_machine(
        self,
        *,
        feedback: FeedbackSources,
        link_calibration: LinkCalibrationState | None,
    ) -> type[APIStateMachine]:
        # Links come from method and field names, and stateful messages are generated without runtime feedback.
        return self.as_state_machine()

    @override
    def apply_stateful_inference(self, observations: Observations | None) -> StatefulInference:
        count = len(infer_links(self._iter_operations()))
        return StatefulInference(inferred=count, total=count, selected=count)

    @override
    def create_extra_data_source(self) -> None:
        return None


@dataclass(slots=True)
class UnsupportedFieldWarning:
    """A request field Schemathesis leaves out of every generated message."""

    operation_label: str | None
    field: str

    @property
    def kind(self) -> SchemathesisWarning:
        return SchemathesisWarning.UNSUPPORTED_FIELD

    @property
    def message(self) -> str:
        return f"`{self.field}` - `google.protobuf.Any` is not generated"

    @property
    def group(self) -> str | None:
        return None


def request_payload(case: Case) -> bytes:
    """Serialized request message a call sends."""
    return encode_message(case.body, case.operation.definition.raw.input_type)


def encode_message(body: Body | NotSet, input_type: Descriptor) -> bytes:
    """`body` serialized as an `input_type` message."""
    if isinstance(body, NotSet):
        body = {}
    if isinstance(body, bytes):
        return body
    try:
        parsed = request_message(body, input_type)
    except (json_format.ParseError, TypeError) as exc:
        raise SerializationError(f"Cannot encode the request body as `{input_type.full_name}`: {exc}") from None
    return parsed.SerializeToString(deterministic=True)


def request_metadata(case: Case, headers: Mapping[str, Any] | None) -> dict[str, str]:
    """Metadata a call sends: case headers, the test case id, then explicit headers, without reserved keys."""
    merged = {key.lower(): str(value) for key, value in (case.headers or {}).items()}
    merged.setdefault(SCHEMATHESIS_TEST_CASE_HEADER.lower(), case.id)
    merged.update((key.lower(), str(value)) for key, value in (headers or {}).items())
    return filter_metadata(merged)


def filter_metadata(headers: Mapping[str, Any]) -> dict[str, str]:
    """Lowercased metadata without the keys HTTP/2 and gRPC reserve."""
    lowered = {key.lower(): str(value) for key, value in headers.items()}
    return {
        key: value for key, value in lowered.items() if key not in EXCLUDED_METADATA and not key.startswith("grpc-")
    }


def _grpcurl_json(value: object, type_name: str) -> object:
    """Rewrite `FieldMask` values to `{"paths": [...]}`, the only JSON form `grpcurl` reads for them."""
    if type_name == FIELD_MASK and isinstance(value, str):
        mask = field_mask_pb2.FieldMask()
        mask.FromJsonString(value)
        return {"paths": list(mask.paths)}
    return value


def _decode(
    operation: APIOperation, message_class: type[message.Message], content: bytes, what: str
) -> message.Message:
    name = message_class.DESCRIPTOR.full_name
    try:
        return message_class.FromString(content)
    except message.DecodeError as exc:
        raise MalformedGrpcMessage(
            operation=operation.label, type_name=name, message=f"Cannot decode the {what} as `{name}`: {exc}"
        ) from None


@st.composite
def grpc_cases(
    draw: Callable,
    *,
    operation: APIOperation,
    bodies: SearchStrategy[Any],
    hooks: HookDispatcher | None,
    auth_storage: AuthStorage | None,
    skip: bool,
    headers: dict[str, Any] | None,
    phase: TestPhase,
) -> Case:
    if skip:
        raise SkipTest("Negative test cases are not available for gRPC operations")
    started_at = Instant()
    hook_context = HookContext(operation=operation)
    body_ = draw(apply_to_all_dispatchers(operation, hook_context, hooks, bodies, "body"))
    headers_ = draw(apply_to_all_dispatchers(operation, hook_context, hooks, st.just(headers), "headers"))
    case = operation.Case(
        headers=headers_,
        body=body_,
        media_type=MEDIA_TYPE,
        _meta=uniform_metadata(
            mode=GenerationMode.POSITIVE,
            phase=PhaseInfo(name=phase, data=build_phase_data(phase, "Positive test case")),
            generation_time=started_at.elapsed,
        ),
    )
    auths.set_on_case(case, auths.AuthContext(operation=operation, app=operation.app), auth_storage)
    return case


@dataclass(slots=True)
class MethodMap(Mapping):
    _schema: GrpcSchema
    _service: ServiceDescriptor
    _unary: dict[str, MethodDescriptor]

    def __len__(self) -> int:
        return len(self._unary)

    def __iter__(self) -> Iterator[str]:
        return iter(self._unary)

    def __getitem__(self, item: str) -> APIOperation:
        method = self._unary.get(item)
        if method is None:
            if item in self._service.methods_by_name:
                raise KeyError(f"`{item}` is a streaming method; only unary methods are supported")
            raise KeyError(not_found_message(item, "method", self._unary))
        return self._schema._build_operation(method)


if TYPE_CHECKING:
    # Verify structural conformance to the spec-agnostic protocol; mypy fails here
    # if a method is renamed or its signature drifts from `ApiSchema`.
    def _verify_api_schema_protocol(schema: GrpcSchema) -> ApiSchema:
        return schema
