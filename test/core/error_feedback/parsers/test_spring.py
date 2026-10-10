from __future__ import annotations

import json

import pytest

from schemathesis.core.error_feedback import (
    BoundDirection,
    FormatPayload,
    NumericBoundPayload,
    ObservationKind,
    PatternPayload,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.error_feedback.parsers.spring import SpringParser
from schemathesis.core.parameters import ParameterLocation
from test.core.error_feedback.parsers.helpers import SPRING_MESSAGES, parse_observations

SPRING_SUBERRORS = (
    b'{"time":"2026-04-30T04:48:07Z","httpStatus":"BAD_REQUEST",'
    b'"header":"VALIDATION ERROR","message":"Validation failed","isSuccess":false,'
    b'"subErrors":[{"message":"must not be blank","field":"password"}]}'
)


SPRING_PROBLEMDETAIL = (
    b'{"type":"http://localhost:8080/petclinic/api/owners",'
    b'"title":"MethodArgumentNotValidException","status":400,'
    b'"detail":"Validation failed for argument [0] in public ... '
    b"[Field error in object 'ownerFieldsDto' on field 'telephone': "
    b'rejected value [null]; codes [...]; default message [must not be null]] ",'
    b'"instance":"/petclinic/api/owners","timestamp":"..."}'
)


SPRING_ERRORS = b'{"errors":[{"field":"email","defaultMessage":"must not be blank"}]}'


SPRING_FIELDERRORS = b'{"fieldErrors":[{"property":"name","message":"must not be blank","code":"REQUIRED_NOT_BLANK"}]}'


SPRING_FIELDFIELD_PREFIX = b'{"subErrors":[{"message":"Name field cannot be empty","field":"name"}]}'


SPRING_FIELDERRORS_SHALL_NOT_BE_EMPTY = (
    b'{"message":"Argument validation error","description":"uri=/customer/contacts",'
    b'"entityName":"contactsDTO",'
    b'"fieldErrors":[{"field":"address","message":"The value shall not be empty"}]}'
)


@pytest.mark.parametrize(
    "body, expected_paths",
    [
        (SPRING_MESSAGES, [("zipcode",), ("city",)]),
        (SPRING_SUBERRORS, [("password",)]),
        (SPRING_PROBLEMDETAIL, [("telephone",)]),
        (SPRING_ERRORS, [("email",)]),
        (SPRING_FIELDERRORS, [("name",)]),
        (SPRING_FIELDFIELD_PREFIX, [("name",)]),
        (SPRING_FIELDERRORS_SHALL_NOT_BE_EMPTY, [("address",)]),
    ],
    ids=[
        "messages",
        "subErrors",
        "problemDetail",
        "errors",
        "fieldErrors",
        "subErrors-with-fieldname-prefix",
        "fieldErrors-shall-not-be-empty",
    ],
)
def test_spring_parser_extracts_observations(body, expected_paths, make_operation, case_factory):
    obs = parse_observations(SpringParser(), json.loads(body), make_operation, case_factory)
    assert [o.parameter_path for o in obs] == expected_paths
    assert all(o.kind is ObservationKind.MUST_NOT_BE_BLANK for o in obs)
    assert all(o.location is ParameterLocation.BODY for o in obs)


@pytest.mark.parametrize(
    "body",
    [
        {"messages": ["a - must not be blank"]},
        {"messages": []},
        {"subErrors": [{"field": "a", "message": "must not be blank"}]},
        {"subErrors": []},
        {"detail": "... [Field error in object 'X' on field 'y': default message [must not be null]]"},
        {"errors": [{"field": "a", "defaultMessage": "must not be blank"}]},
        {"fieldErrors": [{"property": "a", "message": "must not be blank"}]},
        {"fieldErrors": []},
    ],
    ids=[
        "messages",
        "messages-empty",
        "subErrors",
        "subErrors-empty",
        "detail-with-marker",
        "errors-dict-item",
        "fieldErrors",
        "fieldErrors-empty",
    ],
)
def test_spring_parser_can_parse_recognizes_spring_shapes(body):
    assert SpringParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"detail": "Some prose"},
        {"detail": 123},
        {"messages": [123]},
        {"messages": ["ok", 123]},
        {"messages": "not-a-list"},
        {"subErrors": "not-a-list"},
        {"errors": []},
        {"errors": "not-a-list"},
        {"errors": ["string-item", "another"]},
        {"fieldErrors": "not-a-list"},
        {"unrelated": "shape"},
        None,
        "",
        [],
        123,
    ],
    ids=[
        "empty-dict",
        "detail-without-marker",
        "detail-not-string",
        "messages-non-string-items",
        "messages-mixed-string-and-int",
        "messages-not-list",
        "subErrors-not-list",
        "errors-empty",
        "errors-not-list",
        "errors-non-dict-items",
        "fieldErrors-not-list",
        "unknown-keys-only",
        "none",
        "empty-string",
        "empty-list",
        "integer",
    ],
)
def test_spring_parser_can_parse_rejects_non_spring_bodies(body):
    assert SpringParser().can_parse(body=body) is False


