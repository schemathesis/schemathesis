from __future__ import annotations

import pytest

from schemathesis.core.error_feedback import ErrorFeedbackStore, Observation, ObservationKind, TypeMismatchPayload
from schemathesis.core.error_feedback.field_inference import infer_path_from_request
from schemathesis.core.parameters import ParameterLocation
from test.core.error_feedback.helpers import record_400


def _nested(value: object, depth: int, *, as_list: bool = False) -> object:
    for _ in range(depth):
        value = [value] if as_list else {"next": value}
    return value


@pytest.mark.parametrize(
    "value", ["", "0", "9", "x", "\\"], ids=["empty", "zero", "nine", "single-char", "single-backslash"]
)
def test_short_values_skip(case_factory, value):
    case = case_factory(body={"name": value})
    assert infer_path_from_request(case=case, rejected_value=value) is None


@pytest.mark.parametrize("value", ["true", "false", "null"])
def test_blocklisted_values_skip(case_factory, value):
    case = case_factory(body={"flag": value})
    assert infer_path_from_request(case=case, rejected_value=value) is None


@pytest.mark.parametrize(
    ("case_kwargs", "rejected_value"),
    [
        ({"body": {}}, "DISTINCT-VALUE-2026"),
        # str(False) == "False" but the wire form is "false" — must use json.dumps to compare.
        ({"body": {"flag": False, "name": "ALPHA-BRAVO-2026"}}, "False"),
        ({"body": {"name": "alice", "score": 42}}, "2024-01-15"),
        ({"body": _nested("DEEPLY-NESTED-VALUE", 33)}, "DEEPLY-NESTED-VALUE"),
        ({"body": _nested("DEEPLY-NESTED-VALUE", 33, as_list=True)}, "DEEPLY-NESTED-VALUE"),
        ({"body": {"file": b"binary-payload-bytes", "name": "alice"}}, "DOES-NOT-MATCH"),
        ({"body": {"primary": "TWIN-VALUE-2026", "secondary": "TWIN-VALUE-2026"}}, "TWIN-VALUE-2026"),
    ],
    ids=[
        "empty-body",
        "wire-form-mismatch",
        "value-not-present",
        "depth-cap",
        "depth-cap-lists",
        "bytes-leaf",
        "ambiguous-multi-candidate",
    ],
)
def test_no_attribution(case_factory, case_kwargs, rejected_value):
    case = case_factory(**case_kwargs)
    assert infer_path_from_request(case=case, rejected_value=rejected_value) is None


@pytest.mark.parametrize(
    ("case_kwargs", "rejected_value", "expected"),
    [
        (
            {"body": {"commitDate": "dd-MM-yyyy", "comment": "team standup"}},
            "dd-MM-yyyy",
            (ParameterLocation.BODY, ("commitDate",)),
        ),
        (
            {
                "body": {
                    "shipping": {"trackingNumber": "TRK-2026-AABB"},
                    "items": [{"sku": "OTHER-VALUE"}],
                }
            },
            "TRK-2026-AABB",
            (ParameterLocation.BODY, ("shipping", "trackingNumber")),
        ),
        (
            {"query": {"from": "dd-MM-yyyy-HHmm", "limit": "10"}},
            "dd-MM-yyyy-HHmm",
            (ParameterLocation.QUERY, ("from",)),
        ),
        ({"path_parameters": {"id": "PATH-VALUE-2026"}}, "PATH-VALUE-2026", (ParameterLocation.PATH, ("id",))),
        ({"body": {"code": "ABCD"}}, "ABCD", (ParameterLocation.BODY, ("code",))),
        ({"body": {"count": 20260115}}, "20260115", (ParameterLocation.BODY, ("count",))),
        ({"body": _nested("BOUNDARY-VALUE", 32)}, "BOUNDARY-VALUE", (ParameterLocation.BODY, ("next",) * 32)),
        (
            {"body": _nested("BOUNDARY-VALUE", 32, as_list=True)},
            "BOUNDARY-VALUE",
            (ParameterLocation.BODY, (0,) * 32),
        ),
        (
            {"body": {"name": "SHALLOW-VALUE", "deep": _nested("x", 40)}},
            "SHALLOW-VALUE",
            (ParameterLocation.BODY, ("name",)),
        ),
    ],
    ids=[
        "body-flat",
        "body-nested",
        "query",
        "path-after-empty-query",
        "minimum-length-value",
        "number-wire-form",
        "depth-limit",
        "depth-limit-lists",
        "past-a-too-deep-branch",
    ],
)
def test_attributes_matching_slot(case_factory, case_kwargs, rejected_value, expected):
    case = case_factory(**case_kwargs)
    assert infer_path_from_request(case=case, rejected_value=rejected_value) == expected


@pytest.mark.parametrize(
    ("body", "query", "expected"),
    [
        (["filler"] * 10_000, {}, None),
        (["filler"], {}, (ParameterLocation.QUERY, ("name",))),
        (["filler"] * 9_997, {"a": "1"}, (ParameterLocation.QUERY, ("name",))),
        (["filler"] * 9_997, {"a": "1", "b": "2"}, None),
    ],
    ids=["body-exhausts-walk-budget", "small-body", "two-nodes-left-reach-match", "two-nodes-left-end-before-match"],
)
def test_query_attribution_depends_on_remaining_walk_budget(case_factory, body, query, expected):
    case = case_factory(body=body, query={**query, "name": "QUERY-VALUE-2026"})
    assert infer_path_from_request(case=case, rejected_value="QUERY-VALUE-2026") == expected


