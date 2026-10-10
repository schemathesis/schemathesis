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
from schemathesis.core.error_feedback.parsers.aspnet import AspNetParser
from schemathesis.core.error_feedback.parsers.drf import DRFParser
from schemathesis.core.error_feedback.parsers.jackson import JacksonParser
from schemathesis.core.error_feedback.parsers.laravel import LaravelParser
from schemathesis.core.error_feedback.parsers.pydantic import PydanticParser
from schemathesis.core.error_feedback.parsers.rails import (
    RailsParser,
)
from schemathesis.core.error_feedback.parsers.spring import SpringParser
from schemathesis.core.error_feedback.parsers.zod import ZodParser
from schemathesis.core.parameters import ParameterLocation
from test.core.error_feedback.parsers.helpers import parse_observations


def _zod_envelope(*issues: dict) -> dict:
    return {"errors": list(issues)}


_ZOD_INVALID_STRING_EMAIL = _zod_envelope(
    {"validation": "email", "code": "invalid_string", "message": "Invalid email", "path": ["email"]}
)


_ZOD_INVALID_STRING_URL = _zod_envelope(
    {"validation": "url", "code": "invalid_string", "message": "Invalid url", "path": ["url"]}
)


_ZOD_INVALID_STRING_UUID = _zod_envelope(
    {"validation": "uuid", "code": "invalid_string", "message": "Invalid uuid", "path": ["token"]}
)


_ZOD_INVALID_STRING_DATETIME = _zod_envelope(
    {"validation": "datetime", "code": "invalid_string", "message": "Invalid datetime", "path": ["when"]}
)


_ZOD_INVALID_STRING_CUID = _zod_envelope(
    {"validation": "cuid", "code": "invalid_string", "message": "Invalid cuid", "path": ["id"]}
)


_ZOD_INVALID_STRING_REGEX = _zod_envelope(
    {"validation": "regex", "code": "invalid_string", "message": "Invalid", "path": ["code"]}
)


_ZOD_TOO_SMALL_STRING = _zod_envelope(
    {
        "code": "too_small",
        "minimum": 3,
        "type": "string",
        "inclusive": True,
        "exact": False,
        "message": "String must contain at least 3 character(s)",
        "path": ["name"],
    }
)


_ZOD_TOO_BIG_STRING = _zod_envelope(
    {
        "code": "too_big",
        "maximum": 5,
        "type": "string",
        "inclusive": True,
        "exact": False,
        "message": "String must contain at most 5 character(s)",
        "path": ["name"],
    }
)


_ZOD_TOO_SMALL_NUMBER_INCLUSIVE = _zod_envelope(
    {
        "code": "too_small",
        "minimum": 0,
        "type": "number",
        "inclusive": True,
        "exact": False,
        "message": "Number must be greater than or equal to 0",
        "path": ["age"],
    }
)


_ZOD_TOO_BIG_NUMBER_INCLUSIVE = _zod_envelope(
    {
        "code": "too_big",
        "maximum": 130,
        "type": "number",
        "inclusive": True,
        "exact": False,
        "message": "Number must be less than or equal to 130",
        "path": ["age"],
    }
)


_ZOD_TOO_SMALL_NUMBER_EXCLUSIVE = _zod_envelope(
    {
        "code": "too_small",
        "minimum": 0,
        "type": "number",
        "inclusive": False,
        "exact": False,
        "message": "Number must be greater than 0",
        "path": ["score"],
    }
)


_ZOD_TOO_BIG_NUMBER_EXCLUSIVE = _zod_envelope(
    {
        "code": "too_big",
        "maximum": 100,
        "type": "number",
        "inclusive": False,
        "exact": False,
        "message": "Number must be less than 100",
        "path": ["score"],
    }
)


_ZOD_TOO_SMALL_ARRAY = _zod_envelope(
    {
        "code": "too_small",
        "minimum": 1,
        "type": "array",
        "inclusive": True,
        "exact": False,
        "message": "Array must contain at least 1 element(s)",
        "path": ["tags"],
    }
)


_ZOD_TOO_BIG_ARRAY = _zod_envelope(
    {
        "code": "too_big",
        "maximum": 2,
        "type": "array",
        "inclusive": True,
        "exact": False,
        "message": "Array must contain at most 2 element(s)",
        "path": ["tags"],
    }
)


