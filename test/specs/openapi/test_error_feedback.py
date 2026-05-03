from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

import pytest
from pydantic import AnyUrl, BaseModel, Field, ValidationError

import schemathesis
from schemathesis.core.error_feedback import (
    MAX_ENTRIES_PER_BUCKET,
    BoundDirection,
    EnumPayload,
    ErrorFeedbackStore,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    ObservationPayload,
    PatternPayload,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.error_feedback.collector import record_response
from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.parsers.drf import DRFParser, _classify, _location_for_method, _walk
from schemathesis.core.error_feedback.parsers.jackson import JacksonParser
from schemathesis.core.error_feedback.parsers.pydantic import PydanticParser, _parse_expected
from schemathesis.core.error_feedback.parsers.spring import SpringParser
from schemathesis.core.error_feedback.pipeline import FeedbackPipeline, _reset_pipeline_for_tests
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import Response
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import (
    CaseMetadata,
    FuzzingPhaseData,
    GenerationInfo,
    PhaseInfo,
    TestPhase,
)
from schemathesis.specs.openapi.error_feedback import (
    EnumAdjustment,
    FormatAdjustment,
    NumericBoundAdjustment,
    PatternAdjustment,
    RequiredFieldAdjustment,
    SizeBoundAdjustment,
    TypeMismatchAdjustment,
    apply_adjustments,
)
from schemathesis.specs.openapi.patterns import normalize_regex


@pytest.fixture
def make_operation(ctx):
    """Build a real `APIOperation` with the given method/path. Returns a callable."""

    def factory(method: str = "post", path: str = "/api/users"):
        schema_dict = ctx.openapi.build_schema({path: {method: {"responses": {"200": {"description": "OK"}}}}})
        sthesis_schema = schemathesis.openapi.from_dict(schema_dict)
        return sthesis_schema[path][method.upper()]

    return factory


def _obs(field: str, *, op: str = "POST /api/users") -> Observation:
    return Observation(
        operation_label=op,
        location=ParameterLocation.BODY,
        parameter_path=(field,),
        kind=ObservationKind.MUST_NOT_BE_BLANK,
        raw_message=f"{field} - must not be blank",
    )


def test_store_dedups_identical_observations_into_one_entry():
    store = ErrorFeedbackStore()
    for _ in range(1553):
        store.record(_obs("email"))
    assert len(store.observations(operation_label="POST /api/users", location=ParameterLocation.BODY)) == 1


def test_store_evicts_lowest_count_entry_when_bucket_full():
    store = ErrorFeedbackStore()
    for i in range(MAX_ENTRIES_PER_BUCKET):
        store.record(_obs(f"f{i}"))
        store.record(_obs(f"f{i}"))
    for _ in range(50):
        store.record(_obs("f0"))
    store.record(_obs("new_field"))
    store.record(_obs("new_field"))

    paths = {
        o.parameter_path for o in store.observations(operation_label="POST /api/users", location=ParameterLocation.BODY)
    }
    assert ("f0",) in paths
    assert ("new_field",) in paths
    assert len(paths) == MAX_ENTRIES_PER_BUCKET


def test_store_observations_filters_by_min_count():
    store = ErrorFeedbackStore()
    store.record(_obs("email"))
    assert store.observations(operation_label="POST /api/users", location=ParameterLocation.BODY) == ()

    store.record(_obs("email"))
    out = store.observations(operation_label="POST /api/users", location=ParameterLocation.BODY)
    assert len(out) == 1
    assert out[0].parameter_path == ("email",)


def test_store_checkpoint_bumps_generation_and_keeps_observations():
    store = ErrorFeedbackStore()
    store.record(_obs("email"))
    store.record(_obs("email"))
    assert store.generation == 0

    store.checkpoint()
    assert store.generation == 1
    assert len(store.observations(operation_label="POST /api/users", location=ParameterLocation.BODY)) == 1

    store.checkpoint()
    assert store.generation == 2


def test_store_record_does_not_bump_generation():
    store = ErrorFeedbackStore()
    store.record(_obs("email"))
    store.record(_obs("email"))
    store.record(_obs("email"))
    assert store.generation == 0


def test_store_keeps_min_and_max_numeric_bounds_for_same_path():
    store = ErrorFeedbackStore()
    min_payload = NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True)
    max_payload = NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=False)
    for payload in (min_payload, min_payload, max_payload, max_payload):
        store.record(
            Observation(
                operation_label="POST /api/users",
                location=ParameterLocation.BODY,
                parameter_path=("qty",),
                kind=ObservationKind.NUMERIC_BOUND,
                raw_message="",
                payload=payload,
            )
        )
    out = store.observations(operation_label="POST /api/users", location=ParameterLocation.BODY)
    assert sorted((o.payload.direction, o.payload.bound) for o in out) == [
        (BoundDirection.MAX, 100.0),
        (BoundDirection.MIN, 0.0),
    ]


def test_store_concurrent_inserts_are_safe():
    store = ErrorFeedbackStore()

    def worker(field_index: int) -> None:
        ob = _obs(f"f{field_index}")
        for _ in range(100):
            store.record(ob)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(8)))

    out = store.observations(operation_label="POST /api/users", location=ParameterLocation.BODY)
    paths = sorted(o.parameter_path for o in out)
    assert paths == sorted((f"f{i}",) for i in range(8))


def test_parsers_registry_returns_a_list():
    assert isinstance(PARSERS.get_all(), list)


def test_parsers_registry_contains_spring_parser():
    assert SpringParser in PARSERS.get_all()


def test_parsers_registry_contains_pydantic_parser():
    assert PydanticParser in PARSERS.get_all()


@pytest.mark.parametrize(
    "body",
    [
        {"detail": [{"type": "missing", "loc": ["body", "x"], "msg": "Field required"}]},
        {"detail": [{"type": "string_too_short", "loc": ["body", "x"], "msg": "...", "ctx": {"min_length": 3}}]},
    ],
    ids=["missing", "string-too-short"],
)
def test_pydantic_parser_can_parse_recognises_envelope(body):
    assert PydanticParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        {"detail": "RFC 7807 prose, not a list"},
        {"detail": []},
        {"detail": [123, "not a dict"]},
        {"detail": [{"loc": ["body", "x"]}]},  # missing `type`
        {"detail": [{"type": "missing"}]},  # missing `loc`
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "detail-string",
        "detail-empty-list",
        "detail-non-dict-items",
        "detail-missing-type",
        "detail-missing-loc",
    ],
)
def test_pydantic_parser_can_parse_rejects_non_pydantic_bodies(body):
    assert PydanticParser().can_parse(body=body) is False


SPRING_MESSAGES = (
    b'{"error":"Bad Request","status":400,'
    b'"messages":["zipcode - must not be blank","city - must not be blank"],'
    b'"timestamp":"2026-04-30T18:08:24Z"}'
)
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
def test_spring_parser_extracts_observations(body, expected_paths, make_operation):
    obs = SpringParser().parse(
        operation=make_operation(),
        body=json.loads(body),
    )
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
def test_spring_parser_recognizes_non_blank_message_variants(message, make_operation):
    body = {"subErrors": [{"field": "x", "message": message}]}
    obs = SpringParser().parse(operation=make_operation(), body=body)
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
def test_spring_parser_skips_unrecognized_messages(message, make_operation):
    body = {"subErrors": [{"field": "x", "message": message}]}
    assert SpringParser().parse(operation=make_operation(), body=body) == ()


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
def test_spring_parser_recognizes_size_bound_message_variants(message, expected_min, expected_max, make_operation):
    body = {"subErrors": [{"field": "username", "message": message}]}
    obs = SpringParser().parse(operation=make_operation(), body=body)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=expected_min, max=expected_max)),
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
def test_spring_parser_recognizes_format_message_variants(message, expected_name, make_operation):
    body = {"subErrors": [{"field": "contact", "message": message}]}
    obs = SpringParser().parse(operation=make_operation(), body=body)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (("contact",), ObservationKind.FORMAT, FormatPayload(name=expected_name)),
    ]


def test_spring_parser_uuid_takes_precedence_over_uri_when_both_match(make_operation):
    # Defensive: a contrived "must be a valid URI UUID" string would match both
    # the URI and UUID regexes. The classifier checks UUID first so the more
    # specific format wins.
    body = {"subErrors": [{"field": "x", "message": "must be a valid UUID"}]}
    obs = SpringParser().parse(operation=make_operation(), body=body)
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
    message, expected_bound, expected_direction, expected_exclusive, make_operation
):
    body = {"subErrors": [{"field": "score", "message": message}]}
    obs = SpringParser().parse(operation=make_operation(), body=body)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (
            ("score",),
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=expected_bound, direction=expected_direction, exclusive=expected_exclusive),
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
def test_spring_parser_recognizes_pattern_message_variants(message, expected_regex, make_operation):
    body = {"subErrors": [{"field": "code", "message": message}]}
    obs = SpringParser().parse(operation=make_operation(), body=body)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (("code",), ObservationKind.PATTERN, PatternPayload(regex=expected_regex)),
    ]


# Verbatim from gestao-hospital 4.18-rc.1 traffic.
_SPRING_MISSING_PARAMETER_BODY = {
    "timestamp": "2026-05-01T01:00:40.560+0000",
    "status": 400,
    "error": "Bad Request",
    "message": "Required Double parameter 'lat' is not present",
    "path": "/v1/hospitais/maisProximo",
}
# Verbatim from pet-clinic 4.18-rc.1 traffic — Spring 6 / RFC 7807 Problem Detail.
_SPRING_TYPE_COERCION_PETCLINIC = {
    "type": "http://localhost:8080/petclinic/api/owners/null%2Cnull/pets",
    "title": "MethodArgumentTypeMismatchException",
    "status": 500,
    "detail": (
        "Method parameter 'ownerId': Failed to convert value of type "
        "'java.lang.String' to required type 'java.lang.Integer'; "
        'For input string: "null"'
    ),
}


def test_spring_parser_recognizes_missing_request_parameter(make_operation):
    obs = SpringParser().parse(
        operation=make_operation(method="get", path="/v1/hospitais/maisProximo"), body=_SPRING_MISSING_PARAMETER_BODY
    )
    assert [(o.parameter_path, o.kind, o.location) for o in obs] == [
        (("lat",), ObservationKind.MUST_NOT_BE_BLANK, ParameterLocation.QUERY),
    ]


def test_spring_parser_can_parse_recognizes_missing_parameter_envelope():
    assert SpringParser().can_parse(body=_SPRING_MISSING_PARAMETER_BODY) is True


def test_spring_parser_recognizes_method_argument_type_mismatch(make_operation):
    # Field captured from `Method parameter 'ownerId':` prefix; emitted on both
    # PATH and QUERY because the message doesn't pin the binding.
    obs = SpringParser().parse(
        operation=make_operation(method="get", path="/api/owners/{ownerId}/pets"), body=_SPRING_TYPE_COERCION_PETCLINIC
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
    assert SpringParser().can_parse(body=_SPRING_TYPE_COERCION_PETCLINIC) is True


def test_spring_parser_skips_type_coercion_without_method_parameter_prefix(make_operation):
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
        "path": "/v1/hospitais/maisProximo",
    }
    assert (
        SpringParser().parse(operation=make_operation(method="get", path="/v1/hospitais/maisProximo"), body=body) == ()
    )