@pytest.mark.parametrize(
    "message",
    [
        "must not be blank",
        "must not be null",
        "must not be empty",
        "cannot be empty",
        "is required",
        "MUST NOT BE BLANK",
        "Field cannot be empty for some reason",
        "This field is required",
        "First name can't be blank.",
        "can't be empty",
        "can't be null",
        "cannot be blank",
        "must be filled",
    ],
    ids=[
        "must-not-be-blank",
        "must-not-be-null",
        "must-not-be-empty",
        "cannot-be-empty",
        "is-required",
        "case-insensitive",
        "phrase-suffix",
        "phrase-prefix",
        "apostrophe-cant-be-blank",
        "apostrophe-cant-be-empty",
        "apostrophe-cant-be-null",
        "cannot-be-blank",
        "must-be-filled",
    ],
)
def test_spring_parser_recognizes_non_blank_message_variants(message, make_operation, case_factory):
    body = {"subErrors": [{"field": "x", "message": message}]}
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    assert [(o.parameter_path, o.kind) for o in obs] == [(("x",), ObservationKind.MUST_NOT_BE_BLANK)]


@pytest.mark.parametrize(
    "message",
    [
        "Some random validation error",
        "must match pattern '[A-Z]+'",
        "value out of range",
        "",
    ],
    ids=["random", "pattern", "range", "empty-string"],
)
def test_spring_parser_skips_unrecognized_messages(message, make_operation, case_factory):
    body = {"subErrors": [{"field": "x", "message": message}]}
    assert parse_observations(SpringParser(), body, make_operation, case_factory) == ()


@pytest.mark.parametrize(
    "message, expected_min, expected_max",
    [
        ("size must be between 0 and 15", 0, 15),
        ("size must be between 50 and 100", 50, 100),
        ("length must be between 5 and 64", 5, 64),
        ("SIZE MUST BE BETWEEN 1 AND 32", 1, 32),
    ],
    ids=["size-zero-min", "size-non-zero-min", "hibernate-length", "case-insensitive"],
)
def test_spring_parser_recognizes_size_bound_message_variants(
    message, expected_min, expected_max, make_operation, case_factory
):
    body = {"subErrors": [{"field": "username", "message": message}]}
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=expected_min, max=expected_max)),
    ]


def test_spring_parser_recognizes_hibernate_range_message(make_operation, case_factory):
    # Hibernate's `@Range` reports a numeric interval; the `size`/`length` prefix marks a length one.
    body = {
        "errors": [
            {
                "field": "month",
                "defaultMessage": "must be between 1 and 12",
                "rejectedValue": [-2147483648],
            }
        ]
    }
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (
            ("month",),
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=1.0, direction=BoundDirection.MIN, exclusive=False),
        ),
        (
            ("month",),
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=12.0, direction=BoundDirection.MAX, exclusive=False),
        ),
    ]