_ZOD_INVALID_TYPE_STRING = _zod_envelope(
    {
        "code": "invalid_type",
        "expected": "string",
        "received": "number",
        "path": ["name"],
        "message": "Expected string, received number",
    }
)


_ZOD_INVALID_TYPE_NUMBER = _zod_envelope(
    {
        "code": "invalid_type",
        "expected": "number",
        "received": "string",
        "path": ["age"],
        "message": "Expected number, received string",
    }
)


_ZOD_INVALID_TYPE_BOOLEAN = _zod_envelope(
    {
        "code": "invalid_type",
        "expected": "boolean",
        "received": "string",
        "path": ["flag"],
        "message": "Expected boolean, received string",
    }
)


_ZOD_INVALID_TYPE_REQUIRED = _zod_envelope(
    {
        "code": "invalid_type",
        "expected": "string",
        "received": "undefined",
        "path": ["name"],
        "message": "Required",
    }
)


_ZOD_ROOT_BODY_REQUIRED = _zod_envelope(
    {
        "code": "invalid_type",
        "expected": "object",
        "received": "undefined",
        "path": [],
        "message": "Required",
    }
)


_ZOD_INVALID_ENUM_VALUE = _zod_envelope(
    {
        "received": "superuser",
        "code": "invalid_enum_value",
        "options": ["admin", "user", "guest"],
        "path": ["role"],
        "message": "Invalid enum value. Expected 'admin' | 'user' | 'guest', received 'superuser'",
    }
)


_ZOD_INVALID_DATE = _zod_envelope({"code": "invalid_date", "path": ["when"], "message": "Invalid date"})


_ZOD_NESTED_PATH = _zod_envelope(
    {"validation": "email", "code": "invalid_string", "message": "Invalid email", "path": ["user", "email"]}
)


_ZOD_LIST_ELEMENT_PATH = _zod_envelope(
    {
        "code": "too_small",
        "minimum": 2,
        "type": "string",
        "inclusive": True,
        "exact": False,
        "message": "String must contain at least 2 character(s)",
        "path": ["tags", 0],
    }
)


_ZOD_CUSTOM_REFINEMENT = _zod_envelope({"code": "custom", "message": "too short", "path": ["password"]})


_ZOD_MULTI_FIELD = _zod_envelope(
    {"validation": "email", "code": "invalid_string", "message": "Invalid email", "path": ["email"]},
    {
        "code": "too_small",
        "minimum": 0,
        "type": "number",
        "inclusive": True,
        "exact": False,
        "message": "Number must be greater than or equal to 0",
        "path": ["age"],
    },
    {
        "code": "too_small",
        "minimum": 2,
        "type": "string",
        "inclusive": True,
        "exact": False,
        "message": "String must contain at least 2 character(s)",
        "path": ["name"],
    },
)


_ZOD_ALT_KEY = {"issues": _ZOD_INVALID_STRING_EMAIL["errors"]}


_ZOD_HONO_VALIDATOR = {
    "success": False,
    "error": {"issues": _ZOD_INVALID_STRING_EMAIL["errors"], "name": "ZodError"},
}


_ZOD_EXPRESS_MIDDLEWARE = [
    {"type": "Body", "errors": {"issues": _ZOD_INVALID_STRING_EMAIL["errors"], "name": "ZodError"}}
]


