from __future__ import annotations

import pytest

from schemathesis.core.error_feedback import (
    BoundDirection,
    EnumPayload,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    TypeMismatchPayload,
)
from schemathesis.core.error_feedback.parsers.jackson import JacksonParser
from schemathesis.core.error_feedback.parsers.spring import SpringParser
from schemathesis.core.parameters import ParameterLocation
from test.core.error_feedback.parsers.helpers import parse_observations


@pytest.mark.parametrize(
    "source",
    ["Object", "Array"],
    ids=["object-source", "array-source"],
)
def test_jackson_parser_extracts_type_mismatch_for_object_or_array_source(source, make_operation, case_factory):
    body = {
        "message": (
            f"JSON parse error: Cannot deserialize value of type `java.lang.String` "
            f"from {source} value (token `JsonToken.START_{source.upper()}`) "
            f'(through reference chain: AirportRequest["cityName"])'
        ),
    }
    obs = parse_observations(JacksonParser(), body, make_operation, case_factory)
    assert [(o.parameter_path, o.location, o.kind, o.payload) for o in obs] == [
        (
            ("cityName",),
            ParameterLocation.BODY,
            ObservationKind.TYPE_MISMATCH,
            TypeMismatchPayload(type_name="java.lang.String"),
        ),
    ]


_DATE_TIME_FORMAT_OBS = [
    (("departureTime",), ParameterLocation.BODY, ObservationKind.FORMAT, FormatPayload(name="date-time")),
]


@pytest.mark.parametrize(
    "rejected_value, body, expected",
    [
        pytest.param(
            "2000-01-01T00:00:00Z",
            {"departureTime": "2000-01-01T00:00:00Z"},
            _DATE_TIME_FORMAT_OBS,
            id="value-not-mutated-by-spring",
        ),
        pytest.param(
            "2000-01-01T00:00:00ZT00:00:00",
            {"departureTime": "2000-01-01T00:00:00Z"},
            _DATE_TIME_FORMAT_OBS,
            id="instant-with-z-coerced-to-localdatetime",
        ),
        pytest.param(
            "2000-01-01T00:00:00",
            {"departureTime": "2000-01-01"},
            _DATE_TIME_FORMAT_OBS,
            id="date-only-coerced-to-localdatetime",
        ),
        pytest.param(
            "9999-12-31",
            {"departureTime": "2000-01-01T00:00:00Z"},
            [],
            id="rejected-value-not-in-body-no-suffix",
        ),
    ],
)
def test_jackson_parser_extracts_date_parse_format(rejected_value, body, expected, make_operation, case_factory):
    response_body = {"message": f"JSON parse error: Text '{rejected_value}' could not be parsed"}
    operation = make_operation(method="post", path="/api/v1/flights/search")
    case = case_factory(operation=operation, body=body)
    obs = JacksonParser().parse(operation=operation, body=response_body, case=case)
    assert [(o.parameter_path, o.location, o.kind, o.payload) for o in obs] == expected


def test_pipeline_falls_through_to_jackson_for_empty_field_errors_envelope(make_operation, case_factory):
    # Spring envelope with empty fieldErrors + Jackson-shaped top-level `message`:
    # Spring returns nothing, pipeline falls through, Jackson picks it up.
    body = {
        "message": (
            "JSON parse error: Cannot deserialize instance of `java.lang.String` "
            'out of START_OBJECT token; (through reference chain: market.dto.CreditCardDTO["ccNumber"])'
        ),
        "description": "uri=/customer/cart/pay",
        "entityName": None,
        "fieldErrors": [],
    }
    operation = make_operation()
    case = case_factory()
    spring_obs = SpringParser().parse(operation=operation, body=body, case=case)
    jackson_obs = JacksonParser().parse(operation=operation, body=body, case=case)
    assert spring_obs == ()
    assert [(o.parameter_path, o.kind, o.payload) for o in jackson_obs] == [
        (("ccNumber",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="java.lang.String")),
    ]


_JACKSON_LOCAL_DATE = (
    'JSON parse error: Cannot deserialize value of type `java.time.LocalDate` from String "dd-MM-yyyy" '
    'through reference chain: User["hire_date"]'
)


_JACKSON_LOCAL_DATETIME = (
    'Cannot deserialize value of type `java.time.LocalDateTime` from String "now" '
    'through reference chain: Event["startedAt"]'
)


_JACKSON_UUID = (
    'Cannot deserialize value of type `java.util.UUID` from String "abc" through reference chain: Token["id"]'
)


