from __future__ import annotations

import base64
import json
import re
import shlex
from collections.abc import Callable, Generator, Iterator, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
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
from schemathesis.core.errors import (
    IncorrectUsage,
    InvalidSchema,
    OperationNotFound,
    SerializationError,
    not_found_message,
)
from schemathesis.core.failures import AuthScenario, IgnoredAuth, ServerError
from schemathesis.core.output.sanitization import sanitize_value
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.result import Ok, Result
from schemathesis.core.schema_analysis import SchemaWarning
from schemathesis.core.statistic import ApiStatistic, StatefulInference
from schemathesis.core.timing import Instant
from schemathesis.core.transport import CallOutcome, Response
from schemathesis.core.warnings import SchemathesisWarning
from schemathesis.filters import FilterUsage, OperationContext
from schemathesis.generation import GenerationMode
from schemathesis.generation.feedback import NO_FEEDBACK, FeedbackSources
from schemathesis.generation.hypothesis.reporting import UnsatisfiableSchema
from schemathesis.generation.jsonschema.builder import EMPTY_STRATEGY, build
from schemathesis.generation.jsonschema.context import Alphabet
from schemathesis.generation.meta import (
    PhaseInfo,
    TestPhase,
    build_phase_data,
    uniform_metadata,
)
from schemathesis.grpc.checks import MalformedGrpcMessage, UndeclaredGrpcFields, UndecodableGrpcRequest
from schemathesis.hooks import HookContext, apply_to_all_dispatchers
from schemathesis.schemas import APIOperation, APIOperationMap, BaseSchema, OperationDefinition, UndeclaredResponses
from schemathesis.specs.grpc import coverage, error_feedback
from schemathesis.specs.grpc.converter import (
    body_message,
    message_schema,
    message_to_json,
    rewrite_json,
)
from schemathesis.specs.grpc.formats import grpc_formats
from schemathesis.specs.grpc.stateful import (
    GrpcLink,
    LinkedValue,
    create_state_machine,
    infer_links,
    link_candidates,
    with_value,
)
from schemathesis.specs.grpc.status import (
    GRPC_STATUSES,
    INTERNAL,
    LIBRARY_DECODE_ERRORS,
    OK,
    STATUS_NAMES_BY_HTTP_STATUS,
    grpc_status,
    starts_with_verb,
)

if TYPE_CHECKING:
    from google.protobuf.descriptor import MethodDescriptor, ServiceDescriptor
    from hypothesis.strategies import SearchStrategy

    from schemathesis.auths import AuthContext, AuthStorage
    from schemathesis.checks import CheckContext
    from schemathesis.config import GenerationConfig
    from schemathesis.core.error_feedback import ErrorFeedbackStore, Observation
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
# The only characters gRPC accepts in metadata keys.
METADATA_KEY = re.compile(r"[0-9a-z_.-]+")
URL_SAFE_TO_STANDARD = str.maketrans("-_", "+/")


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
            body = message_to_json(message_factory.GetMessageClass(input_type).FromString(body), input_type.file.pool)
        args.extend(["-d", json.dumps(rewrite_json(body, input_type, _grpcurl_json)), address, operation.label])
        return " ".join(shlex.quote(arg) for arg in args)

    @override
    def status_name_for_code(self, status_code: int) -> str | None:
        return STATUS_NAMES_BY_HTTP_STATUS.get(status_code)

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
        return GRPC_STATUSES[grpc_status(response)].outcome

    @override
    def server_error(self, case: Case, response: Response) -> ServerError:
        status = grpc_status(response)
        name = GRPC_STATUSES[status].name
        # Raw bytes are sent unchanged, so a decode failure there is the input's fault, not the message type's.
        if (
            status == INTERNAL
            and not isinstance(case.body, bytes)
            and response.headers.get("grpc-message", [""])[0].startswith(LIBRARY_DECODE_ERRORS)
        ):
            return UndecodableGrpcRequest(
                operation=case.operation.label, status_code=response.status_code, status_name=name
            )
        return ServerError(operation=case.operation.label, status_code=response.status_code, status_name=name)

    @override
    def validate_response(self, operation: APIOperation, response: Response, *, case: Case | None = None) -> None:
        if grpc_status(response) == OK:
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
        if grpc_status(response) != OK or AUTH_METADATA not in response.request.headers:
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
        probe_response = ctx.internals.send_probe(case, probe, kwargs)
        # Without declared security, other errors may come from the anonymous caller's data, not from ignored auth.
        if grpc_status(probe_response) == OK:
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
        for name, value in (("query", query), ("cookies", cookies), ("path_parameters", path_parameters)):
            if value:
                raise IncorrectUsage(f"`{name}` is not supported for gRPC operations")
        generation = self.config.generation_for(operation=operation, phase=phase.value)
        if isinstance(body, (NotSet, LinkedValue)):
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
            bodies: SearchStrategy[Body] = build(
                schema,
                draft=jsonschema_rs.Draft202012,
                formats=grpc_formats(),
                alphabet=Alphabet(allow_x00=generation.allow_x00, codec=generation.codec),
            )
            if isinstance(body, LinkedValue):
                bodies = bodies.map(lambda generated: with_value(operation, generated, body.field, body.value))
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
        return create_state_machine(self, feedback)

    @override
    def apply_stateful_inference(self, observations: Observations | None) -> StatefulInference:
        count = len(infer_links(self._iter_operations()))
        return StatefulInference(inferred=count, total=count, selected=count)

    @cached_property
    def _links_by_source(self) -> dict[str, list[GrpcLink]]:
        links: dict[str, list[GrpcLink]] = {}
        for link in infer_links(self._iter_operations()):
            links.setdefault(link.source, []).append(link)
        return links

    @override
    def iter_link_candidates(
        self,
        *,
        operation: APIOperation,
        case: Case,
        response: Response,
        operations_by_label: dict[str, APIOperation],
        excluded_labels: set[str],
    ) -> list[tuple[APIOperation, dict[str, Body | LinkedValue]]]:
        return link_candidates(
            self._links_by_source.get(operation.label, []), case, response, operations_by_label, excluded_labels
        )

    @override
    def create_extra_data_source(self) -> None:
        return None