_ZOD_ACCEPTED_BODIES = [
    pytest.param(_ZOD_INVALID_STRING_EMAIL, id="invalid-string-email"),
    pytest.param(_ZOD_INVALID_STRING_URL, id="invalid-string-url"),
    pytest.param(_ZOD_INVALID_STRING_UUID, id="invalid-string-uuid"),
    pytest.param(_ZOD_INVALID_STRING_DATETIME, id="invalid-string-datetime"),
    pytest.param(_ZOD_INVALID_STRING_CUID, id="invalid-string-cuid"),
    pytest.param(_ZOD_INVALID_STRING_REGEX, id="invalid-string-regex"),
    pytest.param(_ZOD_TOO_SMALL_STRING, id="too-small-string"),
    pytest.param(_ZOD_TOO_BIG_STRING, id="too-big-string"),
    pytest.param(_ZOD_TOO_SMALL_NUMBER_INCLUSIVE, id="too-small-number-inclusive"),
    pytest.param(_ZOD_TOO_BIG_NUMBER_INCLUSIVE, id="too-big-number-inclusive"),
    pytest.param(_ZOD_TOO_SMALL_NUMBER_EXCLUSIVE, id="too-small-number-exclusive"),
    pytest.param(_ZOD_TOO_BIG_NUMBER_EXCLUSIVE, id="too-big-number-exclusive"),
    pytest.param(_ZOD_TOO_SMALL_ARRAY, id="too-small-array"),
    pytest.param(_ZOD_TOO_BIG_ARRAY, id="too-big-array"),
    pytest.param(_ZOD_INVALID_TYPE_STRING, id="invalid-type-string"),
    pytest.param(_ZOD_INVALID_TYPE_NUMBER, id="invalid-type-number"),
    pytest.param(_ZOD_INVALID_TYPE_BOOLEAN, id="invalid-type-boolean"),
    pytest.param(_ZOD_INVALID_TYPE_REQUIRED, id="invalid-type-required"),
    pytest.param(_ZOD_ROOT_BODY_REQUIRED, id="root-body-required"),
    pytest.param(_ZOD_INVALID_ENUM_VALUE, id="invalid-enum-value"),
    pytest.param(_ZOD_INVALID_DATE, id="invalid-date"),
    pytest.param(_ZOD_NESTED_PATH, id="nested-path"),
    pytest.param(_ZOD_LIST_ELEMENT_PATH, id="list-element-path"),
    pytest.param(_ZOD_CUSTOM_REFINEMENT, id="custom-refinement"),
    pytest.param(_ZOD_MULTI_FIELD, id="multi-field"),
    pytest.param(_ZOD_ALT_KEY, id="alt-issues-key"),
    pytest.param(_ZOD_HONO_VALIDATOR, id="hono-zod-validator"),
    pytest.param(_ZOD_EXPRESS_MIDDLEWARE, id="zod-express-middleware"),
]


@pytest.mark.parametrize("body", _ZOD_ACCEPTED_BODIES)
def test_zod_parser_can_parse_recognises_envelope(body):
    assert ZodParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        {"errors": []},
        {"errors": "not a list"},
        {"errors": [{"field": "x", "defaultMessage": "must not be null"}]},
        {"errors": [{"path": ["email"]}]},
        {"errors": [{"code": "invalid_string"}]},
        {"name": ["This field is required."]},
        {"detail": [{"loc": ["body", "email"], "msg": "field required"}]},
        {"message": "The given data was invalid.", "errors": {"email": ["The email field is required."]}},
        {
            "type": "https://tools.ietf.org/html/rfc9110#section-15.5.1",
            "title": "validation",
            "status": 400,
            "errors": {"Email": ["The Email field is required."]},
        },
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "errors-empty-list",
        "errors-not-a-list",
        "spring-field-default-message",
        "issue-without-code",
        "issue-without-path",
        "drf",
        "pydantic",
        "laravel",
        "aspnet",
    ],
)
def test_zod_parser_can_parse_rejects_non_zod_bodies(body):
    assert ZodParser().can_parse(body=body) is False


def _zod_signatures(observations: tuple[Observation, ...]) -> list[tuple]:
    return sorted((o.parameter_path, o.kind, o.payload) for o in observations)


