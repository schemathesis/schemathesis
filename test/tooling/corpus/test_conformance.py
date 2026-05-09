from __future__ import annotations

import dataclasses

import pytest

from tools.corpus.conformance import BodyViolation, evaluate_body_conformance

SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
VALID_BODY = {"name": "alice"}
INVALID_BODY = {"name": 42}
BROKEN_SCHEMA = {"type": "object", "properties": {"x": {"$ref": "#/missing"}}}


@pytest.mark.parametrize(
    ("body", "schema", "is_negative_body", "expected"),
    [
        (VALID_BODY, SCHEMA, False, None),
        (INVALID_BODY, SCHEMA, False, BodyViolation("application/json", INVALID_BODY, expected_valid=True)),
        (INVALID_BODY, SCHEMA, True, None),
        (VALID_BODY, SCHEMA, True, BodyViolation("application/json", VALID_BODY, expected_valid=False)),
        ({"x": 1}, BROKEN_SCHEMA, False, None),
    ],
    ids=[
        "positive-valid",
        "positive-invalid",
        "negative-invalid",
        "negative-valid",
        "invalid-schema",
    ],
)
def test_evaluate_body_conformance(body, schema, is_negative_body, expected):
    result = evaluate_body_conformance(
        body=body,
        media_type="application/json",
        schema=schema,
        validator_cls=None,
        is_negative_body=is_negative_body,
    )
    if expected is None:
        assert result is None
        return
    assert result is not None
    assert dataclasses.replace(result, errors=()) == expected
    assert bool(result.errors) is expected.expected_valid