def test_parsers_registry_contains_jackson_parser():
    assert JacksonParser in PARSERS.get_all()


def test_parsers_registry_contains_drf_parser():
    assert DRFParser in PARSERS.get_all()


@pytest.mark.parametrize(
    "body",
    [
        {"name": ["This field is required."]},
        {"address": {"zipcode": ["This field is required."]}},
        {"non_field_errors": ["Passwords do not match."]},
        {"emails": [{}, {}, {"value": ["bad"]}]},
        {"tags": {"0": ["bad"]}},
    ],
    ids=[
        "flat-list-of-strings",
        "nested-dict",
        "non-field-errors-only",
        "list-of-dicts",
        "integer-keyed-dict",
    ],
)
def test_drf_parser_can_parse_recognises_envelope(body):
    assert DRFParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        ["top-level-list"],
        {"detail": "single-message"},
        {"x": 5},
        {"x": True},
        123,
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "top-level-list",
        "detail-only",
        "scalar-int-leaf",
        "scalar-bool-leaf",
        "non-dict-non-list",
    ],
)
def test_drf_parser_can_parse_rejects_non_drf_bodies(body):
    assert DRFParser().can_parse(body=body) is False


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"name": ["msg"]}, [(("name",), "msg")]),
        ({"a": ["m1", "m2"]}, [(("a",), "m1"), (("a",), "m2")]),
        ({"address": {"zipcode": ["msg"]}}, [(("address", "zipcode"), "msg")]),
        ({"a": {"b": {"c": ["msg"]}}}, [(("a", "b", "c"), "msg")]),
        (
            {"emails": [{}, {}, {"value": ["msg"]}]},
            [(("emails", 2, "value"), "msg")],
        ),
        (
            {"items": [None, {"x": ["m1"]}, None]},
            [(("items", 1, "x"), "m1")],
        ),
    ],
    ids=[
        "flat-single",
        "flat-multiple",
        "nested-one-level",
        "nested-three-levels",
        "list-of-dicts-with-empty-placeholders",
        "list-of-dicts-with-none-placeholders",
    ],
)
def test_drf_parser_walks_basic_shapes(body, expected):
    assert list(_walk(body)) == expected


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"non_field_errors": ["x"]}, []),
        ({"a": {"non_field_errors": ["x"]}}, []),
        ({"a": ["m"], "non_field_errors": ["x"]}, [(("a",), "m")]),
        ({"tags": {"0": ["m"]}}, [(("tags", 0), "m")]),
        ({"tags": {"0": ["m0"], "2": ["m2"]}}, [(("tags", 0), "m0"), (("tags", 2), "m2")]),
        ({"x": {"0": ["m"], "y": ["n"]}}, [(("x", 0), "m"), (("x", "y"), "n")]),
        ({"x": []}, []),
        ({"x": [""]}, []),
        ({"x": [None]}, []),
        ({"x": 42}, []),
    ],
    ids=[
        "non-field-errors-top-level",
        "non-field-errors-nested",
        "non-field-errors-mixed-with-real-fields",
        "integer-keyed-dict",
        "integer-keyed-dict-multiple",
        "mixed-key-dict",
        "empty-list-leaf",
        "empty-string-leaf",
        "none-only-list",
        "scalar-leaf",
    ],
)
def test_drf_parser_walks_edge_cases(body, expected):
    assert list(_walk(body)) == expected


def test_drf_parser_walks_skips_non_string_dict_keys():
    # JSON deserialisation usually gives all-string keys, but custom decoders
    # could yield int keys; the walker treats them as garbage and continues.
    assert list(_walk({1: ["msg"], "name": ["x"]})) == [(("name",), "x")]


def test_drf_parser_can_parse_bails_on_pathological_depth():
    body: dict = {"x": []}
    nested: list = body["x"]
    for _ in range(20):
        wrapper: list = []
        nested.append({"y": wrapper})
        nested = wrapper
    # The deepest leaf is well past the depth cap; can_parse must still return
    # cleanly without scanning forever.
    assert DRFParser().can_parse(body=body) is False


@pytest.mark.parametrize(
    "method, expected",
    [
        ("POST", ParameterLocation.BODY),
        ("PUT", ParameterLocation.BODY),
        ("PATCH", ParameterLocation.BODY),
        ("GET", ParameterLocation.QUERY),
        ("DELETE", ParameterLocation.QUERY),
        ("HEAD", ParameterLocation.QUERY),
        ("post", ParameterLocation.BODY),
        ("OPTIONS", ParameterLocation.BODY),
        ("WEIRDVERB", ParameterLocation.BODY),
    ],
    ids=[
        "post",
        "put",
        "patch",
        "get",
        "delete",
        "head",
        "lowercase-method",
        "options-defaults-to-body",
        "unknown-method-defaults-to-body",
    ],
)
def test_drf_parser_location_for_method(method, expected):
    assert _location_for_method(method) is expected


@pytest.mark.parametrize(
    "message, kind, payload",
    [
        ("This field is required.", ObservationKind.MUST_NOT_BE_BLANK, None),
        ("This field may not be blank.", ObservationKind.MUST_NOT_BE_BLANK, None),
        ("This field may not be null.", ObservationKind.MUST_NOT_BE_BLANK, None),
        ("Enter a valid email address.", ObservationKind.FORMAT, FormatPayload(name="email")),
        ("Enter a valid URL.", ObservationKind.FORMAT, FormatPayload(name="uri")),
        ("Must be a valid UUID.", ObservationKind.FORMAT, FormatPayload(name="uuid")),
        (
            "A valid integer is required.",
            ObservationKind.TYPE_MISMATCH,
            TypeMismatchPayload(type_name="integer"),
        ),
        (
            "A valid number is required.",
            ObservationKind.TYPE_MISMATCH,
            TypeMismatchPayload(type_name="number"),
        ),
        (
            "Must be a valid boolean.",
            ObservationKind.TYPE_MISMATCH,
            TypeMismatchPayload(type_name="boolean"),
        ),
    ],
    ids=[
        "required",
        "blank",
        "null",
        "email",
        "url",
        "uuid",
        "type-integer",
        "type-number",
        "type-boolean",
    ],
)
def test_drf_parser_classifier_literals(message, kind, payload):
    assert _classify(message) == (kind, payload)


def test_drf_parser_classifier_unrecognised_yields_none():
    assert _classify("Some custom validate_<field> message we cannot map.") is None


@pytest.mark.parametrize(
    "message, kind, payload",
    [
        (
            "Date has wrong format. Use one of these formats instead: YYYY-MM-DD.",
            ObservationKind.FORMAT,
            FormatPayload(name="date"),
        ),
        (
            "Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].",
            ObservationKind.FORMAT,
            FormatPayload(name="date-time"),
        ),
        (
            "Time has wrong format. Use one of these formats instead: hh:mm[:ss[.uuuuuu]].",
            ObservationKind.FORMAT,
            FormatPayload(name="time"),
        ),
        (
            'Expected a list of items but got type "str".',
            ObservationKind.TYPE_MISMATCH,
            TypeMismatchPayload(type_name="array"),
        ),
        (
            'Expected a dictionary of items but got type "list".',
            ObservationKind.TYPE_MISMATCH,
            TypeMismatchPayload(type_name="object"),
        ),
    ],
    ids=["date", "datetime", "time", "array", "object"],
)
def test_drf_parser_classifier_prefixes(message, kind, payload):
    assert _classify(message) == (kind, payload)


@pytest.mark.parametrize(
    "message, expected_min, expected_max",
    [
        ("Ensure this field has at least 3 characters.", 3, None),
        ("Ensure this value has at least 5 characters.", 5, None),
        ("Ensure this value has at least 3 characters (it has 2).", 3, None),
        ("Ensure this field has no more than 64 characters.", None, 64),
        ("Ensure this field has at most 20 characters.", None, 20),
        ("Ensure this value has at most 20 characters (it has 25).", None, 20),
    ],
    ids=[
        "drf-min",
        "django-bridge-min",
        "django-bridge-min-with-suffix",
        "drf-max-no-more-than",
        "drf-max-at-most",
        "django-bridge-max-with-suffix",
    ],
)
def test_drf_parser_classifier_string_size(message, expected_min, expected_max):
    assert _classify(message) == (
        ObservationKind.SIZE_BOUND,
        SizeBoundPayload(min=expected_min, max=expected_max),
    )


@pytest.mark.parametrize(
    "message, expected_min, expected_max",
    [
        ("Ensure this field has at least 1 elements.", 1, None),
        ("Ensure this field has at least 2 elements.", 2, None),
        ("Ensure this field has no more than 5 elements.", None, 5),
        ("Ensure this field has no more than 1 element.", None, 1),
    ],
    ids=["min-int", "min-int-2", "max-int", "max-int-singular-element"],
)
def test_drf_parser_classifier_array_size(message, expected_min, expected_max):
    assert _classify(message) == (
        ObservationKind.SIZE_BOUND,
        SizeBoundPayload(min=expected_min, max=expected_max),
    )


@pytest.mark.parametrize(
    "message, bound, direction, exclusive",
    [
        ("Ensure this value is greater than or equal to 0.", 0.0, BoundDirection.MIN, False),
        ("Ensure this value is greater than 0.5.", 0.5, BoundDirection.MIN, True),
        ("Ensure this value is greater than -50.", -50.0, BoundDirection.MIN, True),
        ("Ensure this value is less than or equal to 100.", 100.0, BoundDirection.MAX, False),
        ("Ensure this value is less than 99.99.", 99.99, BoundDirection.MAX, True),
    ],
    ids=[
        "min-inclusive-int",
        "min-exclusive-decimal",
        "min-negative",
        "max-inclusive-int",
        "max-exclusive-decimal",
    ],
)
def test_drf_parser_classifier_numeric_bound(message, bound, direction, exclusive):
    assert _classify(message) == (
        ObservationKind.NUMERIC_BOUND,
        NumericBoundPayload(bound=bound, direction=direction, exclusive=exclusive),
    )


def _drf_obs(
    *,
    op: str,
    location: ParameterLocation,
    path: tuple[str | int, ...],
    kind: ObservationKind,
    raw_message: str,
    payload: ObservationPayload = None,
) -> Observation:
    return Observation(
        operation_label=op,
        location=location,
        parameter_path=path,
        kind=kind,
        raw_message=raw_message,
        payload=payload,
    )


def test_drf_parser_parse_flat_field(make_operation):
    body = {"name": ["This field is required."]}
    assert DRFParser().parse(operation=make_operation(), body=body) == (
        _drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("name",),
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="This field is required.",
        ),
    )


