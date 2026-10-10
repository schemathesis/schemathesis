from __future__ import annotations

import pytest

from schemathesis.core.error_feedback import (
    BoundDirection,
    NumericBoundPayload,
    ObservationKind,
    PatternPayload,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.error_feedback.parsers.ajv import AjvParser
from schemathesis.core.error_feedback.parsers.aspnet import AspNetParser
from schemathesis.core.error_feedback.parsers.confluent import ConfluentParser
from schemathesis.core.error_feedback.parsers.drf import DRFParser
from schemathesis.core.error_feedback.parsers.flask_rest import FlaskRestParser
from schemathesis.core.error_feedback.parsers.go_validator import GoValidatorParser
from schemathesis.core.error_feedback.parsers.jackson import JacksonParser
from schemathesis.core.error_feedback.parsers.laravel import LaravelParser
from schemathesis.core.error_feedback.parsers.litestar import LitestarParser
from schemathesis.core.error_feedback.parsers.marshmallow import MarshmallowParser
from schemathesis.core.error_feedback.parsers.pydantic import PydanticParser
from schemathesis.core.error_feedback.parsers.symfony import SymfonyParser
from schemathesis.core.error_feedback.parsers.zod import ZodParser
from schemathesis.core.parameters import ParameterLocation
from test.core.error_feedback.parsers.helpers import parse_observations


# Wire envelope captured against a running Litestar app: `key` is a dotted/indexed path,
# `source` is the request location.
def _litestar(*issues: dict[str, str], path: str = "/users", method: str = "POST") -> dict:
    return {
        "status_code": 400,
        "detail": f"Validation failed for {method} {path}",
        "extra": list(issues),
    }


def _issue(key: str, message: str, *, source: str = "body") -> dict[str, str]:
    return {"key": key, "message": message, "source": source}


_LITESTAR_ACCEPTED_BODIES = [
    pytest.param(
        _litestar(_issue("data", "Object missing required field `username`")),
        id="required-root",
    ),
    pytest.param(
        _litestar(_issue("address", "Object missing required field `street`")),
        id="required-nested",
    ),
    pytest.param(
        _litestar(_issue("username", "Expected `str` of length >= 3")),
        id="min-length",
    ),
    pytest.param(
        _litestar(_issue("username", "Expected `str` of length <= 30")),
        id="max-length",
    ),
    pytest.param(
        _litestar(_issue("items", "Expected `array` of length >= 1")),
        id="min-items",
    ),
    pytest.param(
        _litestar(_issue("items", "Expected `array` of length <= 5")),
        id="max-items",
    ),
    pytest.param(
        _litestar(_issue("email", "Expected `str` matching regex '^[^@]+@[^@]+$'")),
        id="regex",
    ),
    pytest.param(
        _litestar(_issue("age", "Expected `int` >= 0")),
        id="ge",
    ),
    pytest.param(
        _litestar(_issue("age", "Expected `int` <= 130")),
        id="le",
    ),
    pytest.param(
        _litestar(_issue("total", "Expected `float` > 0.0")),
        id="gt",
    ),
    pytest.param(
        _litestar(_issue("total", "Expected `float` < 100.0")),
        id="lt",
    ),
    pytest.param(
        _litestar(_issue("username", "Expected `str`, got `int`")),
        id="type-mismatch",
    ),
    pytest.param(
        _litestar(_issue("age", "Expected `int | null`, got `str`")),
        id="type-mismatch-union",
    ),
    pytest.param(
        _litestar(_issue("address.street", "Expected `str` of length >= 2")),
        id="nested-dotted-path",
    ),
    pytest.param(
        _litestar(_issue("items[0]", "Expected `str`, got `int`")),
        id="array-element-path",
    ),
    pytest.param(
        _litestar(
            _issue("username", "Expected `str` of length >= 3"),
            _issue("email", "Expected `str` matching regex '^[^@]+@[^@]+$'"),
        ),
        id="multiple-issues",
    ),
]


@pytest.mark.parametrize("body", _LITESTAR_ACCEPTED_BODIES)
def test_litestar_parser_can_parse_recognises_envelope(body):
    assert LitestarParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        # Right top-level shape but no `extra`.
        {"status_code": 400, "detail": "Validation failed"},
        # `extra` is a list but items aren't issue dicts.
        {"status_code": 400, "detail": "Validation failed", "extra": ["just a string"]},
        # Wrong `status_code` (not a 4xx-style integer).
        {"status_code": "400", "detail": "x", "extra": [{"message": "y", "key": "z", "source": "body"}]},
        # Recognised structure but no recognised message.
        {
            "status_code": 400,
            "detail": "Validation failed",
            "extra": [{"message": "something custom", "key": "x", "source": "body"}],
        },
        # Spring shape.
        {"messages": ["x - y"]},
        # Marshmallow shape.
        {"username": ["Missing data for required field."]},
        # AJV shape.
        {"errors": [{"keyword": "format", "instancePath": "/x", "params": {}}]},
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "no-extra",
        "extra-not-issue-dicts",
        "stringy-status-code",
        "unrecognised-message",
        "spring-shape",
        "marshmallow-shape",
        "ajv-shape",
    ],
)
def test_litestar_parser_can_parse_rejects_non_litestar_bodies(body):
    assert LitestarParser().can_parse(body=body) is False


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            _litestar(_issue("data", "Object missing required field `username`")),
            ((("username",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="required-root",
        ),
        pytest.param(
            _litestar(_issue("address", "Object missing required field `street`")),
            ((("address", "street"), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="required-nested",
        ),
        pytest.param(
            _litestar(_issue("username", "Expected `str` of length >= 3")),
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),),
            id="min-length",
        ),
        pytest.param(
            _litestar(_issue("username", "Expected `str` of length <= 30")),
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=30)),),
            id="max-length",
        ),
        pytest.param(
            _litestar(_issue("items", "Expected `array` of length >= 1")),
            ((("items",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),),
            id="min-items",
        ),
        pytest.param(
            _litestar(_issue("items", "Expected `array` of length <= 5")),
            ((("items",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=5)),),
            id="max-items",
        ),
        pytest.param(
            _litestar(_issue("email", "Expected `str` matching regex '^[^@]+@[^@]+$'")),
            ((("email",), ObservationKind.PATTERN, PatternPayload(regex="^[^@]+@[^@]+$")),),
            id="regex",
        ),
        pytest.param(
            _litestar(_issue("age", "Expected `int` >= 0")),
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="ge",
        ),
        pytest.param(
            _litestar(_issue("age", "Expected `int` <= 130")),
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=130.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="le",
        ),
        pytest.param(
            _litestar(_issue("total", "Expected `float` > 0.0")),
            (
                (
                    ("total",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
                ),
            ),
            id="gt",
        ),
        pytest.param(
            _litestar(_issue("total", "Expected `float` < 100.0")),
            (
                (
                    ("total",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=True),
                ),
            ),
            id="lt",
        ),
        pytest.param(
            _litestar(_issue("username", "Expected `str`, got `int`")),
            ((("username",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="string")),),
            id="type-mismatch",
        ),
        pytest.param(
            _litestar(_issue("age", "Expected `int | null`, got `str`")),
            ((("age",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer")),),
            id="type-mismatch-nullable",
        ),
        pytest.param(
            _litestar(_issue("ratio", "Expected `float`, got `str`")),
            ((("ratio",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="number")),),
            id="type-mismatch-float",
        ),
        pytest.param(
            _litestar(_issue("address.street", "Expected `str` of length >= 2")),
            ((("address", "street"), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=2, max=None)),),
            id="nested-dotted-path",
        ),
        pytest.param(
            _litestar(_issue("items[0]", "Expected `str`, got `int`")),
            ((("items", 0), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="string")),),
            id="array-element-path",
        ),
    ],
)
def test_litestar_parser_parse(make_operation, case_factory, body, expected):
    assert (
        tuple(
            (o.parameter_path, o.kind, o.payload)
            for o in parse_observations(LitestarParser(), body, make_operation, case_factory)
        )
        == expected
    )


def test_litestar_parser_multi_field(make_operation, case_factory):
    body = _litestar(
        _issue("username", "Expected `str` of length >= 3"),
        _issue("email", "Expected `str` matching regex '^[^@]+@[^@]+$'"),
        _issue("age", "Expected `int` <= 130"),
    )
    assert sorted(
        o.parameter_path for o in parse_observations(LitestarParser(), body, make_operation, case_factory)
    ) == [("age",), ("email",), ("username",)]


@pytest.mark.parametrize(
    ("source", "location"),
    [
        ("query", ParameterLocation.QUERY),
        ("path", ParameterLocation.PATH),
        ("header", ParameterLocation.HEADER),
        ("cookie", ParameterLocation.COOKIE),
    ],
)
def test_litestar_parser_routes_source_to_location(make_operation, case_factory, source, location):
    body = _litestar(_issue("field", "Expected `int` >= 1", source=source))
    assert tuple(o.location for o in parse_observations(LitestarParser(), body, make_operation, case_factory)) == (
        location,
    )


def test_litestar_parser_drops_unknown_message(make_operation, case_factory):
    assert (
        parse_observations(
            LitestarParser(),
            _litestar(_issue("x", "something not in our regex set")),
            make_operation,
            case_factory,
        )
        == ()
    )


def test_litestar_parser_parse_returns_empty_for_non_envelope(make_operation, case_factory):
    assert parse_observations(LitestarParser(), {"detail": "nope"}, make_operation, case_factory) == ()


@pytest.mark.parametrize(
    ("body", "expected_path"),
    [
        pytest.param(
            _litestar(_issue("data", "Expected `object`, got `null`")),
            (),
            id="null-body",
        ),
        pytest.param(
            _litestar(_issue("username", "Expected `str`, got `null`")),
            ("username",),
            id="null-field",
        ),
        pytest.param(
            _litestar(_issue("address.street", "Expected `str`, got `null`")),
            ("address", "street"),
            id="null-nested-field",
        ),
    ],
)
def test_litestar_parser_null_value_emits_must_not_be_blank(make_operation, case_factory, body, expected_path):
    assert tuple(
        (o.parameter_path, o.kind, o.payload)
        for o in parse_observations(LitestarParser(), body, make_operation, case_factory)
    ) == ((expected_path, ObservationKind.MUST_NOT_BE_BLANK, None),)


@pytest.mark.parametrize(
    "body",
    [
        # `int | str` accepted server-side: narrowing to one arm would reject inputs
        # the server actually takes. Drop the type-mismatch observation entirely.
        _litestar(_issue("score", "Expected `int | str`, got `array`")),
        _litestar(_issue("ratio", "Expected `int | float | str`, got `bool`")),
    ],
    ids=["two-arm-union", "three-arm-union"],
)
def test_litestar_parser_drops_multi_arm_union_type_mismatch(make_operation, case_factory, body):
    assert parse_observations(LitestarParser(), body, make_operation, case_factory) == ()


def test_litestar_parser_strips_null_arm_from_optional_union(make_operation, case_factory):
    assert tuple(
        (o.parameter_path, o.kind, o.payload)
        for o in parse_observations(
            LitestarParser(),
            _litestar(_issue("age", "Expected `int | null`, got `str`")),
            make_operation,
            case_factory,
        )
    ) == ((("age",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer")),)


def test_litestar_parser_null_on_multi_arm_union_emits_must_not_be_blank(make_operation, case_factory):
    # Nullability is independent of type — drop applies only when narrowing the type would be wrong.
    assert tuple(
        (o.parameter_path, o.kind, o.payload)
        for o in parse_observations(
            LitestarParser(),
            _litestar(_issue("value", "Expected `int | float | str`, got `null`")),
            make_operation,
            case_factory,
        )
    ) == ((("value",), ObservationKind.MUST_NOT_BE_BLANK, None),)


@pytest.mark.parametrize(
    "parser",
    [
        AjvParser(),
        AspNetParser(),
        ConfluentParser(),
        DRFParser(),
        FlaskRestParser(),
        GoValidatorParser(),
        LaravelParser(),
        MarshmallowParser(),
        PydanticParser(),
        JacksonParser(),
        SymfonyParser(),
        ZodParser(),
    ],
    ids=lambda p: type(p).__name__,
)
@pytest.mark.parametrize("body", _LITESTAR_ACCEPTED_BODIES)
def test_other_parsers_reject_litestar_bodies(parser, body):
    assert parser.can_parse(body=body) is False
