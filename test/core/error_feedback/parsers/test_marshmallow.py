from __future__ import annotations

import pytest

from schemathesis.core.error_feedback import (
    BoundDirection,
    EnumPayload,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.error_feedback.parsers.ajv import AjvParser
from schemathesis.core.error_feedback.parsers.aspnet import AspNetParser
from schemathesis.core.error_feedback.parsers.confluent import ConfluentParser
from schemathesis.core.error_feedback.parsers.drf import DRFParser
from schemathesis.core.error_feedback.parsers.go_validator import GoValidatorParser
from schemathesis.core.error_feedback.parsers.jackson import JacksonParser
from schemathesis.core.error_feedback.parsers.laravel import LaravelParser
from schemathesis.core.error_feedback.parsers.marshmallow import MarshmallowParser
from schemathesis.core.error_feedback.parsers.pydantic import PydanticParser
from schemathesis.core.error_feedback.parsers.symfony import SymfonyParser
from schemathesis.core.error_feedback.parsers.zod import ZodParser
from schemathesis.core.parameters import ParameterLocation
from test.core.error_feedback.parsers.helpers import parse_observations

# Wire-form envelopes captured against running plain-Flask, webargs, flask-smorest,
# and APIFlask apps. Inner shape is identical across stacks; wrappers differ.
_PLAIN_REQUIRED = {"username": ["Missing data for required field."]}


_PLAIN_NULL_FIELD = {"username": ["Field may not be null."]}


_PLAIN_EMAIL = {"email": ["Not a valid email address."]}


_PLAIN_URL = {"site": ["Not a valid URL."]}


_PLAIN_UUID = {"token": ["Not a valid UUID."]}


_PLAIN_DATE = {"when": ["Not a valid date."]}


_PLAIN_DATETIME = {"started": ["Not a valid datetime."]}


_PLAIN_INTEGER = {"age": ["Not a valid integer."]}


_PLAIN_NUMBER = {"weight": ["Not a valid number."]}


_PLAIN_BOOLEAN = {"active": ["Not a valid boolean."]}


_PLAIN_LIST_TYPE = {"tags": ["Not a valid list."]}


_PLAIN_MAPPING_TYPE = {"meta": ["Not a valid mapping type."]}


_PLAIN_LENGTH_RANGE = {"username": ["Length must be between 3 and 30."]}


_PLAIN_LENGTH_MIN = {"username": ["Shorter than minimum length 3."]}


_PLAIN_LENGTH_MAX = {"username": ["Longer than maximum length 30."]}


_PLAIN_LENGTH_EQUAL = {"code": ["Length must be 5."]}


_PLAIN_RANGE_GE = {"age": ["Must be greater than or equal to 0."]}


_PLAIN_RANGE_LE = {"age": ["Must be less than or equal to 130."]}


_PLAIN_RANGE_GT = {"age": ["Must be greater than 0."]}


_PLAIN_RANGE_LT = {"age": ["Must be less than 130."]}


_PLAIN_RANGE_BETWEEN = {"age": ["Must be greater than or equal to 0 and less than or equal to 130."]}


_PLAIN_ONEOF = {"role": ["Must be one of: admin, user, guest."]}


_PLAIN_REGEX = {"code": ["String does not match expected pattern."]}


_PLAIN_NESTED = {"address": {"street": ["Missing data for required field."]}}


_PLAIN_LIST_INDEX = {"tags": {"0": ["Shorter than minimum length 3."]}}


_PLAIN_MULTI_FIELD = {
    "username": ["Length must be between 3 and 30."],
    "email": ["Not a valid email address."],
    "age": ["Must be greater than or equal to 0 and less than or equal to 130."],
}


def _wrap_webargs(payload: dict, location: str = "json") -> dict:
    return {"errors": {location: payload}}


def _wrap_smorest(payload: dict, location: str = "json") -> dict:
    return {"code": 422, "errors": {location: payload}, "status": "Unprocessable Entity"}


def _wrap_apiflask(payload: dict, location: str = "json") -> dict:
    return {"detail": {location: payload}, "message": "Validation error"}


_MARSHMALLOW_ACCEPTED_BODIES = [
    pytest.param(_PLAIN_REQUIRED, id="plain-required"),
    pytest.param(_PLAIN_EMAIL, id="plain-email"),
    pytest.param(_PLAIN_LENGTH_RANGE, id="plain-length-range"),
    pytest.param(_PLAIN_NESTED, id="plain-nested"),
    pytest.param(_wrap_webargs(_PLAIN_REQUIRED), id="webargs-required"),
    pytest.param(_wrap_webargs(_PLAIN_LENGTH_RANGE), id="webargs-length"),
    pytest.param(_wrap_webargs(_PLAIN_REQUIRED, location="query"), id="webargs-query-location"),
    pytest.param(_wrap_smorest(_PLAIN_REQUIRED), id="smorest-required"),
    pytest.param(_wrap_smorest(_PLAIN_RANGE_BETWEEN), id="smorest-range"),
    pytest.param(_wrap_apiflask(_PLAIN_REQUIRED), id="apiflask-required"),
    pytest.param(_wrap_apiflask(_PLAIN_ONEOF), id="apiflask-oneof"),
    pytest.param(_PLAIN_MULTI_FIELD, id="plain-multi"),
]


@pytest.mark.parametrize("body", _MARSHMALLOW_ACCEPTED_BODIES)
def test_marshmallow_parser_can_parse_recognises_envelope(body):
    assert MarshmallowParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        {"username": ["This field is required."]},
        {"username": ["can't be blank"]},
        {"errors": [{"field": "x", "message": "y"}]},
        {"detail": "Not Found"},
        {"errors": {"json": {"username": ["This field is required."]}}},
        {"username": [123]},
        {"errors": {"json": {}}},
        {"username": "Not a valid email address."},
        {"propertyPath": "email", "code": "abc"},
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "drf-message",
        "rails-message",
        "ajv-shape",
        "string-detail",
        "wrapped-drf-message",
        "non-string-list",
        "empty-issues-map",
        "string-not-list",
        "symfony-shape",
    ],
)
def test_marshmallow_parser_can_parse_rejects_non_marshmallow_bodies(body):
    assert MarshmallowParser().can_parse(body=body) is False


