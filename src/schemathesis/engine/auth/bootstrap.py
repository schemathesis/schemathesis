from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from schemathesis import auths
from schemathesis.core.errors import HookExecutionError
from schemathesis.engine import Status, events
from schemathesis.engine.auth.minting import MintingError, mint_credentials
from schemathesis.engine.auth.models import AuthBootstrapPayload, BootstrappedSession
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import PhaseName
from schemathesis.specs.openapi.auths import (
    ApiKeyAuthProvider,
    DynamicTokenAuthProvider,
    HttpBearerAuthProvider,
    LoginRequestError,
    TokenExtractionError,
)

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EventGenerator
    from schemathesis.engine.run import Phase
    from schemathesis.schemas import APIOperation, BaseSchema
    from schemathesis.specs.openapi.auth_flow.models import AuthFlowSpec
    from schemathesis.specs.openapi.schemas import OpenApiSchema


@dataclass(slots=True)
class _ScenarioOutcome:
    """Result of one bootstrap scenario (register or login)."""

    recorder: ScenarioRecorder
    response: Response | None
    status: Status
    elapsed_time: float
    error: Exception | None


def execute(ctx: EngineContext, phase: Phase) -> EventGenerator:
    from schemathesis.specs.openapi.schemas import OpenApiSchema

    if not isinstance(ctx.schema, OpenApiSchema):
        yield events.PhaseFinished(
            phase=phase,
            status=Status.SUCCESS,
            payload=AuthBootstrapPayload(spec=None, status=Status.SUCCESS),
        )
        return

    schema = ctx.schema
    try:
        spec = schema.analysis.auth_flow
    except HookExecutionError as exc:
        yield events.NonFatalError(
            error=exc,
            phase=phase.name,
            label=f"`{exc.hook_name}` hook",
            related_to_operation=False,
        )
        yield events.PhaseFinished(phase=phase, status=Status.ERROR, payload=None)
        return
    if spec is None:
        yield events.PhaseFinished(
            phase=phase,
            status=Status.SUCCESS,
            payload=AuthBootstrapPayload(spec=None, status=Status.SUCCESS, message="no auth flow detected"),
        )
        return

    if not should_bootstrap(spec, schema):
        yield events.PhaseFinished(
            phase=phase,
            status=Status.SKIP,
            payload=AuthBootstrapPayload(
                spec=spec,
                status=Status.SKIP,
                message=f"explicit auth covers scheme {spec.target_scheme!r}",
            ),
        )
        return

    yield from _run_with_events(ctx, schema, spec, phase)


def should_bootstrap(spec: AuthFlowSpec, schema: OpenApiSchema) -> bool:
    """Skip bootstrap when the user has already declared explicit credentials for the target scheme."""
    scheme = spec.target_scheme
    auth = schema.config.auth
    if scheme in auth.openapi.schemes:
        return False
    if scheme in auth.dynamic.schemes:
        return False
    if _has_programmatic_auth(schema):
        return False
    return True


def _has_programmatic_auth(schema: BaseSchema) -> bool:
    """Check whether any programmatic AuthProvider is registered globally or per-schema."""
    if auths.GLOBAL_AUTH_STORAGE.is_defined:
        return True
    schema_auth = getattr(schema, "auth", None)
    return schema_auth is not None and schema_auth.is_defined