@pytest.mark.parametrize(
    ("method", "path", "body", "expected"),
    [
        pytest.param(
            "post",
            "/api/users",
            _ZOD_INVALID_STRING_EMAIL,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("email",),
                    ObservationKind.FORMAT,
                    FormatPayload(name="email"),
                ),
            ),
            id="invalid-string-email",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_INVALID_STRING_URL,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("url",),
                    ObservationKind.FORMAT,
                    FormatPayload(name="uri"),
                ),
            ),
            id="invalid-string-url",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_INVALID_STRING_UUID,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("token",),
                    ObservationKind.FORMAT,
                    FormatPayload(name="uuid"),
                ),
            ),
            id="invalid-string-uuid",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_INVALID_STRING_DATETIME,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("when",),
                    ObservationKind.FORMAT,
                    FormatPayload(name="date-time"),
                ),
            ),
            id="invalid-string-datetime",
        ),
        pytest.param("post", "/api/users", _ZOD_INVALID_STRING_CUID, (), id="invalid-string-cuid-dropped"),
        pytest.param("post", "/api/users", _ZOD_INVALID_STRING_REGEX, (), id="invalid-string-regex-dropped"),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_TOO_SMALL_STRING,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("name",),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=3, max=None),
                ),
            ),
            id="too-small-string",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_TOO_BIG_STRING,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("name",),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=None, max=5),
                ),
            ),
            id="too-big-string",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_TOO_SMALL_NUMBER_INCLUSIVE,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="too-small-number-inclusive",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_TOO_BIG_NUMBER_INCLUSIVE,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=130.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="too-big-number-inclusive",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_TOO_SMALL_NUMBER_EXCLUSIVE,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
                ),
            ),
            id="too-small-number-exclusive",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_TOO_BIG_NUMBER_EXCLUSIVE,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=True),
                ),
            ),
            id="too-big-number-exclusive",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_TOO_SMALL_ARRAY,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("tags",),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=1, max=None),
                ),
            ),
            id="too-small-array",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_TOO_BIG_ARRAY,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("tags",),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=None, max=2),
                ),
            ),
            id="too-big-array",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_INVALID_TYPE_STRING,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("name",),
                    ObservationKind.TYPE_MISMATCH,
                    TypeMismatchPayload(type_name="string"),
                ),
            ),
            id="invalid-type-string",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_INVALID_TYPE_NUMBER,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("age",),
                    ObservationKind.TYPE_MISMATCH,
                    TypeMismatchPayload(type_name="number"),
                ),
            ),
            id="invalid-type-number",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_INVALID_TYPE_BOOLEAN,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("flag",),
                    ObservationKind.TYPE_MISMATCH,
                    TypeMismatchPayload(type_name="boolean"),
                ),
            ),
            id="invalid-type-boolean",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_INVALID_TYPE_REQUIRED,
            (("POST /api/users", ParameterLocation.BODY, ("name",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="invalid-type-required",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_ROOT_BODY_REQUIRED,
            (("POST /api/users", ParameterLocation.BODY, (), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="root-body-required",
        ),
        pytest.param(
            "get",
            "/api/users",
            _ZOD_ROOT_BODY_REQUIRED,
            (),
            id="root-body-required-get-dropped",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_INVALID_ENUM_VALUE,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("role",),
                    ObservationKind.ENUM,
                    EnumPayload(values=("admin", "user", "guest")),
                ),
            ),
            id="invalid-enum-value",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_INVALID_DATE,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("when",),
                    ObservationKind.FORMAT,
                    FormatPayload(name="date-time"),
                ),
            ),
            id="invalid-date",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_NESTED_PATH,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("user", "email"),
                    ObservationKind.FORMAT,
                    FormatPayload(name="email"),
                ),
            ),
            id="nested-path",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_LIST_ELEMENT_PATH,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("tags", 0),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=2, max=None),
                ),
            ),
            id="list-element-path",
        ),
        pytest.param("post", "/api/users", _ZOD_CUSTOM_REFINEMENT, (), id="custom-refinement-dropped"),
        pytest.param(
            "get",
            "/api/users",
            _ZOD_INVALID_STRING_EMAIL,
            (
                (
                    "GET /api/users",
                    ParameterLocation.QUERY,
                    ("email",),
                    ObservationKind.FORMAT,
                    FormatPayload(name="email"),
                ),
            ),
            id="get-routes-to-query-location",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_ALT_KEY,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("email",),
                    ObservationKind.FORMAT,
                    FormatPayload(name="email"),
                ),
            ),
            id="alt-issues-key",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_HONO_VALIDATOR,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("email",),
                    ObservationKind.FORMAT,
                    FormatPayload(name="email"),
                ),
            ),
            id="hono-zod-validator",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ZOD_EXPRESS_MIDDLEWARE,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("email",),
                    ObservationKind.FORMAT,
                    FormatPayload(name="email"),
                ),
            ),
            id="zod-express-middleware",
        ),
    ],
)
def test_zod_parser_parse(make_operation, method, path, body, expected, case_factory):
    operation = make_operation(method=method, path=path)
    actual = ZodParser().parse(operation=operation, body=body, case=case_factory())
    actual_signatures = tuple((o.operation_label, o.location, o.parameter_path, o.kind, o.payload) for o in actual)
    assert actual_signatures == expected