@pytest.mark.parametrize(
    "message, expected_name",
    [
        ("must be a well-formed email address", "email"),
        ("must be a valid email address", "email"),
        ("must be a valid email", "email"),
        ("MUST BE A WELL-FORMED EMAIL ADDRESS", "email"),
        ("Please enter a valid email address", "email"),
        ("Please enter a valid e-mail address", "email"),
        ("valid e-mail", "email"),
        ("must be a valid URL", "uri"),
        ("must be a valid URI", "uri"),
        ("must be a valid UUID", "uuid"),
        ("must be a well-formed UUID", "uuid"),
    ],
    ids=[
        "email-well-formed",
        "email-valid-address",
        "email-valid-bare",
        "email-case-insensitive",
        "email-no-must-be-prefix",
        "email-hyphenated-spelling",
        "email-bare-valid",
        "url",
        "uri",
        "uuid-valid",
        "uuid-well-formed",
    ],
)
def test_spring_parser_recognizes_format_message_variants(message, expected_name, make_operation, case_factory):
    body = {"subErrors": [{"field": "contact", "message": message}]}
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (("contact",), ObservationKind.FORMAT, FormatPayload(name=expected_name)),
    ]


def test_spring_parser_uuid_takes_precedence_over_uri_when_both_match(make_operation, case_factory):
    # Defensive: a contrived "must be a valid URI UUID" string would match both
    # the URI and UUID regexes. The classifier checks UUID first so the more
    # specific format wins.
    body = {"subErrors": [{"field": "x", "message": "must be a valid UUID"}]}
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    assert obs[0].payload == FormatPayload(name="uuid")


@pytest.mark.parametrize(
    "message, expected_bound, expected_direction, expected_exclusive",
    [
        # `@Min` / `@Max` / `@DecimalMin` / `@DecimalMax` defaults.
        ("must be greater than or equal to 0", 0.0, BoundDirection.MIN, False),
        ("must be less than or equal to 100", 100.0, BoundDirection.MAX, False),
        ("must be greater than 0.5", 0.5, BoundDirection.MIN, True),
        ("must be less than 99.99", 99.99, BoundDirection.MAX, True),
        ("must be greater than -50", -50.0, BoundDirection.MIN, True),
        # `@Positive` / `@Negative` / `@PositiveOrZero` / `@NegativeOrZero` —
        # Hibernate expands these to "greater/less than 0" with the matching suffix.
        ("must be greater than 0", 0.0, BoundDirection.MIN, True),
        ("must be less than 0", 0.0, BoundDirection.MAX, True),
        ("must be less than or equal to 0", 0.0, BoundDirection.MAX, False),
        ("MUST BE GREATER THAN 5", 5.0, BoundDirection.MIN, True),
    ],
    ids=[
        "min-inclusive",
        "max-inclusive",
        "decimal-min-exclusive",
        "decimal-max-exclusive",
        "negative-bound",
        "positive",
        "negative",
        "negative-or-zero",
        "case-insensitive",
    ],
)
def test_spring_parser_recognizes_numeric_bound_message_variants(
    message, expected_bound, expected_direction, expected_exclusive, make_operation, case_factory
):
    body = {"subErrors": [{"field": "score", "message": message}]}
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (
            ("score",),
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=expected_bound, direction=expected_direction, exclusive=expected_exclusive),
        ),
    ]


