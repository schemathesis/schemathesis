from __future__ import annotations

import pytest

from schemathesis.core.error_feedback.field_inference import infer_path_from_request
from schemathesis.core.parameters import ParameterLocation


def _deeply_nested_body() -> dict[str, object]:
    body: dict[str, object] = {}
    nested = body
    for _ in range(40):
        nested["next"] = {}
        nested = nested["next"]
    nested["leaf"] = "DEEPLY-NESTED-VALUE"
    return body


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
        ({"body": _deeply_nested_body()}, "DEEPLY-NESTED-VALUE"),
        ({"body": {"file": b"binary-payload-bytes", "name": "alice"}}, "DOES-NOT-MATCH"),
        ({"body": {"primary": "TWIN-VALUE-2026", "secondary": "TWIN-VALUE-2026"}}, "TWIN-VALUE-2026"),
    ],
    ids=[
        "empty-body",
        "wire-form-mismatch",
        "value-not-present",
        "depth-cap",
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
    ],
    ids=["body-flat", "body-nested", "query"],
)
def test_attributes_matching_slot(case_factory, case_kwargs, rejected_value, expected):
    case = case_factory(**case_kwargs)
    assert infer_path_from_request(case=case, rejected_value=rejected_value) == expected