def _marshmallow_signatures(observations: tuple[Observation, ...]) -> list[tuple]:
    return [(o.parameter_path, o.kind, o.payload, o.location) for o in observations]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            _PLAIN_REQUIRED,
            ((("username",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="required",
        ),
        pytest.param(
            _PLAIN_NULL_FIELD,
            ((("username",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="null-field",
        ),
        pytest.param(
            _PLAIN_EMAIL,
            ((("email",), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="email",
        ),
        pytest.param(
            _PLAIN_URL,
            ((("site",), ObservationKind.FORMAT, FormatPayload(name="uri")),),
            id="url",
        ),
        pytest.param(
            _PLAIN_UUID,
            ((("token",), ObservationKind.FORMAT, FormatPayload(name="uuid")),),
            id="uuid",
        ),
        pytest.param(
            _PLAIN_DATE,
            ((("when",), ObservationKind.FORMAT, FormatPayload(name="date")),),
            id="date",
        ),
        pytest.param(
            _PLAIN_DATETIME,
            ((("started",), ObservationKind.FORMAT, FormatPayload(name="date-time")),),
            id="datetime",
        ),
        pytest.param(
            _PLAIN_INTEGER,
            ((("age",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer")),),
            id="integer",
        ),
        pytest.param(
            _PLAIN_NUMBER,
            ((("weight",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="number")),),
            id="number",
        ),
        pytest.param(
            _PLAIN_BOOLEAN,
            ((("active",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="boolean")),),
            id="boolean",
        ),
        pytest.param(
            _PLAIN_LIST_TYPE,
            ((("tags",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="array")),),
            id="list-type",
        ),
        pytest.param(
            _PLAIN_MAPPING_TYPE,
            ((("meta",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="object")),),
            id="mapping-type",
        ),
        pytest.param(
            _PLAIN_LENGTH_RANGE,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=30)),),
            id="length-range",
        ),
        pytest.param(
            _PLAIN_LENGTH_MIN,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),),
            id="length-min",
        ),
        pytest.param(
            _PLAIN_LENGTH_MAX,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=30)),),
            id="length-max",
        ),
        pytest.param(
            _PLAIN_LENGTH_EQUAL,
            ((("code",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=5, max=5)),),
            id="length-equal",
        ),
        pytest.param(
            _PLAIN_RANGE_GE,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="range-ge",
        ),
        pytest.param(
            _PLAIN_RANGE_LE,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=130.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="range-le",
        ),
        pytest.param(
            _PLAIN_RANGE_GT,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
                ),
            ),
            id="range-gt",
        ),
        pytest.param(
            _PLAIN_RANGE_LT,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=130.0, direction=BoundDirection.MAX, exclusive=True),
                ),
            ),
            id="range-lt",
        ),
        pytest.param(
            _PLAIN_ONEOF,
            ((("role",), ObservationKind.ENUM, EnumPayload(values=("admin", "user", "guest"))),),
            id="oneof",
        ),
        pytest.param(
            _PLAIN_NESTED,
            ((("address", "street"), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="nested",
        ),
        pytest.param(
            _PLAIN_LIST_INDEX,
            ((("tags", 0), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),),
            id="list-index",
        ),
    ],
)
def test_marshmallow_parser_parse(make_operation, case_factory, body, expected):
    assert (
        tuple(
            (o.parameter_path, o.kind, o.payload)
            for o in parse_observations(MarshmallowParser(), body, make_operation, case_factory)
        )
        == expected
    )