@pytest.mark.parametrize(
    "message, expected_bound, expected_direction, expected_exclusive",
    [
        ("Value shall be a positive number", 0.0, BoundDirection.MIN, True),
        ("Value shall be a non-negative number", 0.0, BoundDirection.MIN, False),
        ("Value shall be a non negative number", 0.0, BoundDirection.MIN, False),
        ("Value shall be a negative number", 0.0, BoundDirection.MAX, True),
        ("Value shall be a non-positive number", 0.0, BoundDirection.MAX, False),
        ("must be a positive value", 0.0, BoundDirection.MIN, True),
        ("VALUE SHALL BE A POSITIVE NUMBER", 0.0, BoundDirection.MIN, True),
    ],
    ids=[
        "positive",
        "non-negative-hyphen",
        "non-negative-space",
        "negative",
        "non-positive",
        "must-be-positive-value",
        "case-insensitive",
    ],
)
def test_spring_parser_recognizes_positive_negative_keyword_variants(
    message, expected_bound, expected_direction, expected_exclusive, make_operation, case_factory
):
    body = {"subErrors": [{"field": "score", "message": message}]}
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (
            ("score",),
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=expected_bound, direction=expected_direction, exclusive=expected_exclusive),
        ),
    ]


@pytest.mark.parametrize(
    "body",
    [
        {
            "errors": [
                {"field": "name", "defaultMessage": "must not be blank"},
                {"field": 5, "defaultMessage": "must not be blank"},
            ]
        },
        {
            "fieldErrors": [
                {"property": "name", "message": "must not be blank"},
                {"property": 5, "message": "must not be blank"},
            ]
        },
    ],
    ids=["errors", "field-errors"],
)
def test_spring_parser_ignores_non_string_field_names(make_operation, case_factory, body):
    observations = parse_observations(SpringParser(), body, make_operation, case_factory)
    assert [(o.parameter_path, o.kind) for o in observations] == [(("name",), ObservationKind.MUST_NOT_BE_BLANK)]


@pytest.mark.parametrize(
    "body, expected",
    [
        pytest.param(
            {"message": "Unrecognized field: 'extraField'"},
            [(("extraField",), ParameterLocation.BODY)],
            id="top-level-message-double-quotes",
        ),
        pytest.param(
            {"error": 'Unrecognized field: "manager"'},
            [(("manager",), ParameterLocation.BODY)],
            id="top-level-error-double-quotes",
        ),
        pytest.param(
            {"fieldErrors": [{"field": "x", "message": "Unrecognized field: 'shadow'"}]},
            [(("shadow",), ParameterLocation.BODY)],
            id="inside-fieldErrors-message",
        ),
        pytest.param(
            {"errors": [{"defaultMessage": "Unrecognized field: 'note'"}]},
            [(("note",), ParameterLocation.BODY)],
            id="inside-errors-defaultMessage",
        ),
        pytest.param(
            {"message": "Unrecognized field: 'a' Unrecognized field: 'b'"},
            [(("a",), ParameterLocation.BODY), (("b",), ParameterLocation.BODY)],
            id="multiple-matches-in-one-string",
        ),
        pytest.param(
            {
                "message": 'JSON parse error: Unrecognized field "commitDate" (class com.x.Assignment), '
                "not marked as ignorable"
            },
            [(("commitDate",), ParameterLocation.BODY)],
            id="spring-boot-jackson-message",
        ),
        pytest.param(
            {
                "message": 'Unrecognized field "commitDate" (class com.x.Assignment), not marked as ignorable '
                '(2 known properties: "spender", "value"])'
            },
            [(("commitDate",), ParameterLocation.BODY)],
            id="raw-jackson-message",
        ),
        pytest.param(
            {
                "detail": 'JSON parse error: Unrecognized field "commitDate" (class com.x.Assignment), '
                "not marked as ignorable"
            },
            [(("commitDate",), ParameterLocation.BODY)],
            id="problem-detail-jackson-message",
        ),
    ],
)
def test_spring_parser_extracts_unrecognized_field(body, expected, make_operation, case_factory):
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    actual = [(o.parameter_path, o.location) for o in obs if o.kind == ObservationKind.UNEXPECTED_PROPERTY]
    assert actual == expected
    for observation in obs:
        if observation.kind == ObservationKind.UNEXPECTED_PROPERTY:
            assert observation.payload is None


