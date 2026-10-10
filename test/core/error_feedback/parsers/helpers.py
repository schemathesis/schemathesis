from __future__ import annotations

from typing import Any

from schemathesis.core.error_feedback import (
    Observation,
    ObservationKind,
    ObservationPayload,
)
from schemathesis.core.parameters import ParameterLocation


def parse_observations(
    parser,
    body,
    make_operation,
    case_factory,
    *,
    method: str = "post",
    path: str = "/api/users",
    case_kwargs: dict[str, Any] | None = None,
):
    operation = make_operation(method=method, path=path)
    return parser.parse(operation=operation, body=body, case=case_factory(**(case_kwargs or {})))


SPRING_MESSAGES = (
    b'{"error":"Bad Request","status":400,'
    b'"messages":["zipcode - must not be blank","city - must not be blank"],'
    b'"timestamp":"2026-04-30T18:08:24Z"}'
)


def drf_obs(
    *,
    op: str,
    location: ParameterLocation,
    path: tuple[str | int, ...],
    kind: ObservationKind,
    raw_message: str,
    payload: ObservationPayload = None,
) -> Observation:
    return Observation(
        operation_label=op,
        location=location,
        parameter_path=path,
        kind=kind,
        raw_message=raw_message,
        payload=payload,
    )


DRF_DETAIL_WRAPPED_BODY = {"detail": {"tags": ["This field may not be blank."]}}