_JACKSON_NESTED_CHAIN = (
    'Cannot deserialize value of type `java.time.LocalDate` from String "x" '
    'through reference chain: Owner["address"]->Address["created_on"]'
)


_JACKSON_INNER_CLASS = (
    'Cannot deserialize value of type `java.util.Map$Entry` from String "x" through reference chain: User["meta"]'
)


_JACKSON_GENERIC_TYPE = (
    'Cannot deserialize value of type `java.util.List<java.lang.Integer>` from String "x" '
    'through reference chain: User["scores"]'
)


# Pre-2.10 wording: bare type, no backticks, "Can not" verb form.
_JACKSON_LEGACY_LOCAL_DATE = (
    "Can not deserialize instance of java.time.LocalDate out of VALUE_STRING token "
    'through reference chain: User["hire_date"]'
)


_JACKSON_LEGACY_UUID = (
    'Can not deserialize instance of java.util.UUID out of VALUE_STRING token through reference chain: Token["id"]'
)


# Collection-element failure: chain has a bare `[N]` between the field and the
# leaf. Jackson uses this when deserialization fails inside a list/array element.
# The index value is irrelevant for JSON Schema (every element shares `items`),
# but it must appear in the path so the walker takes the `items` branch.
_JACKSON_ARRAY_ELEMENT = (
    'Cannot deserialize value of type `java.time.LocalDate` from String "x" '
    'through reference chain: User["addresses"]->java.util.ArrayList[0]->Address["created_on"]'
)


# Modern Jackson with non-String source (object / array / boolean) — different
# verb form ("instance of" rather than "value of type") and the source token
# replaces the String quoting.
_JACKSON_NON_STRING_SOURCE = (
    "JSON parse error: Cannot deserialize instance of `java.util.Date` out of START_OBJECT token; "
    "nested exception is com.fasterxml.jackson.databind.exc.MismatchedInputException: "
    "Cannot deserialize instance of `java.util.Date` out of START_OBJECT token "
    'through reference chain: Patient["checkin"]'
)


_JACKSON_NON_STRING_ARRAY_SOURCE = (
    "Cannot deserialize instance of `java.lang.Integer` out of START_ARRAY token "
    'through reference chain: Order["quantity"]'
)


# Jackson enum-deserialization: the `not one of the values accepted` clause
# names the valid literals inline. A single message carries both the offending
# Java type and the accepted value list — parser emits both kinds of observation.
_JACKSON_ENUM_USERTYPE = (
    "JSON parse error: Cannot deserialize value of type "
    '`com.example.demo.auth.model.enums.UserType` from String "AAA": '
    "not one of the values accepted for Enum class: [USER, ADMIN] "
    'through reference chain: RegisterRequest["userType"]'
)


_JACKSON_ENUM_BARE = (
    "not one of the values accepted for Enum class: [PENDING, ACTIVE, ARCHIVED] "
    'through reference chain: Subscription["status"]'
)


@pytest.mark.parametrize(
    "carrier_key, message, expected_path, expected_type",
    [
        ("msg", _JACKSON_LOCAL_DATE, ("hire_date",), "java.time.LocalDate"),
        ("message", _JACKSON_LOCAL_DATETIME, ("startedAt",), "java.time.LocalDateTime"),
        ("error", _JACKSON_UUID, ("id",), "java.util.UUID"),
        ("detail", _JACKSON_NESTED_CHAIN, ("address", "created_on"), "java.time.LocalDate"),
        ("msg", _JACKSON_INNER_CLASS, ("meta",), "java.util.Map$Entry"),
        ("msg", _JACKSON_GENERIC_TYPE, ("scores",), "java.util.List<java.lang.Integer>"),
        ("msg", _JACKSON_LEGACY_LOCAL_DATE, ("hire_date",), "java.time.LocalDate"),
        ("detail", _JACKSON_LEGACY_UUID, ("id",), "java.util.UUID"),
        ("msg", _JACKSON_ARRAY_ELEMENT, ("addresses", 0, "created_on"), "java.time.LocalDate"),
        ("message", _JACKSON_NON_STRING_SOURCE, ("checkin",), "java.util.Date"),
        ("message", _JACKSON_NON_STRING_ARRAY_SOURCE, ("quantity",), "java.lang.Integer"),
    ],
    ids=[
        "msg-localdate",
        "message-localdatetime",
        "error-uuid",
        "detail-nested-chain",
        "inner-class-name",
        "generic-type-name",
        "legacy-pre-2.10-localdate",
        "legacy-pre-2.10-uuid",
        "array-element-failure",
        "non-string-object-source",
        "non-string-array-source",
    ],
)
def test_jackson_parser_extracts_observations(
    carrier_key, message, expected_path, expected_type, make_operation, case_factory
):
    body = {carrier_key: message}
    obs = parse_observations(JacksonParser(), body, make_operation, case_factory)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (expected_path, ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name=expected_type)),
    ]