@pytest.mark.parametrize(
    "body, expected_name",
    [
        pytest.param(
            {"message": 'parameter name "page_size" is not allowed'},
            "page_size",
            id="double-quoted",
        ),
        pytest.param(
            {"message": "parameter name 'sort' is not allowed"},
            "sort",
            id="single-quoted",
        ),
        pytest.param(
            {"detail": 'parameter name "limit" is not allowed'},
            "limit",
            id="rfc7807-detail",
        ),
    ],
)
def test_spring_parser_extracts_unexpected_query_parameter(body, expected_name, make_operation, case_factory):
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    actual = [(o.parameter_path, o.location, o.kind) for o in obs if o.kind == ObservationKind.UNEXPECTED_PROPERTY]
    assert actual == [((expected_name,), ParameterLocation.QUERY, ObservationKind.UNEXPECTED_PROPERTY)]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            {
                "message": (
                    "Required request body is missing: public com.example.demo.common.model.dto.response."
                    "CustomResponse<java.lang.String> com.example.demo.flight.controller.AirportController."
                    "createAirport(com.example.demo.flight.model.dto.request.airport.CreateAirportRequest)"
                ),
            },
            id="message-with-method-signature",
        ),
        pytest.param(
            {"message": "Required request body is missing"},
            id="bare-message",
        ),
        pytest.param(
            {"detail": "Required request body is missing for handler method"},
            id="rfc7807-detail",
        ),
    ],
)
def test_spring_parser_extracts_missing_request_body(body, make_operation, case_factory):
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    blank_body_obs = [
        (o.parameter_path, o.location, o.kind)
        for o in obs
        if o.kind == ObservationKind.MUST_NOT_BE_BLANK and o.location == ParameterLocation.BODY
    ]
    assert blank_body_obs == [((), ParameterLocation.BODY, ObservationKind.MUST_NOT_BE_BLANK)]


@pytest.mark.parametrize(
    "type_name, expected_bound",
    [
        pytest.param("Integer", 2147483647, id="integer-int32-max"),
        pytest.param("Long", 9223372036854775807, id="long-int64-max"),
    ],
)
def test_spring_parser_extracts_pagination_type_hint(type_name, expected_bound, make_operation, case_factory):
    body = {
        "error": "Bad Request",
        "status": 400,
        "messages": [f"Parameter 'page' must be '{type_name}'"],
        "timestamp": "2026-05-02T21:47:10.769780Z",
    }
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    assert [(o.parameter_path, o.location, o.kind, o.payload) for o in obs] == [
        (
            ("page",),
            ParameterLocation.QUERY,
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=float(expected_bound), direction=BoundDirection.MAX, exclusive=False),
        ),
    ]


def test_spring_parser_emits_path_location_when_field_is_a_path_parameter(make_operation, case_factory):
    # `subErrors[].field = "id"` for a path parameter `id` — the Spring parser
    # historically hardcoded `location=BODY`, leaving the observation orphaned.
    body = {
        "message": "Constraint violation",
        "subErrors": [{"field": "id", "message": "must be a valid UUID", "value": "0", "type": "String"}],
    }
    operation = make_operation(method="get", path="/airports/{id}")
    obs = SpringParser().parse(operation=operation, body=body, case=case_factory())
    assert [(o.parameter_path, o.location, o.kind, o.payload) for o in obs] == [
        (("id",), ParameterLocation.PATH, ObservationKind.FORMAT, FormatPayload(name="uuid")),
    ]


def test_spring_parser_scans_msg_carrier_for_existing_patterns(make_operation, case_factory):
    # Custom Spring envelopes use `msg` instead of `message` for the user-facing
    # text. Existing patterns (`MissingServletRequestParameterException`, etc.)
    # should fire when their phrasing appears under `msg`.
    body = {
        "msg": "Required Integer parameter 'page' is not present",
        "throwable": None,
        "status": "BAD_REQUEST",
    }
    operation = make_operation(method="get", path="/items")
    obs = SpringParser().parse(operation=operation, body=body, case=case_factory())
    assert [(o.parameter_path, o.location, o.kind) for o in obs] == [
        (("page",), ParameterLocation.QUERY, ObservationKind.MUST_NOT_BE_BLANK),
    ]