def test_drf_parser_parse_nested_with_size_bound(make_operation):
    body = {"address": {"zipcode": ["Ensure this field has at least 5 characters."]}}
    assert DRFParser().parse(operation=make_operation(), body=body) == (
        _drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("address", "zipcode"),
            kind=ObservationKind.SIZE_BOUND,
            raw_message="Ensure this field has at least 5 characters.",
            payload=SizeBoundPayload(min=5, max=None),
        ),
    )


def test_drf_parser_parse_get_request_yields_query_location(make_operation):
    body = {"limit": ["A valid integer is required."]}
    assert DRFParser().parse(operation=make_operation(method="get", path="/api/users"), body=body) == (
        _drf_obs(
            op="GET /api/users",
            location=ParameterLocation.QUERY,
            path=("limit",),
            kind=ObservationKind.TYPE_MISMATCH,
            raw_message="A valid integer is required.",
            payload=TypeMismatchPayload(type_name="integer"),
        ),
    )


def test_drf_parser_parse_skips_unrecognised_messages(make_operation):
    body = {"name": ["Custom validate_name message."]}
    assert DRFParser().parse(operation=make_operation(), body=body) == ()


def test_drf_parser_parse_non_field_errors_only_yields_empty(make_operation):
    body = {"non_field_errors": ["Passwords do not match."]}
    assert DRFParser().parse(operation=make_operation(), body=body) == ()


def test_drf_parser_parse_list_with_failing_index(make_operation):
    body = {"emails": [{}, {}, {"value": ["Enter a valid email address."]}]}
    assert DRFParser().parse(operation=make_operation(), body=body) == (
        _drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("emails", 2, "value"),
            kind=ObservationKind.FORMAT,
            raw_message="Enter a valid email address.",
            payload=FormatPayload(name="email"),
        ),
    )


# Verbatim from /tmp/drf-corpus capture — CharField required + min_length=3 (multi-error)
_DRF_MULTI_ERROR_BODY = {"username": ["This field may not be blank.", "Ensure this field has at least 3 characters."]}

# Verbatim from /tmp/drf-corpus capture — Django MaxLengthValidator bridge
_DRF_DJANGO_BRIDGE_BODY = {"email": ["Ensure this value has at most 20 characters (it has 25)."]}

# Verbatim from /tmp/drf-corpus capture — ListSerializer with bad item at index 2
_DRF_LIST_INDEX_BODY = {"emails": [{}, {}, {"value": ["Enter a valid email address."]}]}

# Verbatim from /tmp/drf-corpus capture — nested Serializer
_DRF_NESTED_BODY = {"address": {"zipcode": ["This field is required."], "country": ["Enter a valid value."]}}

# Verbatim from /tmp/drf-corpus capture — IntegerField with min_value=0
_DRF_INTEGER_BODY = {"age": ["Ensure this value is greater than or equal to 0."]}


def test_drf_parser_end_to_end_multi_error_per_field(make_operation):
    obs = DRFParser().parse(operation=make_operation(), body=_DRF_MULTI_ERROR_BODY)
    assert obs == (
        _drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("username",),
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="This field may not be blank.",
        ),
        _drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("username",),
            kind=ObservationKind.SIZE_BOUND,
            raw_message="Ensure this field has at least 3 characters.",
            payload=SizeBoundPayload(min=3, max=None),
        ),
    )


def test_drf_parser_end_to_end_django_bridge_max_length(make_operation):
    obs = DRFParser().parse(operation=make_operation(), body=_DRF_DJANGO_BRIDGE_BODY)
    assert obs == (
        _drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("email",),
            kind=ObservationKind.SIZE_BOUND,
            raw_message="Ensure this value has at most 20 characters (it has 25).",
            payload=SizeBoundPayload(min=None, max=20),
        ),
    )


def test_drf_parser_end_to_end_list_index_attribution(make_operation):
    obs = DRFParser().parse(operation=make_operation(), body=_DRF_LIST_INDEX_BODY)
    assert obs == (
        _drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("emails", 2, "value"),
            kind=ObservationKind.FORMAT,
            raw_message="Enter a valid email address.",
            payload=FormatPayload(name="email"),
        ),
    )


def test_drf_parser_end_to_end_nested_serializer(make_operation):
    obs = DRFParser().parse(operation=make_operation(), body=_DRF_NESTED_BODY)
    # Only the recognised "This field is required." emits — "Enter a valid value." is unmapped.
    assert obs == (
        _drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("address", "zipcode"),
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="This field is required.",
        ),
    )


def test_drf_parser_end_to_end_integer_min_value(make_operation):
    obs = DRFParser().parse(operation=make_operation(), body=_DRF_INTEGER_BODY)
    assert obs == (
        _drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("age",),
            kind=ObservationKind.NUMERIC_BOUND,
            raw_message="Ensure this value is greater than or equal to 0.",
            payload=NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
        ),
    )


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
# replaces the String quoting. Verbatim from gestao-hospital 4.18-rc.1 traffic.
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
def test_jackson_parser_extracts_observations(carrier_key, message, expected_path, expected_type, make_operation):
    body = {carrier_key: message}
    obs = JacksonParser().parse(operation=make_operation(), body=body)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (expected_path, ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name=expected_type)),
    ]


def test_jackson_parser_emits_both_type_and_enum_for_enum_message(make_operation):
    body = {"msg": _JACKSON_ENUM_USERTYPE}
    obs = JacksonParser().parse(operation=make_operation(), body=body)
    assert [(o.kind, o.payload) for o in obs] == [
        (
            ObservationKind.TYPE_MISMATCH,
            TypeMismatchPayload(type_name="com.example.demo.auth.model.enums.UserType"),
        ),
        (ObservationKind.ENUM, EnumPayload(values=("USER", "ADMIN"))),
    ]
    assert all(o.parameter_path == ("userType",) for o in obs)


def test_jackson_parser_emits_enum_only_when_type_clause_is_absent(make_operation):
    body = {"msg": _JACKSON_ENUM_BARE}
    obs = JacksonParser().parse(operation=make_operation(), body=body)
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
def test_jackson_parser_enum_value_list_variants(values_blob, expected, make_operation):
    message = (
        f'Cannot deserialize value of type `Status` from String "x": '
        f"not one of the values accepted for Enum class: [{values_blob}] "
        f'through reference chain: Order["status"]'
    )
    obs = JacksonParser().parse(operation=make_operation(), body={"msg": message})
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


def test_jackson_parser_skips_message_without_reference_chain(make_operation):
    # Field attribution requires the chain — a bare type message can't be routed.
    body = {"msg": 'Cannot deserialize value of type `java.time.LocalDate` from String "x"'}
    assert JacksonParser().can_parse(body=body) is True
    assert JacksonParser().parse(operation=make_operation(), body=body) == ()


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
def test_jackson_parser_parse_returns_empty_for_unparsable_bodies(body, make_operation):
    assert JacksonParser().parse(operation=make_operation(), body=body) == ()


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
def test_jackson_parser_walks_into_array_shape_envelopes(array_key, item_key, make_operation):
    # Custom `@ControllerAdvice` handlers sometimes funnel Jackson parse errors
    # alongside Bean-validation results into a single `errors[]` array.
    body = {array_key: [{item_key: _JACKSON_LOCAL_DATE}]}
    obs = JacksonParser().parse(operation=make_operation(), body=body)
    assert [o.payload for o in obs] == [TypeMismatchPayload(type_name="java.time.LocalDate")]


def test_jackson_parser_skips_non_dict_array_items(make_operation):
    body = {"errors": ["string-item", 123, None, {"message": _JACKSON_LOCAL_DATE}]}
    obs = JacksonParser().parse(operation=make_operation(), body=body)
    assert [o.payload for o in obs] == [TypeMismatchPayload(type_name="java.time.LocalDate")]


def test_jackson_parser_extracts_one_observation_per_carrier_key(make_operation):
    # Different carrier keys can each carry a Jackson error — `_carrier_strings`
    # walks them in order and emits one observation per match.
    body = {
        "msg": _JACKSON_LOCAL_DATE,
        "detail": _JACKSON_UUID,
    }
    obs = JacksonParser().parse(operation=make_operation(), body=body)
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


def test_jackson_numeric_overflow_int(make_operation):
    obs = JacksonParser().parse(operation=make_operation(), body={"msg": _JACKSON_OVERFLOW_INT})
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


def test_jackson_numeric_overflow_long(make_operation):
    message = (
        "JSON parse error: Numeric value (99999999999999999999) out of range of long "
        'through reference chain: Order["quantity"]'
    )
    obs = JacksonParser().parse(operation=make_operation(), body={"msg": message})
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
def test_spring_parser_extracts_multiple_entries_per_shape(body, expected_paths, make_operation):
    obs = SpringParser().parse(operation=make_operation(), body=json.loads(body))
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
def test_spring_parser_splits_dotted_paths_into_tuples(body, expected_path, make_operation):
    obs = SpringParser().parse(operation=make_operation(), body=body)
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
def test_spring_parser_errors_field_and_message_priority(entry, expected, make_operation):
    body = {"errors": [entry]}
    obs = SpringParser().parse(operation=make_operation(), body=body)
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
def test_spring_parser_field_errors_locator_and_message_priority(entry, expected, make_operation):
    body = {"fieldErrors": [entry]}
    obs = SpringParser().parse(operation=make_operation(), body=body)
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
def test_spring_parser_skips_invalid_or_unrecognized_entries(body, make_operation):
    assert SpringParser().parse(operation=make_operation(), body=body) == ()


def test_spring_parser_mixes_valid_and_invalid_messages(make_operation):
    body = {
        "messages": [
            "valid - must not be blank",
            123,
            "no_dash_here",
            "another - is required",
            "x - just some prose",
        ]
    }
    obs = SpringParser().parse(operation=make_operation(), body=body)
    assert [o.parameter_path for o in obs] == [("valid",), ("another",)]


@pytest.mark.parametrize(
    "body",
    [[1, 2, 3], "not a dict", None, 42, 1.5, True],
    ids=["list", "string", "none", "int", "float", "bool"],
)
def test_spring_parser_returns_empty_for_non_dict_body(body, make_operation):
    assert SpringParser().parse(operation=make_operation(), body=body) == ()


def test_pipeline_dispatches_to_spring_parser_for_spring_shape(case_factory, response_factory):
    case = case_factory()
    response = Response.from_any(
        response_factory.requests(
            content=SPRING_MESSAGES,
            content_type="application/json",
            status_code=400,
        )
    )
    obs = FeedbackPipeline.from_registry().parse(
        operation=case.operation,
        case=case,
        response=response,
    )
    assert [o.parameter_path for o in obs] == [("zipcode",), ("city",)]


def test_pipeline_skips_responses_with_no_known_deserializer(case_factory, response_factory):
    case = case_factory()
    response = Response.from_any(
        response_factory.requests(
            content=b"\x00\x01\x02",
            content_type="application/octet-stream",
            status_code=400,
        )
    )
    assert (
        FeedbackPipeline.from_registry().parse(
            operation=case.operation,
            case=case,
            response=response,
        )
        == ()
    )