def _run_with_events(
    ctx: EngineContext,
    schema: OpenApiSchema,
    spec: AuthFlowSpec,
    phase: Phase,
) -> EventGenerator:
    """Execute auth bootstrap scenarios.

    Emits one suite plus one scenario per HTTP call so the run is visible to cassettes, JUnit, Allure, and HTML reports.
    """
    try:
        creds = mint_credentials(spec.credentials)
    except MintingError as exc:
        yield events.PhaseFinished(
            phase=phase,
            status=Status.ERROR,
            payload=AuthBootstrapPayload(spec=spec, status=Status.ERROR, failure_stage="mint", message=str(exc)),
        )
        return

    register_operation = _operation_by_label(schema, spec.register_operation)
    login_operation = _operation_by_label(schema, spec.login_operation)

    suite_started = events.SuiteStarted(phase=PhaseName.AUTH_BOOTSTRAP)
    yield suite_started

    register_outcome = _execute_call_scenario(ctx, operation=register_operation, body=creds)
    yield from _emit_scenario_events(
        phase=PhaseName.AUTH_BOOTSTRAP,
        suite_id=suite_started.id,
        label=register_operation.label,
        outcome=register_outcome,
    )
    if register_outcome.error is not None or not _is_success_response(register_outcome.response):
        yield from _finish_with_failure(
            phase=phase,
            suite_id=suite_started.id,
            payload=_register_failure_payload(spec, register_outcome),
        )
        return

    token_config = spec.token_config
    login_outcome = _execute_call_scenario(
        ctx,
        operation=login_operation,
        body=creds,
        method=token_config.method,
        path=token_config.path,
    )
    yield from _emit_scenario_events(
        phase=PhaseName.AUTH_BOOTSTRAP,
        suite_id=suite_started.id,
        label=login_operation.label,
        outcome=login_outcome,
    )
    if login_outcome.error is not None or login_outcome.response is None:
        yield from _finish_with_failure(
            phase=phase,
            suite_id=suite_started.id,
            payload=_login_failure_payload(spec, login_outcome),
        )
        return

    provider = DynamicTokenAuthProvider(
        path=token_config.path,
        method=token_config.method,
        payload=creds,
        extract_from=token_config.extract_from,
        extract_selector=token_config.extract_selector,
        _applier=_build_applier(schema, spec.target_scheme),
    )
    try:
        token = _extract_token_from_response(provider, login_outcome.response)
    except LoginRequestError as exc:
        yield from _finish_with_failure(
            phase=phase,
            suite_id=suite_started.id,
            payload=AuthBootstrapPayload(spec=spec, status=Status.ERROR, failure_stage="login", message=exc.message),
        )
        return
    except TokenExtractionError as exc:
        yield from _finish_with_failure(
            phase=phase,
            suite_id=suite_started.id,
            payload=AuthBootstrapPayload(spec=spec, status=Status.ERROR, failure_stage="extract", message=exc.message),
        )
        return

    schema.bootstrapped_session = BootstrappedSession(
        credentials=creds,
        token=token,
    )
    yield events.SuiteFinished(id=suite_started.id, phase=PhaseName.AUTH_BOOTSTRAP, status=Status.SUCCESS)
    yield events.PhaseFinished(
        phase=phase,
        status=Status.SUCCESS,
        payload=AuthBootstrapPayload(spec=spec, status=Status.SUCCESS),
    )


def _finish_with_failure(*, phase: Phase, suite_id: uuid.UUID, payload: AuthBootstrapPayload) -> EventGenerator:
    yield events.SuiteFinished(id=suite_id, phase=PhaseName.AUTH_BOOTSTRAP, status=Status.ERROR)
    yield events.PhaseFinished(phase=phase, status=Status.ERROR, payload=payload)


def _execute_call_scenario(
    ctx: EngineContext,
    *,
    operation: APIOperation,
    body: dict[str, str],
    method: str | None = None,
    path: str | None = None,
) -> _ScenarioOutcome:
    """Build a Case, dispatch via `case.call(...)`, and capture the interaction in a recorder."""
    import requests

    schema = ctx.schema
    case = schema.make_case(
        operation=operation,
        method=(method or operation.method).upper(),
        path=path or operation.path,
        body=body,
    )
    recorder = ScenarioRecorder(label=operation.label)
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)

    transport_kwargs = _bootstrap_transport_kwargs(ctx, operation=operation)
    start = time.monotonic()
    try:
        response = case.call(**transport_kwargs)
    except (requests.Timeout, requests.ConnectionError) as exc:
        elapsed = time.monotonic() - start
        prepared = exc.request
        if isinstance(prepared, requests.Request):
            recorder.record_request(case_id=case.id, request=prepared.prepare())
        elif isinstance(prepared, requests.PreparedRequest):
            recorder.record_request(case_id=case.id, request=prepared)
        return _ScenarioOutcome(recorder=recorder, response=None, status=Status.ERROR, elapsed_time=elapsed, error=exc)
    except Exception as exc:
        elapsed = time.monotonic() - start
        return _ScenarioOutcome(recorder=recorder, response=None, status=Status.ERROR, elapsed_time=elapsed, error=exc)

    elapsed = time.monotonic() - start
    recorder.record_response(case_id=case.id, response=response)
    status = Status.SUCCESS if _is_success_response(response) else Status.FAILURE
    return _ScenarioOutcome(recorder=recorder, response=response, status=status, elapsed_time=elapsed, error=None)