def test_spring_parser_extracts_positive_gate_from_market_cart_body(make_operation, case_factory):
    # Verbatim wire sample: two `Value shall be a positive number` fieldErrors must
    # surface as exclusive lower bounds at 0.
    body = {
        "message": "Argument validation error",
        "description": "uri=/customer/cart",
        "entityName": "cartItemDTO",
        "fieldErrors": [
            {"field": "quantity", "message": "Value shall be a positive number"},
            {"field": "productId", "message": "Value shall be a positive number"},
        ],
    }
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (
            ("quantity",),
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
        ),
        (
            ("productId",),
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
        ),
    ]


@pytest.mark.parametrize(
    "message, expected_regex",
    [
        ('must match "[A-Z]+"', "[A-Z]+"),
        ('must match "^\\d{3,4}$"', "^\\d{3,4}$"),
        ('must match "[A-Za-z][A-Za-z0-9_-]{2,15}"', "[A-Za-z][A-Za-z0-9_-]{2,15}"),
        ('must match "\\p{L}+"', "\\p{L}+"),
    ],
    ids=["simple-charclass", "anchored-quantifier", "username-style", "pcre-unicode-property"],
)
def test_spring_parser_recognizes_pattern_message_variants(message, expected_regex, make_operation, case_factory):
    body = {"subErrors": [{"field": "code", "message": message}]}
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (("code",), ObservationKind.PATTERN, PatternPayload(regex=expected_regex)),
    ]


_SPRING_MISSING_PARAMETER_BODY = {
    "timestamp": "2026-05-01T01:00:40.560+0000",
    "status": 400,
    "error": "Bad Request",
    "message": "Required Double parameter 'lat' is not present",
    "path": "/v1/locations/nearest",
}


# Spring 6 / RFC 7807 Problem Detail.
_SPRING_TYPE_COERCION_PROBLEM_DETAIL = {
    "type": "http://localhost:8080/api/owners/null%2Cnull/pets",
    "title": "MethodArgumentTypeMismatchException",
    "status": 500,
    "detail": (
        "Method parameter 'ownerId': Failed to convert value of type "
        "'java.lang.String' to required type 'java.lang.Integer'; "
        'For input string: "null"'
    ),
}


def test_spring_parser_recognizes_missing_request_parameter(make_operation, case_factory):
    obs = SpringParser().parse(
        operation=make_operation(method="get", path="/v1/locations/nearest"),
        body=_SPRING_MISSING_PARAMETER_BODY,
        case=case_factory(),
    )
    assert [(o.parameter_path, o.kind, o.location) for o in obs] == [
        (("lat",), ObservationKind.MUST_NOT_BE_BLANK, ParameterLocation.QUERY),
    ]


def test_spring_parser_can_parse_recognizes_missing_parameter_envelope():
    assert SpringParser().can_parse(body=_SPRING_MISSING_PARAMETER_BODY) is True


def test_spring_parser_recognizes_method_argument_type_mismatch(make_operation, case_factory):
    # Field captured from `Method parameter 'ownerId':` prefix; emitted on both
    # PATH and QUERY because the message doesn't pin the binding.
    obs = SpringParser().parse(
        operation=make_operation(method="get", path="/api/owners/{ownerId}/pets"),
        body=_SPRING_TYPE_COERCION_PROBLEM_DETAIL,
        case=case_factory(),
    )
    assert [(o.parameter_path, o.kind, o.location, o.payload) for o in obs] == [
        (
            ("ownerId",),
            ObservationKind.TYPE_MISMATCH,
            ParameterLocation.PATH,
            TypeMismatchPayload(type_name="java.lang.Integer"),
        ),
        (
            ("ownerId",),
            ObservationKind.TYPE_MISMATCH,
            ParameterLocation.QUERY,
            TypeMismatchPayload(type_name="java.lang.Integer"),
        ),
    ]