def test_jackson_parser_emits_both_type_and_enum_for_enum_message(make_operation, case_factory):
    body = {"msg": _JACKSON_ENUM_USERTYPE}
    obs = parse_observations(JacksonParser(), body, make_operation, case_factory)
    assert [(o.kind, o.payload) for o in obs] == [
        (
            ObservationKind.TYPE_MISMATCH,
            TypeMismatchPayload(type_name="com.example.demo.auth.model.enums.UserType"),
        ),
        (ObservationKind.ENUM, EnumPayload(values=("USER", "ADMIN"))),
    ]
    assert all(o.parameter_path == ("userType",) for o in obs)


def test_jackson_parser_emits_enum_only_when_type_clause_is_absent(make_operation, case_factory):
    body = {"msg": _JACKSON_ENUM_BARE}
    obs = parse_observations(JacksonParser(), body, make_operation, case_factory)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (
            ("status",),
            ObservationKind.ENUM,
            EnumPayload(values=("PENDING", "ACTIVE", "ARCHIVED")),
        ),
    ]


@pytest.mark.parametrize(
    "values_blob, expected",
    [
        ("USER, ADMIN", ("USER", "ADMIN")),
        ("USER,ADMIN", ("USER", "ADMIN")),
        ("ONE", ("ONE",)),
        ("  USER ,  ADMIN  ", ("USER", "ADMIN")),
        ("LOW, MEDIUM, HIGH, CRITICAL", ("LOW", "MEDIUM", "HIGH", "CRITICAL")),
    ],
    ids=["space-separated", "no-spaces", "single-value", "extra-whitespace", "many-values"],
)
def test_jackson_parser_enum_value_list_variants(values_blob, expected, make_operation, case_factory):
    message = (
        f'Cannot deserialize value of type `Status` from String "x": '
        f"not one of the values accepted for Enum class: [{values_blob}] "
        f'through reference chain: Order["status"]'
    )
    obs = parse_observations(JacksonParser(), {"msg": message}, make_operation, case_factory)
    enum_payloads = [o.payload for o in obs if o.kind is ObservationKind.ENUM]
    assert enum_payloads == [EnumPayload(values=expected)]


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        {"detail": "validation failed"},
        {"msg": 123},
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "wrong-text-in-detail",
        "non-string-msg",
    ],
)
def test_jackson_parser_can_parse_rejects_non_jackson_bodies(body):
    assert JacksonParser().can_parse(body=body) is False


def test_jackson_parser_skips_message_without_reference_chain(make_operation, case_factory):
    # Without request context, no field can be attributed; message is dropped.
    body = {"msg": 'Cannot deserialize value of type `java.time.LocalDate` from String "x"'}
    assert JacksonParser().can_parse(body=body) is True
    assert parse_observations(JacksonParser(), body, make_operation, case_factory) == ()


@pytest.mark.parametrize(
    "body",
    [
        None,
        "not a dict",
        [1, 2, 3],
        {"msg": "no Jackson text here"},
    ],
    ids=["none", "string", "list", "no-jackson-text"],
)
def test_jackson_parser_parse_returns_empty_for_unparsable_bodies(body, make_operation, case_factory):
    assert parse_observations(JacksonParser(), body, make_operation, case_factory) == ()


@pytest.mark.parametrize(
    "array_key, item_key",
    [
        ("errors", "message"),
        ("errors", "defaultMessage"),
        ("subErrors", "message"),
        ("fieldErrors", "message"),
    ],
    ids=[
        "errors-message",
        "errors-defaultMessage",
        "subErrors-message",
        "fieldErrors-message",
    ],
)
def test_jackson_parser_walks_into_array_shape_envelopes(array_key, item_key, make_operation, case_factory):
    # Custom `@ControllerAdvice` handlers sometimes funnel Jackson parse errors
    # alongside Bean-validation results into a single `errors[]` array.
    body = {array_key: [{item_key: _JACKSON_LOCAL_DATE}]}
    obs = parse_observations(JacksonParser(), body, make_operation, case_factory)
    assert [o.payload for o in obs] == [TypeMismatchPayload(type_name="java.time.LocalDate")]