@pytest.mark.parametrize(
    ("operation_path", "case_kwargs", "rejected_value", "type_name", "expected_location", "expected_path"),
    [
        # Two body fields share the rejected value -> ambiguous, no observation.
        (
            "/api/items",
            {"body": {"identifier": "DELTA-2026-XYZ", "fallback_id": "DELTA-2026-XYZ", "count": 99}, "method": "POST"},
            "DELTA-2026-XYZ",
            "java.time.LocalDate",
            ParameterLocation.BODY,
            None,
        ),
        # Rejected value lives in a query parameter, not the body.
        (
            "/api/reports",
            {"query": {"from": "dd-MM-yyyy-HHmm", "limit": "10"}, "method": "GET"},
            "dd-MM-yyyy-HHmm",
            "java.time.LocalDateTime",
            ParameterLocation.QUERY,
            ("from",),
        ),
        # Rejected value isn't in the request — likely a server-side default.
        (
            "/api/users",
            {"body": {"name": "alice", "score": 42}, "method": "POST"},
            "2024-01-15",
            "java.time.LocalDate",
            ParameterLocation.BODY,
            None,
        ),
        # Nested ambiguity: same value in both `shipping.trackingNumber` and `items[0].sku`.
        (
            "/api/orders",
            {
                "body": {"shipping": {"trackingNumber": "TRK-2026-AABB"}, "items": [{"sku": "TRK-2026-AABB"}]},
                "method": "POST",
            },
            "TRK-2026-AABB",
            "java.time.LocalDate",
            ParameterLocation.BODY,
            None,
        ),
        # Distinct nested match — only one candidate.
        (
            "/api/orders",
            {
                "body": {"shipping": {"trackingNumber": "TRK-2026-AABB"}, "items": [{"sku": "DIFFERENT-VALUE"}]},
                "method": "POST",
            },
            "TRK-2026-AABB",
            "java.time.LocalDate",
            ParameterLocation.BODY,
            ("shipping", "trackingNumber"),
        ),
    ],
    ids=[
        "ambiguous-flat-body",
        "query-attribution",
        "value-not-in-request",
        "ambiguous-nested",
        "distinct-nested",
    ],
)
def test_field_inference_attribution_through_pipeline(
    make_operation,
    case_factory,
    response_factory,
    operation_path,
    case_kwargs,
    rejected_value,
    type_name,
    expected_location,
    expected_path,
):
    method = case_kwargs["method"]
    operation = make_operation(method=method.lower(), path=operation_path)
    case = case_factory(operation=operation, **case_kwargs)
    response_body = {"message": f'Cannot deserialize value of type `{type_name}` from String "{rejected_value}"'}

    store = ErrorFeedbackStore()
    for _ in range(2):
        record_400(store, operation=operation, case=case, body=response_body, response_factory=response_factory)

    expected: tuple[Observation, ...] = (
        ()
        if expected_path is None
        else (
            Observation(
                operation_label=operation.label,
                location=expected_location,
                parameter_path=expected_path,
                kind=ObservationKind.TYPE_MISMATCH,
                raw_message=response_body["message"],
                payload=TypeMismatchPayload(type_name=type_name),
            ),
        )
    )
    assert store.observations(operation_label=operation.label, location=expected_location) == expected


# Realistic Jackson 400 envelopes for typical Spring deployments without `INCLUDE_FIELD_PATH_IN_ERRORS`.
_JACKSON_NO_REFERENCE_CHAIN_CASES: tuple[dict, ...] = (
    {
        "id": "localdate-dash-format",
        "method": "POST",
        "path": "/api/records",
        "body": {"employeeId": 7, "projectId": 12, "commitDate": "dd-MM-yyyy", "comment": "x", "billable": True},
        "response": {
            "message": 'JSON parse error: Cannot deserialize value of type `java.time.LocalDate` from String "dd-MM-yyyy"'
        },
        "expected_path": ("commitDate",),
        "expected_payload": TypeMismatchPayload(type_name="java.time.LocalDate"),
    },
    {
        "id": "localdate-slash-format",
        "method": "POST",
        "path": "/api/profiles",
        "body": {"name": "Alice", "hireDate": "MM/dd/yyyy", "departmentId": 3},
        "response": {"message": 'Cannot deserialize value of type `java.time.LocalDate` from String "MM/dd/yyyy"'},
        "expected_path": ("hireDate",),
        "expected_payload": TypeMismatchPayload(type_name="java.time.LocalDate"),
    },
)


@pytest.mark.parametrize("payload", _JACKSON_NO_REFERENCE_CHAIN_CASES, ids=lambda p: p["id"])
def test_field_inference_jackson_envelopes_without_reference_chain(
    payload, make_operation, case_factory, response_factory
):
    operation = make_operation(method=payload["method"].lower(), path=payload["path"])
    case = case_factory(operation=operation, body=payload["body"], method=payload["method"])

    store = ErrorFeedbackStore()
    for _ in range(2):
        record_400(store, operation=operation, case=case, body=payload["response"], response_factory=response_factory)

    assert store.observations(operation_label=operation.label, location=ParameterLocation.BODY) == (
        Observation(
            operation_label=operation.label,
            location=ParameterLocation.BODY,
            parameter_path=payload["expected_path"],
            kind=ObservationKind.TYPE_MISMATCH,
            raw_message=payload["response"]["message"],
            payload=payload["expected_payload"],
        ),
    )