def test_marshmallow_parser_range_inline_emits_both_bounds(make_operation, case_factory):
    actual = parse_observations(MarshmallowParser(), _PLAIN_RANGE_BETWEEN, make_operation, case_factory)
    assert sorted((o.payload.bound, o.payload.direction) for o in actual) == [
        (0.0, BoundDirection.MIN),
        (130.0, BoundDirection.MAX),
    ]
    assert all(o.parameter_path == ("age",) and o.location == ParameterLocation.BODY for o in actual)


def test_marshmallow_parser_multi_field(make_operation, case_factory):
    assert sorted(
        o.parameter_path
        for o in parse_observations(MarshmallowParser(), _PLAIN_MULTI_FIELD, make_operation, case_factory)
    ) == [("age",), ("age",), ("email",), ("username",)]


def test_marshmallow_parser_webargs_envelope(make_operation, case_factory):
    assert _marshmallow_signatures(
        parse_observations(
            MarshmallowParser(),
            _wrap_webargs({"username": ["Missing data for required field."]}),
            make_operation,
            case_factory,
        )
    ) == [(("username",), ObservationKind.MUST_NOT_BE_BLANK, None, ParameterLocation.BODY)]


def test_marshmallow_parser_smorest_envelope(make_operation, case_factory):
    assert _marshmallow_signatures(
        parse_observations(
            MarshmallowParser(),
            _wrap_smorest({"username": ["Length must be between 3 and 30."]}),
            make_operation,
            case_factory,
        )
    ) == [(("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=30), ParameterLocation.BODY)]


def test_marshmallow_parser_apiflask_envelope(make_operation, case_factory):
    assert _marshmallow_signatures(
        parse_observations(
            MarshmallowParser(),
            _wrap_apiflask({"role": ["Must be one of: admin, user, guest."]}),
            make_operation,
            case_factory,
        )
    ) == [
        (
            ("role",),
            ObservationKind.ENUM,
            EnumPayload(values=("admin", "user", "guest")),
            ParameterLocation.BODY,
        )
    ]


def test_marshmallow_parser_query_location_routes_to_query(make_operation, case_factory):
    assert _marshmallow_signatures(
        parse_observations(
            MarshmallowParser(),
            _wrap_webargs({"page": ["Not a valid integer."]}, location="query"),
            make_operation,
            case_factory,
        )
    ) == [(("page",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer"), ParameterLocation.QUERY)]


def test_marshmallow_parser_get_routes_plain_body_to_query(make_operation, case_factory):
    obs = parse_observations(MarshmallowParser(), _PLAIN_EMAIL, make_operation, case_factory, method="get")
    assert obs and obs[0].location == ParameterLocation.QUERY


def test_marshmallow_parser_drops_unknown_message(make_operation, case_factory):
    assert (
        parse_observations(
            MarshmallowParser(),
            {"username": ["something custom and unrecognised"]},
            make_operation,
            case_factory,
        )
        == ()
    )


def test_marshmallow_parser_drops_regex_pattern_with_no_template(make_operation, case_factory):
    assert parse_observations(MarshmallowParser(), _PLAIN_REGEX, make_operation, case_factory) == ()


def test_marshmallow_outranks_drf_when_both_claim_a_body():
    body = {"username": ["Missing data for required field."]}
    assert MarshmallowParser().can_parse(body=body) is True
    assert DRFParser().can_parse(body=body) is True
    assert MarshmallowParser.priority > DRFParser.priority


@pytest.mark.parametrize(
    "parser",
    [
        AjvParser(),
        AspNetParser(),
        ConfluentParser(),
        GoValidatorParser(),
        LaravelParser(),
        PydanticParser(),
        JacksonParser(),
        SymfonyParser(),
        ZodParser(),
    ],
    ids=lambda p: type(p).__name__,
)
@pytest.mark.parametrize("body", _MARSHMALLOW_ACCEPTED_BODIES)
def test_other_parsers_reject_marshmallow_bodies(parser, body):
    assert parser.can_parse(body=body) is False