@dataclass(slots=True)
class UnsupportedFieldWarning(SchemaWarning):
    """A request field Schemathesis leaves out of every generated message."""

    operation_label: str | None
    field: str

    @property
    def kind(self) -> SchemathesisWarning:
        return SchemathesisWarning.UNSUPPORTED_FIELD

    @property
    def message(self) -> str:
        return f"`{self.field}` - `google.protobuf.Any` is not generated"


def request_payload(case: Case) -> bytes:
    """Serialized request message a call sends."""
    body = case.body
    if isinstance(body, bytes):
        return body
    input_type = case.operation.definition.raw.input_type
    try:
        parsed = body_message(body, input_type)
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


def parse_target(url: str, verify: bool | str | None, *, uses_proxy: bool) -> tuple[str, bool]:
    """Address and whether it uses TLS, for a `grpc://` or `grpcs://` URL."""
    if uses_proxy:
        raise IncorrectUsage("Proxies are not supported for gRPC targets")
    if isinstance(verify, str) and Path(verify).is_dir():
        raise IncorrectUsage(f"gRPC targets need a CA bundle file, got a directory: `{verify}`")
    scheme, _, rest = url.partition("://")
    if scheme not in ("grpc", "grpcs"):
        raise IncorrectUsage(f"gRPC targets need a `grpc://` or `grpcs://` base URL, got `{url}`")
    # Calls are routed by method name alone, so only a trailing slash can follow the address.
    address, _, path = rest.partition("/")
    if path:
        raise IncorrectUsage(f"gRPC targets need a base URL without a path, got `{url}`")
    tls = scheme == "grpcs"
    if tls and verify is False:
        raise IncorrectUsage("`tls-verify=false` is not supported for gRPC targets; pass a CA bundle path instead")
    return address, tls


def metadata_pairs(headers: Mapping[str, str]) -> tuple[tuple[str, str | bytes], ...]:
    """Metadata as gRPC sends it: values of `-bin` keys travel as bytes."""
    return tuple((key, _metadata_value(key, value)) for key, value in headers.items())


def _metadata_value(key: str, value: str) -> str | bytes:
    # The gRPC library rejects invalid metadata before sending, and the call fails as if the server had.
    if not METADATA_KEY.fullmatch(key):
        raise _invalid_metadata(key, "keys may contain only lowercase letters, digits, `-`, `_` and `.`")
    if key.endswith("-bin"):
        try:
            # Padded or not, in the standard or the URL-safe alphabet.
            standard = value.translate(URL_SAFE_TO_STANDARD)
            return base64.b64decode(standard + "=" * (-len(standard) % 4), validate=True)
        except ValueError:
            raise _invalid_metadata(key, "values of keys ending in `-bin` must be base64") from None
    if not (value.isascii() and value.isprintable()):
        raise _invalid_metadata(key, "values must be printable ASCII, or base64 under a key ending in `-bin`")
    return value


def _invalid_metadata(key: str, rule: str) -> IncorrectUsage:
    return IncorrectUsage(f"Header `{key}` is not valid gRPC metadata: {rule}")


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
    bodies = apply_to_all_dispatchers(operation, hook_context, hooks, bodies, "body")
    if bodies is EMPTY_STRATEGY:
        # Drawing from it only rejects the input, which stateful testing reads as nothing to test.
        raise UnsatisfiableSchema(
            f"Cannot generate test data for `{operation.label}`\n\n"
            f"No `{operation.definition.raw.input_type.full_name}` message satisfies "
            "its required fields and validation rules"
        )
    body_ = draw(bodies)
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
