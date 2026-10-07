from __future__ import annotations

import secrets
import string
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from schemathesis.config import ApiKeyAuthConfig, HttpBearerAuthConfig
from schemathesis.core.errors import HookExecutionError, InvalidSchema
from schemathesis.core.jsonschema import make_validator
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import resolve_pointer
from schemathesis.engine import Status, events
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import PhaseName

if TYPE_CHECKING:
    from collections.abc import Generator

    from schemathesis.core.jsonschema.types import JsonSchemaObject, JsonValue
    from schemathesis.core.transport import Response
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EngineEvent, EventGenerator
    from schemathesis.engine.run import Phase
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.auth_flow.models import AuthFlowSpec
    from schemathesis.specs.openapi.schemas import OpenApiSchema


@dataclass(slots=True)
class AuthBootstrapPayload:
    spec: AuthFlowSpec | None
    status: Status
    failure_stage: Literal["sign-up", "register", "login", "extract"] | None = None
    status_code: int | None = None
    message: str | None = None


def execute(ctx: EngineContext, phase: Phase) -> EventGenerator:
    from schemathesis.specs.openapi.auth_flow.detection import has_supplied_auth
    from schemathesis.specs.openapi.schemas import OpenApiSchema

    schema = ctx.schema
    assert isinstance(schema, OpenApiSchema)
    try:
        spec = schema.analysis.auth_flow
    except HookExecutionError:
        # The schema analysis phase reports failing hooks.
        spec = None
    if spec is None:
        yield events.PhaseFinished(
            phase=phase, status=Status.SUCCESS, payload=AuthBootstrapPayload(spec=None, status=Status.SUCCESS)
        )
        return
    if has_supplied_auth(schema, spec.target_scheme):
        yield events.PhaseFinished(
            phase=phase, status=Status.SKIP, payload=AuthBootstrapPayload(spec=spec, status=Status.SKIP)
        )
        return
    suite = events.SuiteStarted(phase=PhaseName.AUTH_BOOTSTRAP)
    yield suite
    payload = yield from _bootstrap(ctx, schema, spec, suite.id)
    yield events.SuiteFinished(id=suite.id, phase=PhaseName.AUTH_BOOTSTRAP, status=payload.status)
    yield events.PhaseFinished(phase=phase, status=payload.status, payload=payload)


def _bootstrap(
    ctx: EngineContext, schema: OpenApiSchema, spec: AuthFlowSpec, suite_id: uuid.UUID
) -> Generator[EngineEvent, None, AuthBootstrapPayload]:
    register = schema.find_operation_by_label(spec.register_operation)
    login = schema.find_operation_by_label(spec.login_operation)
    # The flow names operations from this schema, so both exist.
    assert register is not None and login is not None
    sign_up = _sign_up_body(register, spec.credentials)
    if sign_up is None:
        return _failure(spec, "sign-up")
    body, media_type = sign_up
    response = yield from _call(ctx, suite_id, register, body, media_type=media_type)
    if isinstance(response, Exception) or not _is_success(response):
        return _failure(spec, "register", response)
    credentials = {name: body[name] for name in spec.credentials}
    response = yield from _call(ctx, suite_id, login, credentials, media_type=spec.login_media_type)
    if isinstance(response, Exception) or not _is_success(response):
        return _failure(spec, "login", response)
    token = _extract_token(response, spec.token_pointer)
    if token is None:
        return _failure(spec, "extract")
    definition = schema.security.security_definitions[spec.target_scheme]
    schema.bootstrapped_credentials = {spec.login_operation: credentials}
    # Flows only target bearer and API key schemes.
    schema.bootstrapped_auth = {
        spec.target_scheme: HttpBearerAuthConfig(bearer=token)
        if definition.get("type") == "http"
        else ApiKeyAuthConfig(api_key=token)
    }
    return AuthBootstrapPayload(spec=spec, status=Status.SUCCESS)