def test_pipeline_returns_empty_when_no_parser_matches(case_factory, response_factory):
    case = case_factory()
    response = Response.from_any(
        response_factory.requests(
            content=b'{"random": "shape"}',
            content_type="application/json",
            status_code=400,
        )
    )
    assert (
        FeedbackPipeline.from_registry().parse(
            operation=case.operation,
            case=case,
            response=response,
        )
        == ()
    )


def _meta_for(mode: GenerationMode) -> CaseMetadata:
    return CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=mode),
        components={},
        phase=PhaseInfo(
            name=TestPhase.FUZZING,
            data=FuzzingPhaseData(
                description="",
                parameter=None,
                parameter_location=None,
                location=None,
            ),
        ),
    )


def _drive_collector(
    *,
    case_factory,
    response_factory,
    status_code: int = 400,
    body: bytes = SPRING_MESSAGES,
    mode: GenerationMode = GenerationMode.POSITIVE,
    times: int = 1,
):
    _reset_pipeline_for_tests()
    case = case_factory(_meta=_meta_for(mode))
    response = Response.from_any(
        response_factory.requests(
            content=body,
            content_type="application/json",
            status_code=status_code,
        )
    )
    store = ErrorFeedbackStore()
    for _ in range(times):
        record_response(
            store=store,
            operation=case.operation,
            case=case,
            response=response,
        )
    return store, case


def test_collector_records_observations_after_two_400_responses(case_factory, response_factory):
    store, case = _drive_collector(
        case_factory=case_factory,
        response_factory=response_factory,
        status_code=400,
        times=2,
    )
    out = store.observations(operation_label=case.operation.label, location=ParameterLocation.BODY)
    assert sorted(o.parameter_path for o in out) == [("city",), ("zipcode",)]


def test_collector_observations_below_min_observations_are_filtered(case_factory, response_factory):
    store, case = _drive_collector(
        case_factory=case_factory,
        response_factory=response_factory,
        status_code=400,
        times=1,
    )
    out = store.observations(operation_label=case.operation.label, location=ParameterLocation.BODY)
    assert out == ()


@pytest.mark.parametrize("status_code", [200, 401, 403, 500, 503])
def test_collector_skips_non_4xx_and_auth_failures(status_code, case_factory, response_factory):
    store, case = _drive_collector(
        case_factory=case_factory,
        response_factory=response_factory,
        status_code=status_code,
        times=2,
    )
    out = store.observations(
        operation_label=case.operation.label,
        location=ParameterLocation.BODY,
        min_count=1,
    )
    assert out == ()


def test_collector_skips_negative_mode_cases(case_factory, response_factory):
    store, case = _drive_collector(
        case_factory=case_factory,
        response_factory=response_factory,
        status_code=400,
        times=2,
        mode=GenerationMode.NEGATIVE,
    )
    out = store.observations(
        operation_label=case.operation.label,
        location=ParameterLocation.BODY,
        min_count=1,
    )
    assert out == ()


def _build_observations(*paths: tuple[str | int, ...]) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=p,
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="must not be blank",
        )
        for p in paths
    )


@pytest.mark.parametrize(
    "input_schema, paths, expected",
    [
        (
            {"type": "object", "properties": {"email": {"type": "string"}}, "required": []},
            [("email",)],
            {
                "type": "object",
                "properties": {"email": {"type": "string", "minLength": 1}},
                "required": ["email"],
            },
        ),
        (
            {"type": "object", "properties": {}, "required": []},
            [("email",)],
            {
                "type": "object",
                "properties": {"email": {"type": "string", "minLength": 1}},
                "required": ["email"],
            },
        ),
        (
            {"type": "object", "properties": {"email": {"type": "string", "minLength": 5}}, "required": []},
            [("email",)],
            {
                "type": "object",
                "properties": {"email": {"type": "string", "minLength": 5}},
                "required": ["email"],
            },
        ),
        (
            {"type": "object", "properties": {"age": {"type": "integer"}}, "required": []},
            [("age",)],
            {
                "type": "object",
                "properties": {"age": {"type": "integer"}},
                "required": ["age"],
            },
        ),
        (
            {
                "oneOf": [
                    {"type": "object", "properties": {}, "required": []},
                    {"type": "string"},
                ]
            },
            [("email",)],
            {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"email": {"type": "string", "minLength": 1}},
                        "required": ["email"],
                    },
                    {"type": "string"},
                ]
            },
        ),
        (True, [("email",)], True),
        (
            {
                "type": ["object", "null"],
                "properties": {"email": {"type": ["string", "null"]}},
                "required": [],
            },
            [("email",)],
            {
                "type": ["object", "null"],
                "properties": {"email": {"type": ["string", "null"], "minLength": 1}},
                "required": ["email"],
            },
        ),
        (
            {
                "type": ["object", "null"],
                "properties": {"age": {"type": ["integer", "null"]}},
                "required": [],
            },
            [("age",)],
            {
                "type": ["object", "null"],
                "properties": {"age": {"type": ["integer", "null"]}},
                "required": ["age"],
            },
        ),
        (
            {"properties": {"email": {"type": "string"}}, "required": "garbage"},
            [("email",)],
            {
                "properties": {"email": {"type": "string", "minLength": 1}},
                "required": ["email"],
            },
        ),
        (
            {"type": "object", "properties": {"email": True}, "required": []},
            [("email",)],
            {
                "type": "object",
                "properties": {"email": {"type": "string", "minLength": 1}},
                "required": ["email"],
            },
        ),
        (
            {"type": "object", "properties": {"email": False}, "required": []},
            [("email",)],
            {
                "type": "object",
                "properties": {"email": {"type": "string", "minLength": 1}},
                "required": ["email"],
            },
        ),
        (
            {"type": "string"},
            [("email",)],
            {"type": "string"},
        ),
        (
            {"oneOf": [{"type": "string"}, {"type": "number"}]},
            [("email",)],
            {"oneOf": [{"type": "string"}, {"type": "number"}]},
        ),
        (
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"email": {"type": "string"}},
                        "required": [],
                    }
                },
                "required": [],
            },
            [("contact", "email")],
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"email": {"type": "string", "minLength": 1}},
                        "required": ["email"],
                    }
                },
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {}, "required": []},
            [("address", "street")],
            {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "properties": {"street": {"type": "string", "minLength": 1}},
                        "required": ["street"],
                    }
                },
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {}, "required": []},
            [()],
            {"type": "object", "properties": {}, "required": []},
        ),
        (
            {"type": "object", "properties": {}, "required": []},
            [("foo", 0, "bar")],
            {"type": "object", "properties": {}, "required": []},
        ),
        (
            {"type": "object", "properties": {}, "required": []},
            [("foo", 0)],
            {"type": "object", "properties": {}, "required": []},
        ),
    ],
    ids=[
        "string-property-bump-minlength",
        "absent-property-inject",
        "stronger-minlength-preserved",
        "integer-property-required-only",
        "oneof-applies-to-object-branch",
        "bool-schema-passthrough",
        "type-union-with-null-tightens-string",
        "type-union-with-null-preserves-non-string",
        "non-list-required-discarded",
        "boolean-true-leaf-replaced-with-default",
        "boolean-false-leaf-replaced-with-default",
        "non-object-root-no-targets-passthrough",
        "oneof-with-no-object-branches-no-targets-passthrough",
        "nested-path-tightens-and-marks-required",
        "nested-path-creates-missing-intermediate-object",
        "empty-path-observation-skipped",
        "non-string-step-in-prefix-skipped",
        "non-string-leaf-skipped",
    ],
)
def test_required_field_adjustment_applies_correctly(input_schema, paths, expected, case_factory):
    out = RequiredFieldAdjustment().apply(
        operation=case_factory().operation,
        location=ParameterLocation.BODY,
        schema=input_schema,
        observations=_build_observations(*paths),
    )
    assert out == expected


def test_required_field_adjustment_idempotent(case_factory):
    # `apply` mutates in place, so feed it the same dict twice and check
    # the second pass doesn't drift from the first.
    schema = {"type": "object", "properties": {}, "required": []}
    obs = _build_observations(("email",))
    operation = case_factory().operation

    RequiredFieldAdjustment().apply(
        operation=operation,
        location=ParameterLocation.BODY,
        schema=schema,
        observations=obs,
    )
    snapshot = {**schema, "properties": {**schema["properties"]}, "required": [*schema["required"]]}
    RequiredFieldAdjustment().apply(
        operation=operation,
        location=ParameterLocation.BODY,
        schema=schema,
        observations=obs,
    )
    assert schema == snapshot


def test_apply_adjustments_returns_input_when_no_observations(case_factory):
    schema = {"type": "object", "properties": {}, "required": []}
    store = ErrorFeedbackStore()
    out = apply_adjustments(
        operation=case_factory().operation,
        location=ParameterLocation.BODY,
        schema=schema,
        store=store,
    )
    assert out is schema


def _build_size_bound_observations(
    *items: tuple[tuple[str | int, ...], int, int],
) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=path,
            kind=ObservationKind.SIZE_BOUND,
            raw_message=f"size must be between {min_value} and {max_value}",
            payload=SizeBoundPayload(min=min_value, max=max_value),
        )
        for path, min_value, max_value in items
    )


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"username": {"type": "string"}}, "required": []},
            [(("username",), 0, 15)],
            {
                "type": "object",
                "properties": {"username": {"type": "string", "minLength": 0, "maxLength": 15}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"tags": {"type": "array", "items": {"type": "string"}}}, "required": []},
            [(("tags",), 1, 5)],
            {
                "type": "object",
                "properties": {
                    "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
                },
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"meta": {"type": "object"}}, "required": []},
            [(("meta",), 1, 10)],
            {
                "type": "object",
                "properties": {"meta": {"type": "object", "minProperties": 1, "maxProperties": 10}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"age": {"type": "integer"}}, "required": []},
            [(("age",), 0, 100)],
            {"type": "object", "properties": {"age": {"type": "integer"}}, "required": []},
        ),
        (
            {
                "type": "object",
                "properties": {"username": {"type": "string", "minLength": 5, "maxLength": 50}},
                "required": [],
            },
            [(("username",), 0, 15)],
            {
                "type": "object",
                "properties": {"username": {"type": "string", "minLength": 5, "maxLength": 15}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"username": {"type": ["string", "null"]}}, "required": []},
            [(("username",), 0, 15)],
            {
                "type": "object",
                "properties": {"username": {"type": ["string", "null"], "minLength": 0, "maxLength": 15}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {}, "required": []},
            [(("username",), 0, 15)],
            {"type": "object", "properties": {}, "required": []},
        ),
        (
            {"type": "string"},
            [(("username",), 0, 15)],
            {"type": "string"},
        ),
        (
            {
                "oneOf": [
                    {"type": "object", "properties": {"username": {"type": "string"}}, "required": []},
                    {"type": "string"},
                ]
            },
            [(("username",), 0, 15)],
            {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"username": {"type": "string", "minLength": 0, "maxLength": 15}},
                        "required": [],
                    },
                    {"type": "string"},
                ]
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"email": {"type": "string"}},
                        "required": [],
                    }
                },
                "required": [],
            },
            [(("contact", "email"), 5, 64)],
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"email": {"type": "string", "minLength": 5, "maxLength": 64}},
                        "required": [],
                    }
                },
                "required": [],
            },
        ),
        (True, [(("username",), 0, 15)], True),
        (
            {"type": "object", "properties": {"username": {"type": "string"}}, "required": []},
            [((), 0, 15)],
            {"type": "object", "properties": {"username": {"type": "string"}}, "required": []},
        ),
        (
            {"type": "object", "properties": {"username": {"type": "string"}}, "required": []},
            [((0,), 0, 15)],
            {"type": "object", "properties": {"username": {"type": "string"}}, "required": []},
        ),
        (
            {
                "type": "object",
                "properties": {
                    "contact": {"type": "object", "properties": "broken"},
                },
                "required": [],
            },
            [(("contact", "email"), 5, 64)],
            {
                "type": "object",
                "properties": {
                    "contact": {"type": "object", "properties": "broken"},
                },
                "required": [],
            },
        ),
    ],
    ids=[
        "string-property-applies-length-bounds",
        "array-property-applies-item-bounds",
        "object-property-applies-property-bounds",
        "integer-property-no-applicable-keyword",
        "tighter-server-bound-overrides-looser-existing",
        "type-union-with-null-applies-length-bounds",
        "absent-property-not-synthesised",
        "non-object-root-passthrough",
        "oneof-applies-to-object-branch",
        "nested-path-applies-bounds",
        "bool-schema-passthrough",
        "empty-path-observation-skipped",
        "non-string-step-skipped",
        "non-dict-intermediate-properties-skipped",
    ],
)
def test_size_bound_adjustment_applies_correctly(input_schema, items, expected, case_factory):
    out = SizeBoundAdjustment().apply(
        operation=case_factory().operation,
        location=ParameterLocation.BODY,
        schema=input_schema,
        observations=_build_size_bound_observations(*items),
    )
    assert out == expected