def test_jackson_parser_skips_non_dict_array_items(make_operation, case_factory):
    body = {"errors": ["string-item", 123, None, {"message": _JACKSON_LOCAL_DATE}]}
    obs = parse_observations(JacksonParser(), body, make_operation, case_factory)
    assert [o.payload for o in obs] == [TypeMismatchPayload(type_name="java.time.LocalDate")]


def test_jackson_parser_extracts_one_observation_per_carrier_key(make_operation, case_factory):
    # Different carrier keys can each carry a Jackson error — `_carrier_strings`
    # walks them in order and emits one observation per match.
    body = {
        "msg": _JACKSON_LOCAL_DATE,
        "detail": _JACKSON_UUID,
    }
    obs = parse_observations(JacksonParser(), body, make_operation, case_factory)
    assert [(o.parameter_path, o.payload) for o in obs] == [
        (("hire_date",), TypeMismatchPayload(type_name="java.time.LocalDate")),
        (("id",), TypeMismatchPayload(type_name="java.util.UUID")),
    ]


_JACKSON_OVERFLOW_INT = (
    "JSON parse error: Numeric value (-8805630315124945371) out of range of int;\n"
    "  nested exception is com.fasterxml.jackson.databind.JsonMappingException:\n"
    "  Numeric value (-8805630315124945371) out of range of int\n"
    " at [Source: (PushbackInputStream); line: 1, column: 557]\n"
    ' (through reference chain: br.com.codenation.hospital.dto.HospitalDTO["availableBeds"])'
)


def test_jackson_numeric_overflow_int(make_operation, case_factory):
    obs = parse_observations(JacksonParser(), {"msg": _JACKSON_OVERFLOW_INT}, make_operation, case_factory)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (
            ("availableBeds",),
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=-2_147_483_648.0, direction=BoundDirection.MIN, exclusive=False),
        ),
        (
            ("availableBeds",),
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=2_147_483_647.0, direction=BoundDirection.MAX, exclusive=False),
        ),
    ]


def test_jackson_numeric_overflow_long(make_operation, case_factory):
    message = (
        "JSON parse error: Numeric value (99999999999999999999) out of range of long "
        'through reference chain: Order["quantity"]'
    )
    obs = parse_observations(JacksonParser(), {"msg": message}, make_operation, case_factory)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (
            ("quantity",),
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=-9_223_372_036_854_775_808.0, direction=BoundDirection.MIN, exclusive=False),
        ),
        (
            ("quantity",),
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=9_223_372_036_854_775_807.0, direction=BoundDirection.MAX, exclusive=False),
        ),
    ]


@pytest.mark.parametrize(
    ("response_message", "request_body", "expected_path"),
    [
        (
            'JSON parse error: Cannot deserialize value of type `java.time.LocalDate` from String "dd-MM-yyyy"',
            {"employeeId": 7, "commitDate": "dd-MM-yyyy", "comment": "team standup"},
            ("commitDate",),
        ),
        (
            'Cannot deserialize value of type `java.time.LocalDate` from String "dd-MM-yyyy" '
            '(through reference chain: com.example.Employee["hireDate"])',
            {"hireDate": "dd-MM-yyyy"},
            ("hireDate",),
        ),
        # `instance of X out of <token>` carries no captured value; with no reference chain,
        # the helper has nothing to walk against and the message is dropped.
        (
            "Cannot deserialize instance of `java.util.Map` out of START_ARRAY token",
            {"name": "alice"},
            None,
        ),
    ],
    ids=["recovered-via-request-walk", "reference-chain-wins", "non-string-source-dropped"],
)
def test_jackson_field_attribution(make_operation, case_factory, response_message, request_body, expected_path):
    operation = make_operation(method="post", path="/api/records")
    case = case_factory(operation=operation, body=request_body, method="POST")
    body = {"message": response_message}
    observations = JacksonParser().parse(operation=operation, body=body, case=case)
    if expected_path is None:
        assert observations == ()
    else:
        assert observations == (
            Observation(
                operation_label=operation.label,
                location=ParameterLocation.BODY,
                parameter_path=expected_path,
                kind=ObservationKind.TYPE_MISMATCH,
                raw_message=response_message,
                payload=TypeMismatchPayload(type_name="java.time.LocalDate"),
            ),
        )