def test_spring_parser_can_parse_recognizes_type_coercion_envelope():
    assert SpringParser().can_parse(body=_SPRING_TYPE_COERCION_PROBLEM_DETAIL) is True


def test_spring_parser_skips_type_coercion_without_method_parameter_prefix(make_operation, case_factory):
    # Older Spring stdlib envelope omits the `Method parameter 'X':` prefix —
    # without a field name we can't attribute, so we don't emit.
    body = {
        "timestamp": "2026-05-01T01:54:33.490+0000",
        "status": 400,
        "error": "Bad Request",
        "message": (
            "Failed to convert value of type 'java.lang.String' to required type 'java.lang.Double'; "
            'nested exception is java.lang.NumberFormatException: For input string: "x"'
        ),
        "path": "/v1/locations/nearest",
    }
    assert (
        SpringParser().parse(
            operation=make_operation(method="get", path="/v1/locations/nearest"), body=body, case=case_factory()
        )
        == ()
    )


SPRING_MESSAGES_MULTI = b'{"messages":["email - must not be blank","username - must not be null","age - is required"]}'


SPRING_SUBERRORS_MULTI = (
    b'{"subErrors":[{"field":"email","message":"must not be blank"},{"field":"username","message":"is required"}]}'
)


SPRING_PROBLEMDETAIL_MULTI = (
    b'{"detail":"Validation failed: '
    b"[Field error in object 'X' on field 'email': rejected value [null]; "
    b"codes [...]; default message [must not be null]] "
    b"[Field error in object 'X' on field 'name': rejected value []; "
    b'codes [...]; default message [must not be blank]]"}'
)


SPRING_ERRORS_MULTI = (
    b'{"errors":['
    b'{"field":"email","defaultMessage":"must not be blank"},'
    b'{"field":"username","defaultMessage":"is required"}'
    b"]}"
)


SPRING_FIELDERRORS_MULTI = (
    b'{"fieldErrors":['
    b'{"property":"email","message":"must not be blank"},'
    b'{"property":"username","message":"is required"}'
    b"]}"
)


@pytest.mark.parametrize(
    "body, expected_paths",
    [
        (SPRING_MESSAGES_MULTI, [("email",), ("username",), ("age",)]),
        (SPRING_SUBERRORS_MULTI, [("email",), ("username",)]),
        (SPRING_PROBLEMDETAIL_MULTI, [("email",), ("name",)]),
        (SPRING_ERRORS_MULTI, [("email",), ("username",)]),
        (SPRING_FIELDERRORS_MULTI, [("email",), ("username",)]),
    ],
    ids=["messages", "subErrors", "problemDetail", "errors", "fieldErrors"],
)
def test_spring_parser_extracts_multiple_entries_per_shape(body, expected_paths, make_operation, case_factory):
    obs = parse_observations(SpringParser(), json.loads(body), make_operation, case_factory)
    assert [o.parameter_path for o in obs] == expected_paths


@pytest.mark.parametrize(
    "body, expected_path",
    [
        (
            {"subErrors": [{"field": "address.street", "message": "must not be blank"}]},
            ("address", "street"),
        ),
        (
            {"messages": ["address.city.zip - must not be blank"]},
            ("address", "city", "zip"),
        ),
        (
            {"errors": [{"field": "user.email", "defaultMessage": "must not be blank"}]},
            ("user", "email"),
        ),
        (
            {"fieldErrors": [{"property": "owner.contact.phone", "message": "must not be null"}]},
            ("owner", "contact", "phone"),
        ),
    ],
    ids=["subErrors-2-deep", "messages-3-deep", "errors-2-deep", "fieldErrors-3-deep"],
)
def test_spring_parser_splits_dotted_paths_into_tuples(body, expected_path, make_operation, case_factory):
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    assert [o.parameter_path for o in obs] == [expected_path]