def _build_format_observations(*items: tuple[tuple[str | int, ...], str]) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=path,
            kind=ObservationKind.FORMAT,
            raw_message=f"must be a valid {name}",
            payload=FormatPayload(name=name),
        )
        for path, name in items
    )


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"email": {"type": "string"}}, "required": []},
            [(("email",), "email")],
            {
                "type": "object",
                "properties": {"email": {"type": "string", "format": "email"}},
                "required": [],
            },
        ),
        (
            {
                "type": "object",
                "properties": {"email": {"type": "string", "format": "uri"}},
                "required": [],
            },
            [(("email",), "email")],
            {
                "type": "object",
                "properties": {"email": {"type": "string", "format": "uri"}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"age": {"type": "integer"}}, "required": []},
            [(("age",), "email")],
            {"type": "object", "properties": {"age": {"type": "integer"}}, "required": []},
        ),
        (
            {"type": "object", "properties": {"email": {"type": ["string", "null"]}}, "required": []},
            [(("email",), "email")],
            {
                "type": "object",
                "properties": {"email": {"type": ["string", "null"], "format": "email"}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {}, "required": []},
            [(("email",), "email")],
            {"type": "object", "properties": {}, "required": []},
        ),
        (True, [(("email",), "email")], True),
        ({"type": "string"}, [(("email",), "email")], {"type": "string"}),
        (
            {
                "oneOf": [
                    {"type": "object", "properties": {"email": {"type": "string"}}, "required": []},
                    {"type": "string"},
                ]
            },
            [(("email",), "email")],
            {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"email": {"type": "string", "format": "email"}},
                        "required": [],
                    },
                    {"type": "string"},
                ]
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"email": {"type": "string"}},
                        "required": [],
                    }
                },
                "required": [],
            },
            [(("contact", "email"), "email")],
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"email": {"type": "string", "format": "email"}},
                        "required": [],
                    }
                },
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"x": {"type": "string"}}, "required": []},
            [((), "email")],
            {"type": "object", "properties": {"x": {"type": "string"}}, "required": []},
        ),
        (
            {"type": "object", "properties": {"x": {"type": "string"}}, "required": []},
            [((0,), "email")],
            {"type": "object", "properties": {"x": {"type": "string"}}, "required": []},
        ),
    ],
    ids=[
        "string-property-format-injected",
        "existing-format-preserved",
        "non-string-property-skipped",
        "type-union-with-null-format-injected",
        "absent-property-not-synthesised",
        "bool-schema-passthrough",
        "non-object-root-passthrough",
        "oneof-applies-to-object-branch",
        "nested-path-format-injected",
        "empty-path-observation-skipped",
        "non-string-step-skipped",
    ],
)
def test_format_adjustment_applies_correctly(input_schema, items, expected, case_factory):
    out = FormatAdjustment().apply(
        operation=case_factory().operation,
        location=ParameterLocation.BODY,
        schema=input_schema,
        observations=_build_format_observations(*items),
    )
    assert out == expected


def _build_numeric_bound_observations(
    *items: tuple[tuple[str | int, ...], float, BoundDirection, bool],
) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=path,
            kind=ObservationKind.NUMERIC_BOUND,
            raw_message=f"must be {direction.value} {bound}",
            payload=NumericBoundPayload(bound=bound, direction=direction, exclusive=exclusive),
        )
        for path, bound, direction, exclusive in items
    )


@pytest.fixture
def openapi_31_case_factory(openapi_31):
    def factory():
        return openapi_31["/users"]["GET"].Case(method="GET", media_type="application/json")

    return factory


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"score": {"type": "integer"}}, "required": []},
            [(("score",), 0.0, BoundDirection.MIN, False)],
            {"type": "object", "properties": {"score": {"type": "integer", "minimum": 0}}, "required": []},
        ),
        (
            {"type": "object", "properties": {"score": {"type": "integer"}}, "required": []},
            [(("score",), 100.0, BoundDirection.MAX, True)],
            {
                "type": "object",
                "properties": {"score": {"type": "integer", "maximum": 100, "exclusiveMaximum": True}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"price": {"type": "number"}}, "required": []},
            [(("price",), 0.5, BoundDirection.MIN, True)],
            {
                "type": "object",
                "properties": {"price": {"type": "number", "minimum": 0.5, "exclusiveMinimum": True}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"score": {"type": "integer", "minimum": -10}}, "required": []},
            [(("score",), 0.0, BoundDirection.MIN, False)],
            {
                "type": "object",
                "properties": {"score": {"type": "integer", "minimum": -10}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"score": {"type": "integer", "maximum": 999}}, "required": []},
            [(("score",), 100.0, BoundDirection.MAX, True)],
            {
                "type": "object",
                "properties": {"score": {"type": "integer", "maximum": 999}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"score": {"type": "integer"}}, "required": []},
            [(("score",), 100.0, BoundDirection.MAX, False)],
            {"type": "object", "properties": {"score": {"type": "integer", "maximum": 100}}, "required": []},
        ),
        (
            {"type": "object", "properties": {"name": {"type": "string"}}, "required": []},
            [(("name",), 0.0, BoundDirection.MIN, False)],
            {"type": "object", "properties": {"name": {"type": "string"}}, "required": []},
        ),
        (
            {"type": "object", "properties": {"score": {"type": ["integer", "null"]}}, "required": []},
            [(("score",), 0.0, BoundDirection.MIN, False)],
            {
                "type": "object",
                "properties": {"score": {"type": ["integer", "null"], "minimum": 0}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {}, "required": []},
            [(("score",), 0.0, BoundDirection.MIN, False)],
            {"type": "object", "properties": {}, "required": []},
        ),
        (True, [(("score",), 0.0, BoundDirection.MIN, False)], True),
        ({"type": "string"}, [(("score",), 0.0, BoundDirection.MIN, False)], {"type": "string"}),
        (
            {
                "oneOf": [
                    {"type": "object", "properties": {"score": {"type": "integer"}}, "required": []},
                    {"type": "string"},
                ]
            },
            [(("score",), 0.0, BoundDirection.MIN, False)],
            {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"score": {"type": "integer", "minimum": 0}},
                        "required": [],
                    },
                    {"type": "string"},
                ]
            },
        ),
        (
            {"type": "object", "properties": {"x": {"type": "integer"}}, "required": []},
            [((), 0.0, BoundDirection.MIN, False)],
            {"type": "object", "properties": {"x": {"type": "integer"}}, "required": []},
        ),
    ],
    ids=[
        "integer-min-inclusive",
        "integer-max-exclusive-draft4",
        "number-decimal-bound",
        "existing-min-not-overwritten",
        "existing-max-not-overwritten",
        "max-inclusive-draft4",
        "non-numeric-property-skipped",
        "type-union-with-null-applies",
        "absent-property-not-synthesised",
        "bool-schema-passthrough",
        "non-object-root-passthrough",
        "oneof-applies-to-object-branch",
        "empty-path-observation-skipped",
    ],
)
def test_numeric_bound_adjustment_applies_correctly_draft4(input_schema, items, expected, case_factory):
    out = NumericBoundAdjustment().apply(
        operation=case_factory().operation,
        location=ParameterLocation.BODY,
        schema=input_schema,
        observations=_build_numeric_bound_observations(*items),
    )
    assert out == expected


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"score": {"type": "integer"}}, "required": []},
            [(("score",), 0.0, BoundDirection.MIN, False)],
            {"type": "object", "properties": {"score": {"type": "integer", "minimum": 0}}, "required": []},
        ),
        (
            {"type": "object", "properties": {"score": {"type": "integer"}}, "required": []},
            [(("score",), 0.0, BoundDirection.MIN, True)],
            {
                "type": "object",
                "properties": {"score": {"type": "integer", "exclusiveMinimum": 0}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"score": {"type": "integer"}}, "required": []},
            [(("score",), 100.0, BoundDirection.MAX, True)],
            {
                "type": "object",
                "properties": {"score": {"type": "integer", "exclusiveMaximum": 100}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"price": {"type": "number"}}, "required": []},
            [(("price",), 0.5, BoundDirection.MIN, True)],
            {
                "type": "object",
                "properties": {"price": {"type": "number", "exclusiveMinimum": 0.5}},
                "required": [],
            },
        ),
    ],
    ids=[
        "integer-min-inclusive",
        "integer-min-exclusive-draft2020",
        "integer-max-exclusive-draft2020",
        "number-decimal-exclusive-draft2020",
    ],
)
def test_numeric_bound_adjustment_applies_correctly_draft2020(input_schema, items, expected, openapi_31_case_factory):
    out = NumericBoundAdjustment().apply(
        operation=openapi_31_case_factory().operation,
        location=ParameterLocation.BODY,
        schema=input_schema,
        observations=_build_numeric_bound_observations(*items),
    )
    assert out == expected