def test_zod_parser_parse_multi_field(make_operation, case_factory):
    observations = parse_observations(ZodParser(), _ZOD_MULTI_FIELD, make_operation, case_factory)
    assert _zod_signatures(observations) == sorted(
        [
            (("email",), ObservationKind.FORMAT, FormatPayload(name="email")),
            (
                ("age",),
                ObservationKind.NUMERIC_BOUND,
                NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
            ),
            (("name",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=2, max=None)),
        ]
    )


def test_zod_parser_skips_issue_with_non_list_path(make_operation, case_factory):
    body = _zod_envelope(
        {"validation": "email", "code": "invalid_string", "message": "Invalid email", "path": ["email"]},
        {"validation": "url", "code": "invalid_string", "message": "Invalid url", "path": "url"},
    )

    observations = parse_observations(ZodParser(), body, make_operation, case_factory)

    assert _zod_signatures(observations) == [(("email",), ObservationKind.FORMAT, FormatPayload(name="email"))]


def test_zod_parser_parse_returns_empty_for_non_envelope(make_operation, case_factory):
    assert parse_observations(ZodParser(), {"detail": "nope"}, make_operation, case_factory) == ()


@pytest.mark.parametrize(
    "issue",
    [
        {"code": "too_small", "type": "string", "minimum": "three", "path": ["name"]},
        {"code": "too_small", "type": "object", "minimum": 1, "path": ["meta"]},
        {"code": "too_small", "type": "string", "minimum": True, "path": ["name"]},
        {"code": "too_big", "type": "string", "maximum": "five", "path": ["name"]},
        {"code": "too_big", "type": "object", "maximum": 1, "path": ["meta"]},
        {"code": "invalid_type", "received": "string", "path": ["x"]},
        {"code": "invalid_enum_value", "options": [], "path": ["role"]},
        {"code": "invalid_enum_value", "options": [1, 2], "path": ["role"]},
        {"code": "invalid_string", "validation": "ip", "path": ["addr"]},
        {"code": "completely_unknown", "path": ["x"]},
        {"code": "invalid_type", "received": "string", "expected": True, "path": ["x"]},
        {"code": "invalid_string", "validation": "email", "path": []},
        {"code": "invalid_string", "validation": "email", "path": [True]},
        {"code": "invalid_string", "validation": "email", "path": [None]},
    ],
    ids=[
        "too-small-non-numeric-minimum",
        "too-small-unknown-type",
        "too-small-bool-minimum",
        "too-big-non-numeric-maximum",
        "too-big-unknown-type",
        "invalid-type-without-expected",
        "invalid-enum-empty-options",
        "invalid-enum-non-string-options",
        "invalid-string-unmapped-validation",
        "unknown-code",
        "invalid-type-non-string-expected",
        "empty-path",
        "bool-path-segment",
        "none-path-segment",
    ],
)
def test_zod_parser_parse_drops_malformed_issue(make_operation, issue, case_factory):
    body = {"errors": [issue, {"code": "invalid_string", "validation": "email", "path": ["seed"]}]}
    actual = parse_observations(ZodParser(), body, make_operation, case_factory)
    actual_paths = tuple(o.parameter_path for o in actual)
    assert actual_paths == (("seed",),)


@pytest.mark.parametrize(
    "parser",
    [AspNetParser(), LaravelParser(), RailsParser(), PydanticParser(), JacksonParser()],
    ids=lambda p: type(p).__name__,
)
@pytest.mark.parametrize("body", _ZOD_ACCEPTED_BODIES)
def test_other_parsers_reject_zod_bodies(parser, body):
    assert parser.can_parse(body=body) is False


def test_zod_outranks_spring_when_both_claim_a_body():
    body = _ZOD_INVALID_STRING_EMAIL
    assert ZodParser().can_parse(body=body) is True
    assert SpringParser().can_parse(body=body) is True
    assert ZodParser.priority > SpringParser.priority


def test_zod_outranks_drf_when_both_claim_a_body():
    body = _ZOD_INVALID_STRING_EMAIL
    assert ZodParser().can_parse(body=body) is True
    assert DRFParser().can_parse(body=body) is True
    assert ZodParser.priority > DRFParser.priority
