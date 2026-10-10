from __future__ import annotations

import pytest

from schemathesis.core.error_feedback import (
    BoundDirection,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.error_feedback.parsers.drf import DRFParser
from schemathesis.core.error_feedback.parsers.jackson import JacksonParser
from schemathesis.core.error_feedback.parsers.laravel import LaravelParser
from schemathesis.core.error_feedback.parsers.pydantic import PydanticParser
from schemathesis.core.error_feedback.parsers.rails import (
    RailsParser,
)
from schemathesis.core.error_feedback.parsers.spring import SpringParser
from schemathesis.core.parameters import ParameterLocation
from test.core.error_feedback.parsers.helpers import parse_observations


def _laravel_envelope(errors: dict) -> dict:
    return {"message": "The given data was invalid.", "errors": errors}


_LARAVEL_REQUIRED = _laravel_envelope({"email": ["The email field is required."]})


_LARAVEL_EMAIL_FORMAT = _laravel_envelope({"email": ["The email field must be a valid email address."]})


_LARAVEL_URL_FORMAT = _laravel_envelope({"site": ["The site field must be a valid URL."]})


_LARAVEL_UUID_FORMAT = _laravel_envelope({"token": ["The token field must be a valid UUID."]})


_LARAVEL_DATE_FORMAT = _laravel_envelope({"when": ["The when field must be a valid date."]})


_LARAVEL_INTEGER_TYPE = _laravel_envelope({"age": ["The age field must be an integer."]})


_LARAVEL_NUMERIC_TYPE = _laravel_envelope({"rate": ["The rate field must be a number."]})


_LARAVEL_BOOLEAN_TYPE = _laravel_envelope({"flag": ["The flag field must be true or false."]})


_LARAVEL_STRING_MIN = _laravel_envelope({"name": ["The name field must be at least 3 characters."]})


_LARAVEL_STRING_MAX = _laravel_envelope({"name": ["The name field must not be greater than 50 characters."]})


_LARAVEL_ARRAY_MIN = _laravel_envelope({"tags": ["The tags field must have at least 2 items."]})


_LARAVEL_ARRAY_MAX = _laravel_envelope({"tags": ["The tags field must not have more than 2 items."]})


_LARAVEL_INTEGER_MIN = _laravel_envelope({"age": ["The age field must be at least 0."]})


_LARAVEL_INTEGER_MAX = _laravel_envelope({"age": ["The age field must not be greater than 130."]})


_LARAVEL_NUMERIC_GT = _laravel_envelope({"rate": ["The rate field must be greater than 0."]})


_LARAVEL_NUMERIC_LT = _laravel_envelope({"rate": ["The rate field must be less than 100."]})


_LARAVEL_IN_LIST = _laravel_envelope({"role": ["The selected role is invalid."]})


_LARAVEL_REGEX = _laravel_envelope({"phone": ["The phone field format is invalid."]})


_LARAVEL_NESTED_DOTTED = _laravel_envelope({"user.email": ["The user.email field is required."]})


_LARAVEL_MULTI_FIELD = _laravel_envelope(
    {
        "email": ["The email field must be a valid email address."],
        "age": ["The age field must be an integer."],
        "name": ["The name field is required."],
        "role": ["The selected role is invalid."],
    }
)


_LARAVEL_ACCEPTED_BODIES = [
    pytest.param(_LARAVEL_REQUIRED, id="required"),
    pytest.param(_LARAVEL_EMAIL_FORMAT, id="email-format"),
    pytest.param(_LARAVEL_URL_FORMAT, id="url-format"),
    pytest.param(_LARAVEL_UUID_FORMAT, id="uuid-format"),
    pytest.param(_LARAVEL_DATE_FORMAT, id="date-format"),
    pytest.param(_LARAVEL_INTEGER_TYPE, id="integer-type"),
    pytest.param(_LARAVEL_NUMERIC_TYPE, id="numeric-type"),
    pytest.param(_LARAVEL_BOOLEAN_TYPE, id="boolean-type"),
    pytest.param(_LARAVEL_STRING_MIN, id="string-min"),
    pytest.param(_LARAVEL_STRING_MAX, id="string-max"),
    pytest.param(_LARAVEL_ARRAY_MIN, id="array-min"),
    pytest.param(_LARAVEL_ARRAY_MAX, id="array-max"),
    pytest.param(_LARAVEL_INTEGER_MIN, id="integer-min"),
    pytest.param(_LARAVEL_INTEGER_MAX, id="integer-max"),
    pytest.param(_LARAVEL_NUMERIC_GT, id="numeric-gt"),
    pytest.param(_LARAVEL_NUMERIC_LT, id="numeric-lt"),
    pytest.param(_LARAVEL_IN_LIST, id="in-list"),
    pytest.param(_LARAVEL_REGEX, id="regex"),
    pytest.param(_LARAVEL_NESTED_DOTTED, id="nested-dotted"),
    pytest.param(_LARAVEL_MULTI_FIELD, id="multi-field"),
]


@pytest.mark.parametrize("body", _LARAVEL_ACCEPTED_BODIES)
def test_laravel_parser_can_parse_recognises_envelope(body):
    assert LaravelParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        {"errors": {"email": ["The email field is required."]}},
        {"message": "The given data was invalid."},
        {
            "type": "...",
            "title": "One or more validation errors occurred.",
            "status": 400,
            "errors": {"Email": ["The Email field is required."]},
        },
        {
            "message": "The given data was invalid.",
            "type": "https://tools.ietf.org/html/rfc7231#section-6.5.1",
            "errors": {"email": ["The email field is required."]},
        },
        {"message": "Invalid", "errors": {"email": ["Custom validator output."]}},
        {"message": "The given data was invalid.", "errors": {"email": "The email field is required."}},
        {"message": "The given data was invalid.", "errors": {"email": [123]}},
        {"name": ["This field is required."]},
        {"errors": ["Email can't be blank"]},
        {"messages": ["email - must not be null"]},
        {"detail": [{"loc": ["body", "email"], "msg": "field required"}]},
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "errors-without-message",
        "message-without-errors",
        "aspnet-problemdetails",
        "aspnet-problemdetails-with-message",
        "envelope-without-laravel-vocabulary",
        "errors-value-not-list",
        "errors-list-non-string-item",
        "drf",
        "rails-legacy",
        "spring",
        "pydantic",
    ],
)
def test_laravel_parser_can_parse_rejects_non_laravel_bodies(body):
    assert LaravelParser().can_parse(body=body) is False