def _build_pattern_observations(*items: tuple[tuple[str | int, ...], str]) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=path,
            kind=ObservationKind.PATTERN,
            raw_message=f'must match "{regex}"',
            payload=PatternPayload(regex=regex),
        )
        for path, regex in items
    )


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"code": {"type": "string"}}, "required": []},
            [(("code",), "[A-Z]{2,4}")],
            {
                "type": "object",
                "properties": {"code": {"type": "string", "pattern": "[A-Z]{2,4}"}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"code": {"type": "string", "pattern": "[a-z]+"}}, "required": []},
            [(("code",), "[A-Z]+")],
            {
                "type": "object",
                "properties": {"code": {"type": "string", "pattern": "[a-z]+"}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"age": {"type": "integer"}}, "required": []},
            [(("age",), "[0-9]+")],
            {"type": "object", "properties": {"age": {"type": "integer"}}, "required": []},
        ),
        (
            {"type": "object", "properties": {"code": {"type": ["string", "null"]}}, "required": []},
            [(("code",), "[A-Z]+")],
            {
                "type": "object",
                "properties": {"code": {"type": ["string", "null"], "pattern": "[A-Z]+"}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {}, "required": []},
            [(("code",), "[A-Z]+")],
            {"type": "object", "properties": {}, "required": []},
        ),
        (True, [(("code",), "[A-Z]+")], True),
        ({"type": "string"}, [(("code",), "[A-Z]+")], {"type": "string"}),
        (
            {
                "oneOf": [
                    {"type": "object", "properties": {"code": {"type": "string"}}, "required": []},
                    {"type": "string"},
                ]
            },
            [(("code",), "[A-Z]+")],
            {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"code": {"type": "string", "pattern": "[A-Z]+"}},
                        "required": [],
                    },
                    {"type": "string"},
                ]
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"phone": {"type": "string"}},
                        "required": [],
                    }
                },
                "required": [],
            },
            [(("contact", "phone"), "\\+?\\d{3,15}")],
            {
                "type": "object",
                "properties": {
                    "contact": {
                        "type": "object",
                        "properties": {"phone": {"type": "string", "pattern": "\\+?\\d{3,15}"}},
                        "required": [],
                    }
                },
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"code": {"type": "string"}}, "required": []},
            [(("code",), "\\p{L}+")],
            {
                "type": "object",
                "properties": {"code": {"type": "string", "pattern": normalize_regex("\\p{L}+")}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"code": {"type": "string"}}, "required": []},
            [(("code",), "\\A[A-Z]+\\Z")],
            {
                "type": "object",
                "properties": {"code": {"type": "string", "pattern": "^[A-Z]+$"}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"code": {"type": "string"}}, "required": []},
            [(("code",), "[A-Z(unbalanced")],
            {"type": "object", "properties": {"code": {"type": "string"}}, "required": []},
        ),
        (
            {"type": "object", "properties": {"code": {"type": "string"}}, "required": []},
            [((), "[A-Z]+")],
            {"type": "object", "properties": {"code": {"type": "string"}}, "required": []},
        ),
    ],
    ids=[
        "string-property-pattern-injected",
        "existing-pattern-preserved",
        "non-string-property-skipped",
        "type-union-with-null-pattern-injected",
        "absent-property-not-synthesised",
        "bool-schema-passthrough",
        "non-object-root-passthrough",
        "oneof-applies-to-object-branch",
        "nested-path-pattern-injected",
        "pcre-unicode-property-translated",
        "python-anchors-translated-to-ecma",
        "invalid-untranslatable-pattern-skipped",
        "empty-path-observation-skipped",
    ],
)
def test_pattern_adjustment_applies_correctly(input_schema, items, expected, case_factory):
    out = PatternAdjustment().apply(
        operation=case_factory().operation,
        location=ParameterLocation.BODY,
        schema=input_schema,
        observations=_build_pattern_observations(*items),
    )
    assert out == expected


def _build_type_mismatch_observations(
    *items: tuple[tuple[str | int, ...], str],
) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=path,
            kind=ObservationKind.TYPE_MISMATCH,
            raw_message=f'Cannot deserialize value of type `{type_name}` from String "..."',
            payload=TypeMismatchPayload(type_name=type_name),
        )
        for path, type_name in items
    )


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"hire_date": {"type": "string"}}, "required": []},
            [(("hire_date",), "java.time.LocalDate")],
            {
                "type": "object",
                "properties": {"hire_date": {"type": "string", "format": "date"}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"started_at": {"type": "string"}}, "required": []},
            [(("started_at",), "java.time.LocalDateTime")],
            {
                "type": "object",
                "properties": {"started_at": {"type": "string", "format": "date-time"}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"created_at": {"type": "string"}}, "required": []},
            [(("created_at",), "java.time.Instant")],
            {
                "type": "object",
                "properties": {"created_at": {"type": "string", "format": "date-time"}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"token": {"type": "string"}}, "required": []},
            [(("token",), "java.util.UUID")],
            {
                "type": "object",
                "properties": {"token": {"type": "string", "format": "uuid"}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"website": {"type": "string"}}, "required": []},
            [(("website",), "java.net.URL")],
            {
                "type": "object",
                "properties": {"website": {"type": "string", "format": "uri"}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"hire_date": {"type": "string", "format": "date"}}, "required": []},
            [(("hire_date",), "java.time.LocalDate")],
            {
                "type": "object",
                "properties": {"hire_date": {"type": "string", "format": "date"}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"x": {"type": "string"}}, "required": []},
            [(("x",), "com.example.Custom")],
            {"type": "object", "properties": {"x": {"type": "string"}}, "required": []},
        ),
        (
            {"type": "object", "properties": {"x": {"type": "integer"}}, "required": []},
            [(("x",), "java.time.LocalDate")],
            {"type": "object", "properties": {"x": {"type": "integer"}}, "required": []},
        ),
        (
            {"type": "object", "properties": {"hire_date": {"type": ["string", "null"]}}, "required": []},
            [(("hire_date",), "java.time.LocalDate")],
            {
                "type": "object",
                "properties": {"hire_date": {"type": ["string", "null"], "format": "date"}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {}, "required": []},
            [(("hire_date",), "java.time.LocalDate")],
            {"type": "object", "properties": {}, "required": []},
        ),
        (True, [(("hire_date",), "java.time.LocalDate")], True),
        ({"type": "string"}, [(("hire_date",), "java.time.LocalDate")], {"type": "string"}),
        (
            {
                "type": "object",
                "properties": {
                    "owner": {
                        "type": "object",
                        "properties": {"hire_date": {"type": "string"}},
                        "required": [],
                    }
                },
                "required": [],
            },
            [(("owner", "hire_date"), "java.time.LocalDate")],
            {
                "type": "object",
                "properties": {
                    "owner": {
                        "type": "object",
                        "properties": {"hire_date": {"type": "string", "format": "date"}},
                        "required": [],
                    }
                },
                "required": [],
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "addresses": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"created_on": {"type": "string"}},
                            "required": [],
                        },
                    }
                },
                "required": [],
            },
            [(("addresses", 0, "created_on"), "java.time.LocalDate")],
            {
                "type": "object",
                "properties": {
                    "addresses": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"created_on": {"type": "string", "format": "date"}},
                            "required": [],
                        },
                    }
                },
                "required": [],
            },
        ),
        (
            {
                "type": "object",
                "properties": {"addresses": {"type": "array"}},
                "required": [],
            },
            [(("addresses", 0, "created_on"), "java.time.LocalDate")],
            {
                "type": "object",
                "properties": {"addresses": {"type": "array"}},
                "required": [],
            },
        ),
    ],
    ids=[
        "localdate-to-date",
        "localdatetime-to-date-time",
        "instant-to-date-time",
        "uuid-to-uuid",
        "url-to-uri",
        "existing-format-preserved",
        "unmapped-type-passthrough",
        "non-string-property-skipped",
        "type-union-with-null-applies",
        "absent-property-not-synthesised",
        "bool-schema-passthrough",
        "non-object-root-passthrough",
        "nested-path-applies-format",
        "array-element-applies-format-via-items",
        "array-without-items-passes-through",
    ],
)
def test_type_mismatch_adjustment_applies_correctly(input_schema, items, expected, case_factory):
    out = TypeMismatchAdjustment().apply(
        operation=case_factory().operation,
        location=ParameterLocation.BODY,
        schema=input_schema,
        observations=_build_type_mismatch_observations(*items),
    )
    assert out == expected


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"age": {"type": "string"}}, "required": []},
            [(("age",), "integer")],
            {"type": "object", "properties": {"age": {"type": "integer"}}, "required": []},
        ),
        (
            {"type": "object", "properties": {"age": {"type": "integer"}}, "required": []},
            [(("age",), "integer")],
            {"type": "object", "properties": {"age": {"type": "integer"}}, "required": []},
        ),
        (
            {
                "type": "object",
                "properties": {"age": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
                "required": [],
            },
            [(("age",), "integer")],
            {
                "type": "object",
                "properties": {"age": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
                "required": [],
            },
        ),
        (
            {
                "type": "object",
                "properties": {"age": {"oneOf": [{"type": "string"}, {"type": "integer"}]}},
                "required": [],
            },
            [(("age",), "integer")],
            {
                "type": "object",
                "properties": {"age": {"oneOf": [{"type": "string"}, {"type": "integer"}]}},
                "required": [],
            },
        ),
        (
            {
                "type": "object",
                "properties": {"age": {"type": ["string", "integer"]}},
                "required": [],
            },
            [(("age",), "integer")],
            {
                "type": "object",
                "properties": {"age": {"type": ["string", "integer"]}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"x": {"type": "string"}}, "required": []},
            [(("x",), "boolean")],
            {"type": "object", "properties": {"x": {"type": "boolean"}}, "required": []},
        ),
        (
            {"type": "object", "properties": {"hire_date": {"type": "string"}}, "required": []},
            [(("hire_date",), "java.time.LocalDate")],
            {
                "type": "object",
                "properties": {"hire_date": {"type": "string", "format": "date"}},
                "required": [],
            },
        ),
        (
            # Schema declares a sub-object; rewriting `type` to a scalar would
            # leave `properties` orphaned. Conservative: skip.
            {
                "type": "object",
                "properties": {"profile": {"type": "object", "properties": {"name": {"type": "string"}}}},
                "required": [],
            },
            [(("profile",), "integer")],
            {
                "type": "object",
                "properties": {"profile": {"type": "object", "properties": {"name": {"type": "string"}}}},
                "required": [],
            },
        ),
        (
            # Schema declares an array; rewriting `type` to a scalar would
            # leave `items` orphaned. Conservative: skip.
            {
                "type": "object",
                "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
                "required": [],
            },
            [(("tags",), "integer")],
            {
                "type": "object",
                "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
                "required": [],
            },
        ),
    ],
    ids=[
        "drf-token-rewrites-scalar-type",
        "drf-token-noop-when-matching",
        "composed-anyOf-skip",
        "composed-oneOf-skip",
        "type-list-skip",
        "drf-token-boolean",
        "java-fqn-regression",
        "drf-token-skipped-when-existing-type-is-object",
        "drf-token-skipped-when-existing-type-is-array",
    ],
)
def test_type_mismatch_adjustment_handles_drf_and_java_payloads(input_schema, items, expected, case_factory):
    out = TypeMismatchAdjustment().apply(
        operation=case_factory().operation,
        location=ParameterLocation.BODY,
        schema=input_schema,
        observations=_build_type_mismatch_observations(*items),
    )
    assert out == expected


