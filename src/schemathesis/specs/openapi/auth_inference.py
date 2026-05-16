"""Helpers for inferring missing OpenAPI security from runtime 401/403 responses."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from schemathesis.auths import AuthContext
from schemathesis.core.error_feedback import (
    ErrorFeedbackStore,
    Observation,
    ObservationKind,
    RequiresAuthPayload,
)
from schemathesis.core.parameters import ParameterLocation
from schemathesis.specs.openapi._auth_retry import build_retry_transport_kwargs, clone_case
from schemathesis.specs.openapi.adapter.security import get_effective_security_scheme_names
from schemathesis.specs.openapi.schemas import OpenApiSchema

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.schemas import OpenApiCase


def _intersect_configured_schemes(schema: OpenApiSchema) -> list[str]:
    """Return scheme names declared in the spec that also have configured credentials."""
    configured = schema.config.auth.all_openapi_schemes
    if not configured:
        return []
    declared = schema.security.security_definitions
    return [name for name in configured if name in declared]


def _already_inferred(store: ErrorFeedbackStore, operation_label: str) -> bool:
    """Return True iff a `REQUIRES_AUTHENTICATION` observation already exists for this operation."""
    observations = store.observations(
        operation_label=operation_label,
        location=ParameterLocation.PATH,
    )
    return any(observation.kind is ObservationKind.REQUIRES_AUTHENTICATION for observation in observations)


def _has_declared_security(operation: APIOperation) -> bool:
    """Return True iff the operation declares any security (own, schema-level fallback, or inferred overlay)."""
    return bool(get_effective_security_scheme_names(operation, operation.schema.raw_schema))


def _is_auth_success(operation: APIOperation, retry_status: int) -> bool:
    """A retry confirms inference iff it slips past the auth gate without crashing the server."""
    if 200 <= retry_status < 300:
        return True
    if retry_status in {401, 403} or retry_status >= 500:
        return False
    # 4xx outside the auth band confirms only when the spec actually documents that status —
    # otherwise an undocumented 404/405 (route mismatch) would falsely look like inference success.
    return operation.responses.find_by_status_code(retry_status) is not None


def _send_with_scheme(
    *,
    case: OpenApiCase,
    scheme_name: str,
    transport_kwargs: dict[str, Any],
    recorder: ScenarioRecorder,
) -> Response | None:
    """Retry `case` with the named scheme's configured credentials applied; record the retry.

    Returns `None` if the probe could not run end-to-end — provider failures, transport errors,
    or any other unexpected exception. The caller treats `None` as "skip this scheme".
    """
    schema = case.operation.schema
    config = schema.config.auth.all_openapi_schemes[scheme_name]

    retry_case = clone_case(case)
    recorder.record_case(parent_id=case.id, case=retry_case, transition=None, is_transition_applied=False)

    context = AuthContext(operation=case.operation, app=case.operation.app)

    kwargs = build_retry_transport_kwargs(transport_kwargs, [])
    if case.operation.app is not None:
        kwargs.setdefault("app", case.operation.app)

    try:
        provider = schema.security.auth_provider_for(scheme_name, config)
        data = provider.get(retry_case, context)
        provider.set(retry_case, data, context)
        response = schema.transport.send(retry_case, **kwargs)
    except Exception:  # noqa: BLE001 - probe must not propagate; one bad scheme can't kill the loop.
        return None

    recorder.record_response(case_id=retry_case.id, response=response)
    return response


def record_auth_inference(
    *,
    store: ErrorFeedbackStore,
    recorder: ScenarioRecorder,
    case: Case,
    response: Response,
    transport_kwargs: dict[str, Any],
) -> None:
    """Infer a missing security requirement when the server enforces auth on a publicly-declared operation.

    Fires only on 401/403 responses to operations with no declared security. For each
    configured scheme that the spec also declares, retries the case with that scheme's
    credentials; the first retry that confirms auth recovery wins and the scheme is recorded
    on the operation as a runtime overlay so future generations attach the same credentials.
    Each operation is inferred at most once. Probe failures (provider errors, network errors,
    5xx, undocumented 4xx) skip the current scheme.
    """
    if response.status_code not in (401, 403):
        return
    operation = case.operation
    if not isinstance(operation.schema, OpenApiSchema):
        return
    if _has_declared_security(operation):
        return
    if _already_inferred(store, operation.label):
        return

    candidate_schemes = _intersect_configured_schemes(operation.schema)
    if not candidate_schemes:
        return

    for scheme_name in candidate_schemes:
        retry_response = _send_with_scheme(
            case=case,
            scheme_name=scheme_name,
            transport_kwargs=transport_kwargs,
            recorder=recorder,
        )
        if retry_response is None or not _is_auth_success(operation, retry_response.status_code):
            continue

        store.record(
            Observation(
                operation_label=operation.label,
                location=ParameterLocation.PATH,
                parameter_path=(),
                kind=ObservationKind.REQUIRES_AUTHENTICATION,
                raw_message=f"{response.status_code} fixed by {scheme_name}",
                payload=RequiresAuthPayload(scheme_name=scheme_name),
            )
        )
        # Stored on the schema (not the operation) because operation instances aren't shared
        # across phases — the engine reparses operations per phase.
        operation.schema._inferred_security[operation.label] = [{scheme_name: []}]
        return
