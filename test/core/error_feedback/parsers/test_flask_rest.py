from __future__ import annotations

import pytest

from schemathesis.core.error_feedback import (
    BoundDirection,
    EnumPayload,
    FormatPayload,
    NumericBoundPayload,
    ObservationKind,
    PatternPayload,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.error_feedback.parsers.ajv import AjvParser
from schemathesis.core.error_feedback.parsers.aspnet import AspNetParser
from schemathesis.core.error_feedback.parsers.confluent import ConfluentParser
from schemathesis.core.error_feedback.parsers.flask_rest import FlaskRestParser
from schemathesis.core.error_feedback.parsers.go_validator import GoValidatorParser
from schemathesis.core.error_feedback.parsers.jackson import JacksonParser
from schemathesis.core.error_feedback.parsers.laravel import LaravelParser
from schemathesis.core.error_feedback.parsers.marshmallow import MarshmallowParser
from schemathesis.core.error_feedback.parsers.pydantic import PydanticParser
from schemathesis.core.error_feedback.parsers.symfony import SymfonyParser
from schemathesis.core.error_feedback.parsers.zod import ZodParser
from schemathesis.core.parameters import ParameterLocation
from test.core.error_feedback.parsers.helpers import parse_observations

# Flask-RESTful (`reqparse`): top-level `message` is the issues map.
# Flask-RESTX (`reqparse` and `@api.expect(model, validate=True)`): `errors` is the issues map
# and top-level `message` is the constant string `"Input payload validation failed"`.
# Flask-RESTX model mode embeds Python `jsonschema` messages; jsonschema_rs ports use
# double-quoted values and length/items/properties messages that carry the threshold.
_RESTX_MESSAGE = "Input payload validation failed"


def _restful(issues: dict[str, str]) -> dict:
    return {"message": issues}


def _restx(issues: dict[str, str]) -> dict:
    return {"errors": issues, "message": _RESTX_MESSAGE}


_REQPARSE_MISSING = "Missing required parameter in the JSON body"


_REQPARSE_PATTERN = 'Value does not match pattern: "^[A-Z]{3}$"'


_REQPARSE_INT = "invalid literal for int() with base 10: 'twenty'"


_REQPARSE_FLOAT = "could not convert string to float: 'twenty'"


_FLASK_REST_ACCEPTED_BODIES = [
    pytest.param(_restful({"username": _REQPARSE_MISSING}), id="restful-required"),
    pytest.param(_restful({"code": _REQPARSE_PATTERN}), id="restful-pattern"),
    pytest.param(_restful({"age": _REQPARSE_INT}), id="restful-int-coercion"),
    pytest.param(_restx({"username": _REQPARSE_MISSING}), id="restx-reqparse-required"),
    pytest.param(_restx({"username": "'username' is a required property"}), id="restx-jsonschema-required"),
    pytest.param(_restx({"username": '"username" is a required property'}), id="restx-jsonschema-rs-required"),
    pytest.param(_restx({"age": "200 is greater than the maximum of 130"}), id="restx-jsonschema-max"),
    pytest.param(_restx({"username": "'a' is too short"}), id="restx-jsonschema-too-short"),
    pytest.param(_restx({"username": '"a" is shorter than 3 characters'}), id="restx-jsonschema-rs-too-short"),
    pytest.param(_restx({"role": "'superuser' is not one of ['admin', 'user', 'guest']"}), id="restx-jsonschema-enum"),
    pytest.param(_restx({"email": "'not-email' is not a 'email'"}), id="restx-jsonschema-format"),
    pytest.param(
        _restx({"email": "'not-email' does not match '^[^@]+@[^@]+$'"}),
        id="restx-jsonschema-pattern",
    ),
]


@pytest.mark.parametrize("body", _FLASK_REST_ACCEPTED_BODIES)
def test_flask_rest_parser_can_parse_recognises_envelope(body):
    assert FlaskRestParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        {"message": "just a string"},
        {"errors": {"username": ["a list, not a string"]}},
        {"username": ["This field is required."]},
        {"errors": [{"keyword": "format"}]},
        {"detail": "Not Found"},
        {"errors": {"username": "unrecognised gibberish"}},
        {"errors": {}, "message": _RESTX_MESSAGE},
        {"message": {}},
        {"errors": {123: "non-str key"}, "message": _RESTX_MESSAGE},
        [{"propertyPath": "email"}],
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "spring-style",
        "marshmallow-shape",
        "drf-shape",
        "ajv-shape",
        "string-detail",
        "unrecognised-message",
        "empty-issues-map",
        "empty-message-dict",
        "non-str-key",
        "symfony-shape",
    ],
)
def test_flask_rest_parser_can_parse_rejects_non_envelope_bodies(body):
    assert FlaskRestParser().can_parse(body=body) is False


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            _restful({"username": _REQPARSE_MISSING}),
            ((("username",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="restful-required",
        ),
        pytest.param(
            _restful({"code": _REQPARSE_PATTERN}),
            ((("code",), ObservationKind.PATTERN, PatternPayload(regex="^[A-Z]{3}$")),),
            id="restful-pattern",
        ),
        pytest.param(
            _restful({"age": _REQPARSE_INT}),
            ((("age",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer")),),
            id="restful-int-coercion",
        ),
        pytest.param(
            _restful({"weight": _REQPARSE_FLOAT}),
            ((("weight",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="number")),),
            id="restful-float-coercion",
        ),
        pytest.param(
            _restx({"username": "'username' is a required property"}),
            ((("username",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="restx-required-py",
        ),
        pytest.param(
            _restx({"username": '"username" is a required property'}),
            ((("username",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="restx-required-rs",
        ),
        pytest.param(
            _restx({"age": "42 is not of type 'string'"}),
            ((("age",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="string")),),
            id="restx-type-py",
        ),
        pytest.param(
            _restx({"age": '42 is not of type "string"'}),
            ((("age",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="string")),),
            id="restx-type-rs",
        ),
        pytest.param(
            _restx({"age": "200 is greater than the maximum of 130"}),
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=130.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="restx-max-inclusive",
        ),
        pytest.param(
            _restx({"age": "-5 is less than the minimum of 0"}),
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="restx-min-inclusive",
        ),
        pytest.param(
            _restx({"age": "0 is less than or equal to the minimum of 0"}),
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
                ),
            ),
            id="restx-min-exclusive",
        ),
        pytest.param(
            _restx({"age": "100 is greater than or equal to the maximum of 100"}),
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=True),
                ),
            ),
            id="restx-max-exclusive",
        ),
        pytest.param(
            _restx({"username": '"a" is shorter than 3 characters'}),
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),),
            id="restx-min-length-rs",
        ),
        pytest.param(
            _restx({"username": '"abcdefghij" is longer than 5 characters'}),
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=5)),),
            id="restx-max-length-rs",
        ),
        pytest.param(
            _restx({"tags": "[] has less than 1 item"}),
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),),
            id="restx-min-items-rs",
        ),
        pytest.param(
            _restx({"tags": "[1,2,3] has more than 2 items"}),
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=2)),),
            id="restx-max-items-rs",
        ),
        pytest.param(
            _restx({"meta": "{} has less than 1 property"}),
            ((("meta",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),),
            id="restx-min-properties-rs",
        ),
        pytest.param(
            _restx({"meta": '{"a":1,"b":2} has more than 1 property'}),
            ((("meta",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=1)),),
            id="restx-max-properties-rs",
        ),
        pytest.param(
            _restx({"role": "'superuser' is not one of ['admin', 'user', 'guest']"}),
            ((("role",), ObservationKind.ENUM, EnumPayload(values=("admin", "user", "guest"))),),
            id="restx-enum-py",
        ),
        pytest.param(
            _restx({"role": '"superuser" is not one of "admin", "user" or "guest"'}),
            ((("role",), ObservationKind.ENUM, EnumPayload(values=("admin", "user", "guest"))),),
            id="restx-enum-rs",
        ),
        pytest.param(
            _restx({"email": "'not-email' is not a 'email'"}),
            ((("email",), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="restx-format-email-py",
        ),
        pytest.param(
            _restx({"site": '"!nope" is not a "uri"'}),
            ((("site",), ObservationKind.FORMAT, FormatPayload(name="uri")),),
            id="restx-format-uri-rs",
        ),
        pytest.param(
            _restx({"code": "'abc' does not match '^[A-Z]{3}$'"}),
            ((("code",), ObservationKind.PATTERN, PatternPayload(regex="^[A-Z]{3}$")),),
            id="restx-pattern-py",
        ),
        pytest.param(
            _restx({"code": '"abc" does not match "^[A-Z]{3}$"'}),
            ((("code",), ObservationKind.PATTERN, PatternPayload(regex="^[A-Z]{3}$")),),
            id="restx-pattern-rs",
        ),
    ],
)
def test_flask_rest_parser_parse(make_operation, case_factory, body, expected):
    assert (
        tuple(
            (o.parameter_path, o.kind, o.payload)
            for o in parse_observations(FlaskRestParser(), body, make_operation, case_factory)
        )
        == expected
    )


def test_flask_rest_parser_parse_returns_empty_for_non_envelope(make_operation, case_factory):
    assert parse_observations(FlaskRestParser(), {"detail": "nope"}, make_operation, case_factory) == ()


@pytest.mark.parametrize(
    "body",
    [
        # Python jsonschema's bare `is too short`/`is too long` lacks the threshold, so we
        # can't pin a SIZE_BOUND. Drop silently rather than emit a wrong constraint.
        _restx({"username": "'a' is too short"}),
        _restx({"username": "'abc' is too long"}),
        _restful({"role": "superuser is not a valid choice"}),
        _restx({"value": "'hello' was expected"}),
        # Numeric enum in jsonschema renders bare `1, 2, 3` (no surrounding quotes),
        # which `EnumPayload` (string-only) can't represent.
        _restx({"rank": "5 is not one of [1, 2, 3]"}),
        # jsonschema_rs collapses 4+ choices to "X, Y or N other candidates" — drop.
        _restx({"flag": '"x" is not one of "a", "b" or 3 other candidates'}),
        # Either implementation emitting a malformed enum payload — drop.
        _restx({"role": "'x' is not one of [admin, user]"}),
        _restx({"role": '"x" is not one of "admin", bb or "user"'}),
    ],
    ids=[
        "py-too-short",
        "py-too-long",
        "restful-invalid-choice",
        "restx-const",
        "py-numeric-enum",
        "rs-enum-truncated",
        "py-enum-unquoted",
        "rs-enum-unquoted",
    ],
)
def test_flask_rest_parser_drops_messages_without_actionable_threshold(make_operation, case_factory, body):
    assert parse_observations(FlaskRestParser(), body, make_operation, case_factory) == ()


def test_flask_rest_parser_rs_enum_with_single_value(make_operation, case_factory):
    body = _restx({"role": '"superuser" is not one of "admin"'})
    assert tuple(
        (o.parameter_path, o.kind, o.payload)
        for o in parse_observations(FlaskRestParser(), body, make_operation, case_factory)
    ) == ((("role",), ObservationKind.ENUM, EnumPayload(values=("admin",))),)


def test_flask_rest_parser_get_routes_to_query_location(make_operation, case_factory):
    obs = parse_observations(
        FlaskRestParser(),
        _restx({"page": "'a' is not of type 'integer'"}),
        make_operation,
        case_factory,
        method="get",
    )
    assert obs and obs[0].location == ParameterLocation.QUERY


def test_flask_rest_parser_multi_field(make_operation, case_factory):
    body = _restx(
        {
            "username": "'username' is a required property",
            "age": "200 is greater than the maximum of 130",
            "role": "'superuser' is not one of ['admin', 'user', 'guest']",
        }
    )
    paths = sorted(o.parameter_path for o in parse_observations(FlaskRestParser(), body, make_operation, case_factory))
    assert paths == [("age",), ("role",), ("username",)]


@pytest.mark.parametrize(
    "parser",
    [
        AjvParser(),
        AspNetParser(),
        ConfluentParser(),
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
@pytest.mark.parametrize("body", _FLASK_REST_ACCEPTED_BODIES)
def test_other_parsers_reject_flask_rest_bodies(parser, body):
    assert parser.can_parse(body=body) is False