def _build_enum_observations(*items: tuple[tuple[str | int, ...], tuple[str, ...]]) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=path,
            kind=ObservationKind.ENUM,
            raw_message=f"not one of the values accepted for Enum class: [{', '.join(values)}]",
            payload=EnumPayload(values=values),
        )
        for path, values in items
    )


@pytest.mark.parametrize(
    "input_schema, items, expected",
    [
        (
            {"type": "object", "properties": {"role": {"type": "string"}}, "required": []},
            [(("role",), ("USER", "ADMIN"))],
            {
                "type": "object",
                "properties": {"role": {"type": "string", "enum": ["USER", "ADMIN"]}},
                "required": [],
            },
        ),
        (
            {
                "type": "object",
                "properties": {"role": {"type": "string", "enum": ["GUEST"]}},
                "required": [],
            },
            [(("role",), ("USER", "ADMIN"))],
            {
                "type": "object",
                "properties": {"role": {"type": "string", "enum": ["GUEST"]}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"role": {"type": "integer"}}, "required": []},
            [(("role",), ("USER", "ADMIN"))],
            {"type": "object", "properties": {"role": {"type": "integer"}}, "required": []},
        ),
        (
            {"type": "object", "properties": {"role": {"type": ["string", "null"]}}, "required": []},
            [(("role",), ("USER", "ADMIN"))],
            {
                "type": "object",
                "properties": {"role": {"type": ["string", "null"], "enum": ["USER", "ADMIN"]}},
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {}, "required": []},
            [(("role",), ("USER", "ADMIN"))],
            {"type": "object", "properties": {}, "required": []},
        ),
        (True, [(("role",), ("USER", "ADMIN"))], True),
        ({"type": "string"}, [(("role",), ("USER", "ADMIN"))], {"type": "string"}),
        (
            {
                "oneOf": [
                    {"type": "object", "properties": {"role": {"type": "string"}}, "required": []},
                    {"type": "string"},
                ]
            },
            [(("role",), ("USER", "ADMIN"))],
            {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"role": {"type": "string", "enum": ["USER", "ADMIN"]}},
                        "required": [],
                    },
                    {"type": "string"},
                ]
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "account": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                        "required": [],
                    }
                },
                "required": [],
            },
            [(("account", "status"), ("ACTIVE", "ARCHIVED"))],
            {
                "type": "object",
                "properties": {
                    "account": {
                        "type": "object",
                        "properties": {"status": {"type": "string", "enum": ["ACTIVE", "ARCHIVED"]}},
                        "required": [],
                    }
                },
                "required": [],
            },
        ),
        (
            {"type": "object", "properties": {"role": {"type": "string"}}, "required": []},
            [((), ("USER",))],
            {"type": "object", "properties": {"role": {"type": "string"}}, "required": []},
        ),
    ],
    ids=[
        "string-property-enum-injected",
        "existing-enum-preserved",
        "non-string-property-skipped",
        "type-union-with-null-enum-injected",
        "absent-property-not-synthesised",
        "bool-schema-passthrough",
        "non-object-root-passthrough",
        "oneof-applies-to-object-branch",
        "nested-path-enum-injected",
        "empty-path-observation-skipped",
    ],
)
def test_enum_adjustment_applies_correctly(input_schema, items, expected, case_factory):
    out = EnumAdjustment().apply(
        operation=case_factory().operation,
        location=ParameterLocation.BODY,
        schema=input_schema,
        observations=_build_enum_observations(*items),
    )
    assert out == expected


def test_size_bound_adjustment_preserves_stricter_existing_bound(case_factory):
    # Schema's `maxLength: 10` is tighter than server's `max: 15` — keep the schema's.
    schema = {"type": "object", "properties": {"username": {"type": "string", "maxLength": 10}}, "required": []}
    out = SizeBoundAdjustment().apply(
        operation=case_factory().operation,
        location=ParameterLocation.BODY,
        schema=schema,
        observations=_build_size_bound_observations((("username",), 0, 15)),
    )
    assert out == {
        "type": "object",
        "properties": {"username": {"type": "string", "minLength": 0, "maxLength": 10}},
        "required": [],
    }


def test_size_bound_adjustment_min_only_payload(case_factory):
    schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": []}
    obs = (
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("name",),
            kind=ObservationKind.SIZE_BOUND,
            raw_message="too short",
            payload=SizeBoundPayload(min=3, max=None),
        ),
    )
    out = SizeBoundAdjustment().apply(
        operation=case_factory().operation,
        location=ParameterLocation.BODY,
        schema=schema,
        observations=obs,
    )
    assert out == {
        "type": "object",
        "properties": {"name": {"type": "string", "minLength": 3}},
        "required": [],
    }


def test_size_bound_adjustment_max_only_payload(case_factory):
    schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": []}
    obs = (
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("name",),
            kind=ObservationKind.SIZE_BOUND,
            raw_message="too long",
            payload=SizeBoundPayload(min=None, max=20),
        ),
    )
    out = SizeBoundAdjustment().apply(
        operation=case_factory().operation,
        location=ParameterLocation.BODY,
        schema=schema,
        observations=obs,
    )
    assert out == {
        "type": "object",
        "properties": {"name": {"type": "string", "maxLength": 20}},
        "required": [],
    }


def test_apply_adjustments_does_not_mutate_caller_schema(case_factory):
    # Callers cache the input schema; the dispatcher must clone before mutating
    # so adjustment-internal mutation never leaks back to the caller.
    original = {
        "type": "object",
        "properties": {"username": {"type": "string"}},
        "required": [],
    }
    snapshot = json.loads(json.dumps(original))

    case = case_factory()
    store = ErrorFeedbackStore()
    blank_observation = Observation(
        operation_label=case.operation.label,
        location=ParameterLocation.BODY,
        parameter_path=("username",),
        kind=ObservationKind.MUST_NOT_BE_BLANK,
        raw_message="must not be blank",
    )
    size_observation = Observation(
        operation_label=case.operation.label,
        location=ParameterLocation.BODY,
        parameter_path=("username",),
        kind=ObservationKind.SIZE_BOUND,
        raw_message="size must be between 3 and 8",
        payload=SizeBoundPayload(min=3, max=8),
    )
    for o in (blank_observation, blank_observation, size_observation, size_observation):
        store.record(o)

    out = apply_adjustments(
        operation=case.operation,
        location=ParameterLocation.BODY,
        schema=original,
        store=store,
    )
    assert original == snapshot
    assert out is not original
    assert out["properties"]["username"] == {"type": "string", "minLength": 3, "maxLength": 8}
    assert out["required"] == ["username"]


# Pydantic v2 error fixtures. `test_pydantic_fixture_matches_runtime` keeps
# them aligned with the installed Pydantic version.
_PYDANTIC_FIXTURES: tuple[tuple[dict, Observation], ...] = (
    (
        {"type": "missing", "loc": ["body", "name"], "msg": "Field required"},
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("name",),
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="Field required",
        ),
    ),
    (
        {
            "type": "string_too_short",
            "loc": ["body", "name"],
            "msg": "String should have at least 3 characters",
            "ctx": {"min_length": 3},
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("name",),
            kind=ObservationKind.SIZE_BOUND,
            raw_message="String should have at least 3 characters",
            payload=SizeBoundPayload(min=3, max=None),
        ),
    ),
    (
        {
            "type": "string_too_long",
            "loc": ["body", "name"],
            "msg": "String should have at most 20 characters",
            "ctx": {"max_length": 20},
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("name",),
            kind=ObservationKind.SIZE_BOUND,
            raw_message="String should have at most 20 characters",
            payload=SizeBoundPayload(min=None, max=20),
        ),
    ),
    (
        {
            "type": "greater_than",
            "loc": ["body", "qty"],
            "msg": "Input should be greater than 0",
            "ctx": {"gt": 0},
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("qty",),
            kind=ObservationKind.NUMERIC_BOUND,
            raw_message="Input should be greater than 0",
            payload=NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
        ),
    ),
    (
        {
            "type": "greater_than_equal",
            "loc": ["body", "qty"],
            "msg": "Input should be greater than or equal to 1",
            "ctx": {"ge": 1},
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("qty",),
            kind=ObservationKind.NUMERIC_BOUND,
            raw_message="Input should be greater than or equal to 1",
            payload=NumericBoundPayload(bound=1.0, direction=BoundDirection.MIN, exclusive=False),
        ),
    ),
    (
        {
            "type": "less_than",
            "loc": ["body", "qty"],
            "msg": "Input should be less than 100",
            "ctx": {"lt": 100},
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("qty",),
            kind=ObservationKind.NUMERIC_BOUND,
            raw_message="Input should be less than 100",
            payload=NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=True),
        ),
    ),
    (
        {
            "type": "less_than_equal",
            "loc": ["body", "qty"],
            "msg": "Input should be less than or equal to 100",
            "ctx": {"le": 100},
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("qty",),
            kind=ObservationKind.NUMERIC_BOUND,
            raw_message="Input should be less than or equal to 100",
            payload=NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=False),
        ),
    ),
    (
        {
            "type": "string_pattern_mismatch",
            "loc": ["body", "code"],
            "msg": "String should match pattern '[A-Z]+'",
            "ctx": {"pattern": "[A-Z]+"},
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("code",),
            kind=ObservationKind.PATTERN,
            raw_message="String should match pattern '[A-Z]+'",
            payload=PatternPayload(regex="[A-Z]+"),
        ),
    ),
    (
        {
            "type": "literal_error",
            "loc": ["body", "priority"],
            "msg": "Input should be 'LOW' or 'HIGH'",
            "ctx": {"expected": "'LOW' or 'HIGH'"},
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("priority",),
            kind=ObservationKind.ENUM,
            raw_message="Input should be 'LOW' or 'HIGH'",
            payload=EnumPayload(values=("LOW", "HIGH")),
        ),
    ),
    (
        {
            "type": "date_from_datetime_parsing",
            "loc": ["body", "hire_date"],
            "msg": "Input should be a valid date or datetime, invalid character in year",
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("hire_date",),
            kind=ObservationKind.FORMAT,
            raw_message="Input should be a valid date or datetime, invalid character in year",
            payload=FormatPayload(name="date"),
        ),
    ),
    (
        {
            "type": "datetime_from_date_parsing",
            "loc": ["body", "started_at"],
            "msg": "Input should be a valid datetime or date, invalid character in year",
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("started_at",),
            kind=ObservationKind.FORMAT,
            raw_message="Input should be a valid datetime or date, invalid character in year",
            payload=FormatPayload(name="date-time"),
        ),
    ),
    (
        {
            "type": "uuid_parsing",
            "loc": ["body", "token"],
            "msg": "Input should be a valid UUID",
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("token",),
            kind=ObservationKind.FORMAT,
            raw_message="Input should be a valid UUID",
            payload=FormatPayload(name="uuid"),
        ),
    ),
    (
        {
            "type": "url_parsing",
            "loc": ["body", "website"],
            "msg": "Input should be a valid URL",
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("website",),
            kind=ObservationKind.FORMAT,
            raw_message="Input should be a valid URL",
            payload=FormatPayload(name="uri"),
        ),
    ),
)