def _sign_up_body(operation: APIOperation, credentials: tuple[str, ...]) -> tuple[dict[str, JsonValue], str] | None:
    # Servers reject blank profile fields, or issue unusable sessions without optional ones such as a role.
    from hypothesis.errors import InvalidArgument, Unsatisfiable

    from schemathesis.generation.hypothesis.examples import generate_one
    from schemathesis.specs.openapi._hypothesis import make_positive_strategy

    # Use the body the flow came from: the first one that declares properties.
    body = next(
        body for body in operation.body if isinstance(body.raw_schema, dict) and "properties" in body.raw_schema
    )
    schema = cast("JsonSchemaObject", body.optimized_schema)
    properties = {
        name: {**subschema, "minLength": 1}
        if isinstance(subschema, dict) and subschema.get("type") == "string" and "minLength" not in subschema
        else subschema
        for name, subschema in schema["properties"].items()
    }
    strategy = make_positive_strategy(
        {**schema, "type": "object", "required": list(properties), "properties": properties},
        operation.label,
        ParameterLocation.BODY,
        body.media_type,
        operation.schema.config.generation_for(operation=operation, phase="fuzzing"),
        operation.schema.adapter.jsonschema_validator_cls,
        name_to_uri=body.name_to_uri,
    )
    try:
        value = cast("dict[str, JsonValue]", generate_one(strategy))
    except (Unsatisfiable, InvalidArgument, InvalidSchema):
        return None
    # Servers often enforce password and email rules the schema omits; realistic values pass them.
    minted = {**value, **{name: _mint(name) for name in credentials}}
    validator = make_validator(schema, operation.schema.adapter.jsonschema_validator_cls)
    return (minted if validator.is_valid(minted) else value), body.media_type


def _mint(name: str) -> str:
    from schemathesis.specs.openapi.auth_flow.vocabulary import is_email, is_secret

    alphabet = string.ascii_letters + string.digits
    if is_secret(name):
        return secrets.choice(string.ascii_uppercase) + "".join(secrets.choice(alphabet) for _ in range(14)) + "!1"
    if is_email(name):
        return f"{secrets.token_hex(6)}@{secrets.token_hex(4)}.test"
    return "".join(secrets.choice(alphabet) for _ in range(12))


def _call(
    ctx: EngineContext,
    suite_id: uuid.UUID,
    operation: APIOperation,
    body: dict[str, JsonValue],
    media_type: str | None,
) -> Generator[EngineEvent, None, Response | Exception]:
    import requests

    started = events.ScenarioStarted(phase=PhaseName.AUTH_BOOTSTRAP, suite_id=suite_id, label=operation.label)
    yield started
    case = operation.Case(body=body, media_type=media_type)
    recorder = ScenarioRecorder(label=operation.label)
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    start = time.monotonic()
    result: Response | Exception
    try:
        result = case.call(**ctx.get_transport_kwargs(operation=operation))
    except requests.RequestException as exc:
        result = exc
        status = Status.ERROR
    else:
        recorder.record_response(case_id=case.id, response=result)
        status = Status.SUCCESS if _is_success(result) else Status.FAILURE
    yield events.ScenarioFinished(
        id=started.id,
        suite_id=suite_id,
        phase=PhaseName.AUTH_BOOTSTRAP,
        label=operation.label,
        status=status,
        recorder=recorder,
        elapsed_time=time.monotonic() - start,
        skip_reason=None,
        is_final=False,
    )
    return result


def _is_success(response: Response) -> bool:
    return 200 <= response.status_code < 300


def _failure(
    spec: AuthFlowSpec,
    stage: Literal["sign-up", "register", "login", "extract"],
    result: Response | Exception | None = None,
) -> AuthBootstrapPayload:
    status_code = message = None
    if isinstance(result, Exception):
        message = str(result)
    elif result is not None:
        status_code = result.status_code
        message = result.text
    return AuthBootstrapPayload(
        spec=spec, status=Status.ERROR, failure_stage=stage, status_code=status_code, message=message
    )


def _extract_token(response: Response, pointer: str) -> str | None:
    try:
        document = response.json()
    except ValueError:
        return None
    token = resolve_pointer(document, pointer)
    return token if isinstance(token, str) and token else None
