from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from schemathesis.core.errors import HookExecutionError
from schemathesis.core.transforms import resolve_pointer
from schemathesis.engine import Status, events
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import PhaseName

if TYPE_CHECKING:
    from collections.abc import Generator

    from schemathesis.core.jsonschema.types import JsonValue
    from schemathesis.core.spec import AuthFlow, AuthFlowSteps
    from schemathesis.core.transport import Response
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EngineEvent, EventGenerator
    from schemathesis.engine.run import Phase
    from schemathesis.schemas import APIOperation


@dataclass(slots=True)
class AuthBootstrapPayload:
    spec: AuthFlowSteps | None
    status: Status
    failure_stage: Literal["sign-up", "register", "login", "extract"] | None = None
    status_code: int | None = None
    message: str | None = None


def execute(ctx: EngineContext, phase: Phase) -> EventGenerator:
    try:
        flow = ctx.schema.auth_flow()
    except HookExecutionError:
        # The schema analysis phase reports failing hooks.
        flow = None
    if flow is None:
        yield events.PhaseFinished(
            phase=phase, status=Status.SUCCESS, payload=AuthBootstrapPayload(spec=None, status=Status.SUCCESS)
        )
        return
    if not ctx.schema.config.auth.auto_signup or flow.is_supplied():
        yield events.PhaseFinished(
            phase=phase, status=Status.SKIP, payload=AuthBootstrapPayload(spec=flow.spec, status=Status.SKIP)
        )
        return
    suite = events.SuiteStarted(phase=PhaseName.AUTH_BOOTSTRAP)
    yield suite
    payload = yield from _bootstrap(ctx, flow, suite.id)
    yield events.SuiteFinished(id=suite.id, phase=PhaseName.AUTH_BOOTSTRAP, status=payload.status)
    yield events.PhaseFinished(phase=phase, status=payload.status, payload=payload)


def _bootstrap(
    ctx: EngineContext, flow: AuthFlow, suite_id: uuid.UUID
) -> Generator[EngineEvent, None, AuthBootstrapPayload]:
    spec = flow.spec
    register = ctx.schema.find_operation_by_label(spec.register_operation)
    login = ctx.schema.find_operation_by_label(spec.login_operation)
    # The flow names operations from this schema, so both exist.
    assert register is not None and login is not None
    sign_up = flow.sign_up_body(register)
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
    flow.authenticate(credentials, token)
    return AuthBootstrapPayload(spec=spec, status=Status.SUCCESS)


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
    spec: AuthFlowSteps,
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