@pytest.mark.parametrize(
    "entry, expected",
    _PYDANTIC_FIXTURES,
    ids=[entry["type"] for entry, _ in _PYDANTIC_FIXTURES],
)
def test_pydantic_parser_extracts_observation(entry, expected, make_operation):
    obs = PydanticParser().parse(operation=make_operation(), body={"detail": [entry]})
    assert obs == (expected,)


def test_pydantic_parser_coerces_decimal_numeric_bounds(make_operation):
    # Decimal-typed Pydantic fields put a Decimal in `ctx`; the bound must
    # still produce a NumericBoundPayload (coerced to float).
    body = {
        "detail": [
            {
                "type": "greater_than",
                "loc": ["body", "price"],
                "msg": "Input should be greater than 0",
                "ctx": {"gt": Decimal("0.01")},
            }
        ]
    }
    obs = PydanticParser().parse(operation=make_operation(), body=body)
    assert obs == (
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("price",),
            kind=ObservationKind.NUMERIC_BOUND,
            raw_message="Input should be greater than 0",
            payload=NumericBoundPayload(bound=0.01, direction=BoundDirection.MIN, exclusive=True),
        ),
    )


class _PydanticStrings(BaseModel):
    name: Annotated[str, Field(min_length=3, max_length=20)] = "default"
    code: Annotated[str, Field(pattern=r"[A-Z]+")] = "DEFAULT"


class _PydanticRequired(BaseModel):
    name: str


class _PydanticGT(BaseModel):
    qty: Annotated[int, Field(gt=0)] = 1


class _PydanticGE(BaseModel):
    qty: Annotated[int, Field(ge=1)] = 1


class _PydanticLT(BaseModel):
    qty: Annotated[int, Field(lt=100)] = 1


class _PydanticLE(BaseModel):
    qty: Annotated[int, Field(le=100)] = 1


class _PydanticLiteral(BaseModel):
    priority: Literal["LOW", "HIGH"] = "LOW"


class _PydanticFormats(BaseModel):
    hire_date: date = date(2024, 1, 1)
    started_at: datetime = datetime(2024, 1, 1)
    started_time: time = time(0, 0)
    token: UUID = UUID("00000000-0000-0000-0000-000000000000")
    website: AnyUrl = "http://example.com"


# `(type_code, model_class, invalid_kwargs, context_key)` — the wire-level
# `ctx` key the parser reads for this code; empty string means none is read.
_PYDANTIC_RUNTIME_CASES: tuple[tuple[str, type[BaseModel], dict, str], ...] = (
    ("missing", _PydanticRequired, {}, ""),
    ("string_too_short", _PydanticStrings, {"name": "x"}, "min_length"),
    ("string_too_long", _PydanticStrings, {"name": "x" * 100}, "max_length"),
    ("greater_than", _PydanticGT, {"qty": 0}, "gt"),
    ("greater_than_equal", _PydanticGE, {"qty": 0}, "ge"),
    ("less_than", _PydanticLT, {"qty": 100}, "lt"),
    ("less_than_equal", _PydanticLE, {"qty": 101}, "le"),
    ("string_pattern_mismatch", _PydanticStrings, {"code": "lowercase"}, "pattern"),
    ("literal_error", _PydanticLiteral, {"priority": "BOGUS"}, "expected"),
    ("date_from_datetime_parsing", _PydanticFormats, {"hire_date": "not-a-date"}, ""),
    ("datetime_from_date_parsing", _PydanticFormats, {"started_at": "not-a-datetime"}, ""),
    ("uuid_parsing", _PydanticFormats, {"token": "not-a-uuid"}, ""),
    ("url_parsing", _PydanticFormats, {"website": "not-a-url"}, ""),
)


@pytest.mark.parametrize(
    "type_code, model, invalid_kwargs, context_key",
    _PYDANTIC_RUNTIME_CASES,
    ids=[case[0] for case in _PYDANTIC_RUNTIME_CASES],
)
def test_pydantic_fixture_matches_runtime(type_code, model, invalid_kwargs, context_key):
    # Drift guard: minor Pydantic version bumps that rename a `type` code or
    # restructure `ctx` fail this test loudly.
    fixtures_by_type = {entry["type"]: entry for entry, _ in _PYDANTIC_FIXTURES}
    fixture = fixtures_by_type[type_code]
    with pytest.raises(ValidationError) as exc_info:
        model(**invalid_kwargs)
    runtime = next((err for err in exc_info.value.errors() if err["type"] == type_code), None)
    assert runtime is not None, f"runtime Pydantic did not emit type={type_code!r}"
    wire_path = tuple(fixture["loc"][1:])
    assert tuple(runtime["loc"])[-len(wire_path) :] == wire_path, (
        f"loc tail mismatch: runtime {runtime['loc']!r} vs fixture {wire_path!r}"
    )
    if context_key:
        assert runtime.get("ctx", {}).get(context_key) == fixture["ctx"][context_key], (
            f"ctx.{context_key} drift: runtime {runtime.get('ctx', {}).get(context_key)!r} "
            f"vs fixture {fixture['ctx'][context_key]!r}"
        )


@pytest.mark.parametrize(
    "loc_prefix, expected_location",
    [
        ("body", ParameterLocation.BODY),
        ("query", ParameterLocation.QUERY),
        ("path", ParameterLocation.PATH),
        ("header", ParameterLocation.HEADER),
        ("cookie", ParameterLocation.COOKIE),
        ("form", ParameterLocation.BODY),
    ],
    ids=["body", "query", "path", "header", "cookie", "form-as-body"],
)
def test_pydantic_parser_maps_loc_prefix_to_location(loc_prefix, expected_location, make_operation):
    body = {"detail": [{"type": "missing", "loc": [loc_prefix, "x"], "msg": "Field required"}]}
    obs = PydanticParser().parse(operation=make_operation(), body=body)
    assert len(obs) == 1
    assert obs[0].location == expected_location
    assert obs[0].parameter_path == ("x",)


def test_pydantic_parser_defaults_to_body_when_loc_prefix_unrecognized(make_operation):
    body = {"detail": [{"type": "missing", "loc": ["unknown", "x"], "msg": "Field required"}]}
    obs = PydanticParser().parse(operation=make_operation(), body=body)
    assert obs[0].location == ParameterLocation.BODY
    assert obs[0].parameter_path == ("unknown", "x")


def test_pydantic_parser_handles_int_path_segments(make_operation):
    # FastAPI emits int segments for list-element validation failures.
    body = {"detail": [{"type": "missing", "loc": ["body", "items", 0, "qty"], "msg": "Field required"}]}
    obs = PydanticParser().parse(operation=make_operation(), body=body)
    assert obs[0].parameter_path == ("items", 0, "qty")
    assert obs[0].location == ParameterLocation.BODY


@pytest.mark.parametrize(
    "text, expected",
    [
        ("'LOW' or 'HIGH'", ("LOW", "HIGH")),
        ("'a', 'b' or 'c'", ("a", "b", "c")),
        ("'ONE'", ("ONE",)),
        ("'PENDING', 'ACTIVE' or 'ARCHIVED'", ("PENDING", "ACTIVE", "ARCHIVED")),
        # Pydantic switches quote style for values containing the other quote.
        ("\"O'Brien\" or 'Smith'", ("O'Brien", "Smith")),
        ('"a\'b" or "c\'d"', ("a'b", "c'd")),
        ("nothing here", None),
        ("", None),
    ],
    ids=[
        "two-values",
        "three-values",
        "single",
        "longer",
        "apostrophe-mix",
        "all-double-quoted",
        "empty-prose",
        "empty-string",
    ],
)
def test_pydantic_parse_expected_extracts_values(text, expected):
    assert _parse_expected(text) == expected


@pytest.mark.parametrize("value", [None, 123, ["LOW", "HIGH"], {"expected": "LOW"}])
def test_pydantic_parse_expected_returns_none_for_non_string(value):
    assert _parse_expected(value) is None


@pytest.mark.parametrize(
    "body",
    [
        None,
        "not a dict",
        [1, 2, 3],
        {"detail": "RFC 7807 prose"},
        {"detail": [{"type": "missing"}]},  # missing loc
        {"detail": [{"type": "unknown_code", "loc": ["body", "x"], "msg": "..."}]},
        {"detail": [{"type": "missing", "loc": [], "msg": "..."}]},  # empty loc - no path
        {"detail": [{"type": "missing", "loc": ["body"], "msg": "..."}]},  # only the location prefix
    ],
    ids=[
        "none",
        "string-body",
        "list-body",
        "detail-string",
        "missing-loc",
        "unknown-type-code",
        "empty-loc",
        "loc-prefix-only",
    ],
)
def test_pydantic_parser_parse_returns_empty_for_uninteresting_bodies(body, make_operation):
    assert PydanticParser().parse(operation=make_operation(), body=body) == ()


@pytest.mark.parametrize(
    "type_code, context",
    [
        ("string_too_short", {}),
        ("string_too_long", {}),
        ("greater_than", {}),
        ("greater_than", {"gt": True}),
        ("less_than_equal", {"le": "100"}),
        ("string_pattern_mismatch", {}),
        ("enum", {"expected": "no quoted values here"}),
        ("literal_error", {}),
    ],
    ids=[
        "size-min-missing",
        "size-max-missing",
        "numeric-missing",
        "numeric-bool",
        "numeric-string",
        "pattern-missing",
        "enum-no-tokens",
        "literal-no-context",
    ],
)
def test_pydantic_parser_skips_handler_with_invalid_context(type_code, context, make_operation):
    body = {"detail": [{"type": type_code, "loc": ["body", "x"], "msg": "...", "ctx": context}]}
    assert PydanticParser().parse(operation=make_operation(), body=body) == ()


def test_pydantic_parser_skips_non_dict_detail_entry(make_operation):
    body = {"detail": [42, {"type": "missing", "loc": ["body", "x"], "msg": "Field required"}]}
    obs = PydanticParser().parse(operation=make_operation(), body=body)
    assert obs[0].parameter_path == ("x",)
    assert len(obs) == 1


def test_pydantic_parser_emits_one_observation_per_detail_entry(make_operation):
    body = {
        "detail": [
            {"type": "missing", "loc": ["body", "name"], "msg": "Field required"},
            {"type": "string_too_short", "loc": ["body", "code"], "msg": "...", "ctx": {"min_length": 3}},
        ]
    }
    obs = PydanticParser().parse(operation=make_operation(), body=body)
    assert [(o.parameter_path, o.kind) for o in obs] == [
        (("name",), ObservationKind.MUST_NOT_BE_BLANK),
        (("code",), ObservationKind.SIZE_BOUND),
    ]