def _emit_scenario_events(
    *,
    phase: PhaseName,
    suite_id: uuid.UUID,
    label: str,
    outcome: _ScenarioOutcome,
) -> EventGenerator:
    started = events.ScenarioStarted(phase=phase, suite_id=suite_id, label=label)
    yield started
    yield events.ScenarioFinished(
        id=started.id,
        suite_id=suite_id,
        phase=phase,
        label=label,
        status=outcome.status,
        recorder=outcome.recorder,
        elapsed_time=outcome.elapsed_time,
        skip_reason=None,
        is_final=False,
    )


def _bootstrap_transport_kwargs(ctx: EngineContext, *, operation: APIOperation) -> dict[str, Any]:
    """Build a minimal `case.call(...)` kwargs dict that is safe across HTTP, WSGI, and ASGI transports.

    The engine's full `transport_kwargs` carries a `requests.Session` and `cert`/`max_redirects`
    flags that the WSGI transport forwards verbatim into Werkzeug's `client.open`, where they
    are rejected. Bootstrap only needs the values that affect the wire-level call: headers for
    every transport, plus timeout/verify on HTTP and ASGI (which both flow through `requests`).
    """
    from schemathesis.transport import is_asgi_app

    config = ctx.config
    kwargs: dict[str, Any] = {}
    headers = config.headers_for(operation=operation)
    if headers:
        kwargs["headers"] = headers
    app = ctx.schema.app
    is_wsgi = app is not None and not is_asgi_app(app)
    if not is_wsgi:
        timeout = config.request_timeout_for(operation=operation)
        if timeout is not None:
            kwargs["timeout"] = timeout
        verify = config.tls_verify_for(operation=operation)
        if verify is not None:
            kwargs["verify"] = verify
    return kwargs


def _operation_by_label(schema: OpenApiSchema, label: str) -> APIOperation:
    operation = schema.find_operation_by_label(label)
    if operation is None:
        raise RuntimeError(f"operation not found: {label}")
    return operation


def _is_success_response(response: Response | None) -> bool:
    if response is None:
        return False
    return 200 <= response.status_code < 300


def _register_failure_payload(spec: AuthFlowSpec, outcome: _ScenarioOutcome) -> AuthBootstrapPayload:
    if outcome.error is not None:
        return AuthBootstrapPayload(
            spec=spec,
            status=Status.ERROR,
            failure_stage="register",
            message=f"transport failure: {outcome.error}",
        )
    response = outcome.response
    assert response is not None
    return AuthBootstrapPayload(
        spec=spec,
        status=Status.ERROR,
        failure_stage="register",
        status_code=response.status_code,
        message=response.text[:500],
    )


def _login_failure_payload(spec: AuthFlowSpec, outcome: _ScenarioOutcome) -> AuthBootstrapPayload:
    error = outcome.error
    assert error is not None
    return AuthBootstrapPayload(
        spec=spec,
        status=Status.ERROR,
        failure_stage="login",
        message=f"transport failure: {error}",
    )


def _extract_token_from_response(provider: DynamicTokenAuthProvider, response: Response) -> str:
    """Adapt `core.transport.Response` to the provider's text/get_json/headers interface."""
    flat_headers = {key: value[0] for key, value in response.headers.items() if value}
    return provider.extract_token(
        status_code=response.status_code,
        text=response.text,
        get_json=response.json,
        headers=flat_headers,
    )


def _build_applier(schema: OpenApiSchema, scheme_name: str) -> HttpBearerAuthProvider | ApiKeyAuthProvider:
    """Build the per-request applier that injects the fetched token using the target scheme's wire format."""
    definition = schema.security.security_definitions.get(scheme_name, {})
    scheme_type = definition.get("type")
    if scheme_type == "http" and str(definition.get("scheme", "")).lower() == "bearer":
        return HttpBearerAuthProvider(bearer="")
    if scheme_type == "apiKey":
        return ApiKeyAuthProvider(value="", name=definition["name"], location=definition["in"])
    raise RuntimeError(f"unsupported security scheme: {scheme_name}")