def _laravel_signatures(observations: tuple[Observation, ...]) -> list[tuple]:
    return sorted((o.parameter_path, o.kind, o.payload) for o in observations)


@pytest.mark.parametrize(
    ("method", "path", "body", "expected"),
    [
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_REQUIRED,
            (("POST /api/users", ParameterLocation.BODY, ("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="required",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_EMAIL_FORMAT,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("email",),
                    ObservationKind.FORMAT,
                    FormatPayload(name="email"),
                ),
            ),
            id="email-format",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_URL_FORMAT,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("site",),
                    ObservationKind.FORMAT,
                    FormatPayload(name="uri"),
                ),
            ),
            id="url-format",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_UUID_FORMAT,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("token",),
                    ObservationKind.FORMAT,
                    FormatPayload(name="uuid"),
                ),
            ),
            id="uuid-format",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_DATE_FORMAT,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("when",),
                    ObservationKind.FORMAT,
                    FormatPayload(name="date"),
                ),
            ),
            id="date-format",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_INTEGER_TYPE,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("age",),
                    ObservationKind.TYPE_MISMATCH,
                    TypeMismatchPayload(type_name="integer"),
                ),
            ),
            id="integer-type",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_NUMERIC_TYPE,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("rate",),
                    ObservationKind.TYPE_MISMATCH,
                    TypeMismatchPayload(type_name="number"),
                ),
            ),
            id="numeric-type",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_BOOLEAN_TYPE,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("flag",),
                    ObservationKind.TYPE_MISMATCH,
                    TypeMismatchPayload(type_name="boolean"),
                ),
            ),
            id="boolean-type",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_STRING_MIN,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("name",),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=3, max=None),
                ),
            ),
            id="string-min",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_STRING_MAX,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("name",),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=None, max=50),
                ),
            ),
            id="string-max",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_ARRAY_MIN,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("tags",),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=2, max=None),
                ),
            ),
            id="array-min",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_ARRAY_MAX,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("tags",),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=None, max=2),
                ),
            ),
            id="array-max",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_INTEGER_MIN,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="integer-min",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_INTEGER_MAX,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=130.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="integer-max",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_NUMERIC_GT,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("rate",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
                ),
            ),
            id="numeric-gt",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_NUMERIC_LT,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("rate",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=True),
                ),
            ),
            id="numeric-lt",
        ),
        pytest.param(
            "post",
            "/api/users",
            _LARAVEL_NESTED_DOTTED,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("user", "email"),
                    ObservationKind.MUST_NOT_BE_BLANK,
                    None,
                ),
            ),
            id="nested-dotted",
        ),
        pytest.param("post", "/api/users", _LARAVEL_IN_LIST, (), id="in-list-dropped"),
        pytest.param("post", "/api/users", _LARAVEL_REGEX, (), id="regex-dropped"),
        pytest.param(
            "post",
            "/api/users",
            _laravel_envelope({"email": ["", "The email field is required."]}),
            (("POST /api/users", ParameterLocation.BODY, ("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="empty-string-message-skipped",
        ),
        pytest.param(
            "get",
            "/api/users",
            _LARAVEL_REQUIRED,
            (("GET /api/users", ParameterLocation.QUERY, ("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="get-routes-to-query-location",
        ),
    ],
)
def test_laravel_parser_parse(make_operation, method, path, body, expected, case_factory):
    operation = make_operation(method=method, path=path)
    actual = LaravelParser().parse(operation=operation, body=body, case=case_factory())
    actual_signatures = tuple((o.operation_label, o.location, o.parameter_path, o.kind, o.payload) for o in actual)
    assert actual_signatures == expected


def test_laravel_parser_parse_multi_field_drops_unactionable(make_operation, case_factory):
    observations = LaravelParser().parse(
        operation=make_operation(),
        body=_LARAVEL_MULTI_FIELD,
        case=case_factory(),
    )
    assert _laravel_signatures(observations) == sorted(
        [
            (("email",), ObservationKind.FORMAT, FormatPayload(name="email")),
            (("age",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer")),
            (("name",), ObservationKind.MUST_NOT_BE_BLANK, None),
        ]
    )


def test_laravel_parser_parse_returns_empty_for_non_envelope(make_operation, case_factory):
    assert parse_observations(LaravelParser(), {"detail": "nope"}, make_operation, case_factory) == ()


@pytest.mark.parametrize(
    "parser",
    [RailsParser(), PydanticParser(), SpringParser(), JacksonParser()],
    ids=lambda p: type(p).__name__,
)
@pytest.mark.parametrize("body", _LARAVEL_ACCEPTED_BODIES)
def test_other_parsers_reject_laravel_bodies(parser, body):
    assert parser.can_parse(body=body) is False


def test_laravel_outranks_drf_when_both_claim_a_body():
    body = _LARAVEL_REQUIRED
    assert LaravelParser().can_parse(body=body) is True
    assert DRFParser().can_parse(body=body) is True
    assert LaravelParser.priority > DRFParser.priority