@pytest.mark.parametrize(
    "entry, expected",
    [
        (
            {"field": "x", "defaultMessage": "must not be blank", "message": "Some random text"},
            [("x",)],
        ),
        (
            {"field": "x", "message": "must not be blank"},
            [("x",)],
        ),
        (
            {"field": "x", "defaultMessage": "Some random text", "message": "must not be blank"},
            [],
        ),
        (
            {"field": "x", "code": "REQUIRED"},
            [],
        ),
        (
            {"defaultMessage": "must not be blank"},
            [],
        ),
    ],
    ids=[
        "default-message-takes-priority",
        "message-fallback-when-no-default",
        "default-message-shadows-message",
        "no-message-skipped",
        "no-field-skipped",
    ],
)
def test_spring_parser_errors_field_and_message_priority(entry, expected, make_operation, case_factory):
    body = {"errors": [entry]}
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    assert [o.parameter_path for o in obs] == expected


@pytest.mark.parametrize(
    "entry, expected",
    [
        (
            {"property": "p", "field": "f", "path": "h", "message": "must not be blank"},
            [("p",)],
        ),
        (
            {"field": "f", "path": "h", "message": "must not be blank"},
            [("f",)],
        ),
        (
            {"path": "h", "message": "must not be blank"},
            [("h",)],
        ),
        (
            {"property": "p", "defaultMessage": "must not be blank"},
            [("p",)],
        ),
        (
            {"property": "p", "message": "must not be blank", "defaultMessage": "Some random text"},
            [("p",)],
        ),
        (
            {"message": "must not be blank"},
            [],
        ),
    ],
    ids=[
        "property-shadows-all",
        "field-when-no-property",
        "path-when-no-property-or-field",
        "default-message-fallback",
        "message-takes-priority-over-default",
        "no-locator-skipped",
    ],
)
def test_spring_parser_field_errors_locator_and_message_priority(entry, expected, make_operation, case_factory):
    body = {"fieldErrors": [entry]}
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    assert [o.parameter_path for o in obs] == expected


@pytest.mark.parametrize(
    "body",
    [
        {"messages": ["just some text without a dash"]},
        {"messages": ["x - some random message"]},
        {"messages": [123, None, []]},
        {"subErrors": ["string-item", 123]},
        {"subErrors": [{"field": 123, "message": "must not be blank"}]},
        {"subErrors": [{"field": "x", "message": 123}]},
        {"subErrors": [{}]},
        {
            "detail": "no field/message pairs here, just prose with the marker Field error in object 'X' but no on-field clause"
        },
        {"errors": [123, None, "string"]},
        {"errors": [{"field": "x", "message": "Some random message"}]},
        {"fieldErrors": [123, None]},
        {"fieldErrors": [{"property": "x", "message": "Some random text"}]},
    ],
    ids=[
        "messages-no-dash",
        "messages-unknown-message",
        "messages-non-string-items",
        "subErrors-non-dict-items",
        "subErrors-non-string-field",
        "subErrors-non-string-message",
        "subErrors-empty-dict",
        "detail-no-on-field",
        "errors-non-dict-items",
        "errors-unknown-message",
        "fieldErrors-non-dict-items",
        "fieldErrors-unknown-message",
    ],
)
def test_spring_parser_skips_invalid_or_unrecognized_entries(body, make_operation, case_factory):
    assert parse_observations(SpringParser(), body, make_operation, case_factory) == ()


def test_spring_parser_mixes_valid_and_invalid_messages(make_operation, case_factory):
    body = {
        "messages": [
            "valid - must not be blank",
            123,
            "no_dash_here",
            "another - is required",
            "x - just some prose",
        ]
    }
    obs = parse_observations(SpringParser(), body, make_operation, case_factory)
    assert [o.parameter_path for o in obs] == [("valid",), ("another",)]


@pytest.mark.parametrize(
    "body",
    [[1, 2, 3], "not a dict", None, 42, 1.5, True],
    ids=["list", "string", "none", "int", "float", "bool"],
)
def test_spring_parser_returns_empty_for_non_dict_body(body, make_operation, case_factory):
    assert parse_observations(SpringParser(), body, make_operation, case_factory) == ()
