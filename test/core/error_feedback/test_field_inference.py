from __future__ import annotations

import pytest

from schemathesis.core.error_feedback.field_inference import infer_path_from_request
from schemathesis.core.parameters import ParameterLocation


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
