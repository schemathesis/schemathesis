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
from schemathesis.core.error_feedback.parsers.ajv import AjvParser
from schemathesis.core.error_feedback.parsers.aspnet import AspNetParser
from schemathesis.core.error_feedback.parsers.drf import DRFParser, _classify, _walk
from schemathesis.core.error_feedback.parsers.extractors import location_for_method
from schemathesis.core.error_feedback.parsers.go_validator import GoValidatorParser
from schemathesis.core.error_feedback.parsers.jackson import JacksonParser
from schemathesis.core.error_feedback.parsers.laravel import LaravelParser
from schemathesis.core.error_feedback.parsers.pydantic import PydanticParser, _parse_expected
from schemathesis.core.error_feedback.parsers.rails import (
    RailsParser,
    _classify_message,
    _split_legacy_message,
    _walk_legacy,
    _walk_modern,
)
from schemathesis.core.error_feedback.parsers.spring import SpringParser
from schemathesis.core.error_feedback.parsers.symfony import SymfonyParser
from schemathesis.core.error_feedback.parsers.zod import ZodParser
from schemathesis.core.error_feedback.pipeline import FeedbackPipeline, _reset_pipeline_for_tests
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import Response
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
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
def test_spring_parser_extracts_observations(body, expected_paths, make_operation, case_factory):
    obs = SpringParser().parse(
        operation=make_operation(),
        body=json.loads(body),
        case=case_factory(),
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
def test_spring_parser_recognizes_non_blank_message_variants(message, make_operation, case_factory):
    body = {"subErrors": [{"field": "x", "message": message}]}
    obs = SpringParser().parse(operation=make_operation(), body=body, case=case_factory())
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
    assert SpringParser().parse(operation=make_operation(), body=body, case=case_factory()) == ()


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
    obs = SpringParser().parse(operation=make_operation(), body=body, case=case_factory())
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
def test_spring_parser_recognizes_format_message_variants(message, expected_name, make_operation, case_factory):
    body = {"subErrors": [{"field": "contact", "message": message}]}
    obs = SpringParser().parse(operation=make_operation(), body=body, case=case_factory())
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (("contact",), ObservationKind.FORMAT, FormatPayload(name=expected_name)),
    ]


def test_spring_parser_uuid_takes_precedence_over_uri_when_both_match(make_operation, case_factory):
    # Defensive: a contrived "must be a valid URI UUID" string would match both
    # the URI and UUID regexes. The classifier checks UUID first so the more
    # specific format wins.
    body = {"subErrors": [{"field": "x", "message": "must be a valid UUID"}]}
    obs = SpringParser().parse(operation=make_operation(), body=body, case=case_factory())
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
    obs = SpringParser().parse(operation=make_operation(), body=body, case=case_factory())
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
def test_spring_parser_recognizes_pattern_message_variants(message, expected_regex, make_operation, case_factory):
    body = {"subErrors": [{"field": "code", "message": message}]}
    obs = SpringParser().parse(operation=make_operation(), body=body, case=case_factory())
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
    assert location_for_method(method) is expected


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


def test_drf_parser_parse_flat_field(make_operation, case_factory):
    body = {"name": ["This field is required."]}
    assert DRFParser().parse(operation=make_operation(), body=body, case=case_factory()) == (
        _drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("name",),
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="This field is required.",
        ),
    )


def test_drf_parser_parse_nested_with_size_bound(make_operation, case_factory):
    body = {"address": {"zipcode": ["Ensure this field has at least 5 characters."]}}
    assert DRFParser().parse(operation=make_operation(), body=body, case=case_factory()) == (
        _drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("address", "zipcode"),
            kind=ObservationKind.SIZE_BOUND,
            raw_message="Ensure this field has at least 5 characters.",
            payload=SizeBoundPayload(min=5, max=None),
        ),
    )


def test_drf_parser_parse_get_request_yields_query_location(make_operation, case_factory):
    body = {"limit": ["A valid integer is required."]}
    assert DRFParser().parse(
        operation=make_operation(method="get", path="/api/users"), body=body, case=case_factory()
    ) == (
        _drf_obs(
            op="GET /api/users",
            location=ParameterLocation.QUERY,
            path=("limit",),
            kind=ObservationKind.TYPE_MISMATCH,
            raw_message="A valid integer is required.",
            payload=TypeMismatchPayload(type_name="integer"),
        ),
    )


def test_drf_parser_parse_skips_unrecognised_messages(make_operation, case_factory):
    body = {"name": ["Custom validate_name message."]}
    assert DRFParser().parse(operation=make_operation(), body=body, case=case_factory()) == ()


def test_drf_parser_parse_non_field_errors_only_yields_empty(make_operation, case_factory):
    body = {"non_field_errors": ["Passwords do not match."]}
    assert DRFParser().parse(operation=make_operation(), body=body, case=case_factory()) == ()


def test_drf_parser_parse_list_with_failing_index(make_operation, case_factory):
    body = {"emails": [{}, {}, {"value": ["Enter a valid email address."]}]}
    assert DRFParser().parse(operation=make_operation(), body=body, case=case_factory()) == (
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


def test_drf_parser_end_to_end_multi_error_per_field(make_operation, case_factory):
    obs = DRFParser().parse(operation=make_operation(), body=_DRF_MULTI_ERROR_BODY, case=case_factory())
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


def test_drf_parser_end_to_end_django_bridge_max_length(make_operation, case_factory):
    obs = DRFParser().parse(operation=make_operation(), body=_DRF_DJANGO_BRIDGE_BODY, case=case_factory())
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


def test_drf_parser_end_to_end_list_index_attribution(make_operation, case_factory):
    obs = DRFParser().parse(operation=make_operation(), body=_DRF_LIST_INDEX_BODY, case=case_factory())
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


def test_drf_parser_end_to_end_nested_serializer(make_operation, case_factory):
    obs = DRFParser().parse(operation=make_operation(), body=_DRF_NESTED_BODY, case=case_factory())
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


def test_drf_parser_end_to_end_integer_min_value(make_operation, case_factory):
    obs = DRFParser().parse(operation=make_operation(), body=_DRF_INTEGER_BODY, case=case_factory())
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


_RAILS_PRESENCE_REQUIRED_MODERN = {
    "email": ["can't be blank", "is invalid"],
    "name": ["can't be blank", "is too short (minimum is 2 characters)"],
    "age": ["is not a number"],
    "role": ["is not included in the list"],
    "password": ["is too short (minimum is 8 characters)"],
    "terms_accepted": ["must be accepted"],
}
_RAILS_PRESENCE_REQUIRED_LEGACY = {
    "errors": [
        "Email can't be blank",
        "Email is invalid",
        "Name can't be blank",
        "Name is too short (minimum is 2 characters)",
        "Age is not a number",
        "Role is not included in the list",
        "Password is too short (minimum is 8 characters)",
        "Terms accepted must be accepted",
    ],
}
_RAILS_LENGTH_TOO_SHORT_MODERN = {"name": ["is too short (minimum is 2 characters)"]}
_RAILS_LENGTH_TOO_SHORT_LEGACY = {"errors": ["Name is too short (minimum is 2 characters)"]}
_RAILS_LENGTH_TOO_LONG_MODERN = {"name": ["is too long (maximum is 50 characters)"]}
_RAILS_LENGTH_TOO_LONG_LEGACY = {"errors": ["Name is too long (maximum is 50 characters)"]}
_RAILS_NUMERICALITY_BELOW_MIN_MODERN = {"age": ["must be greater than or equal to 0"]}
_RAILS_NUMERICALITY_BELOW_MIN_LEGACY = {"errors": ["Age must be greater than or equal to 0"]}
_RAILS_NUMERICALITY_ABOVE_MAX_MODERN = {"age": ["must be less than 130"]}
_RAILS_NUMERICALITY_ABOVE_MAX_LEGACY = {"errors": ["Age must be less than 130"]}
_RAILS_MULTI_FIELD_VIOLATIONS_MODERN = {
    "email": ["is invalid"],
    "name": ["is too short (minimum is 2 characters)"],
    "age": ["is not a number"],
    "role": ["is not included in the list"],
    "reserved_handle": ["is reserved"],
    "password_confirmation": ["doesn't match Password"],
    "password": ["is too short (minimum is 8 characters)"],
    "terms_accepted": ["must be accepted"],
}
_RAILS_MULTI_FIELD_VIOLATIONS_LEGACY = {
    "errors": [
        "Email is invalid",
        "Name is too short (minimum is 2 characters)",
        "Age is not a number",
        "Role is not included in the list",
        "Reserved handle is reserved",
        "Password confirmation doesn't match Password",
        "Password is too short (minimum is 8 characters)",
        "Terms accepted must be accepted",
    ],
}
_RAILS_NESTED_ATTRIBUTES_MODERN = {
    "address.street": ["can't be blank"],
    "address.city": ["can't be blank"],
    "address.zipcode": ["can't be blank", "is invalid"],
}
_RAILS_LENGTH_WRONG_EXACT_MODERN = {"exact_code": ["is the wrong length (should be 6 characters)"]}
_RAILS_LENGTH_ARRAY_TOO_LONG_MODERN = {"tag_list": ["is too long (maximum is 5 characters)"]}
_RAILS_NUMERICALITY_NOT_INTEGER_MODERN = {"age": ["must be an integer"]}


def _wrap_rails_modern(body: dict) -> dict:
    return {"errors": body}


_RAILS_ACCEPTED_BODIES = [
    pytest.param(_RAILS_PRESENCE_REQUIRED_MODERN, id="presence-modern"),
    pytest.param(_RAILS_PRESENCE_REQUIRED_LEGACY, id="presence-legacy"),
    pytest.param(_wrap_rails_modern(_RAILS_PRESENCE_REQUIRED_MODERN), id="presence-wrapped"),
    pytest.param(_RAILS_LENGTH_TOO_SHORT_MODERN, id="length-too-short"),
    pytest.param(_RAILS_LENGTH_TOO_LONG_MODERN, id="length-too-long"),
    pytest.param(_RAILS_LENGTH_WRONG_EXACT_MODERN, id="length-wrong-exact"),
    pytest.param(_RAILS_LENGTH_ARRAY_TOO_LONG_MODERN, id="length-array"),
    pytest.param(_RAILS_NUMERICALITY_BELOW_MIN_MODERN, id="numericality-min"),
    pytest.param(_RAILS_NUMERICALITY_ABOVE_MAX_MODERN, id="numericality-max"),
    pytest.param(_RAILS_NUMERICALITY_NOT_INTEGER_MODERN, id="numericality-not-integer"),
    pytest.param(_RAILS_MULTI_FIELD_VIOLATIONS_MODERN, id="multi-field-modern"),
    pytest.param(_RAILS_MULTI_FIELD_VIOLATIONS_LEGACY, id="multi-field-legacy"),
    pytest.param(_RAILS_NESTED_ATTRIBUTES_MODERN, id="nested-attributes"),
    pytest.param({"email": ["can't be blank"], "name": []}, id="empty-sibling-list"),
    pytest.param({"role": ["is not included in the list"]}, id="vocab-inclusion-only"),
]


@pytest.mark.parametrize("body", _RAILS_ACCEPTED_BODIES)
def test_rails_parser_can_parse_recognises_envelope(body):
    assert RailsParser().can_parse(body=body) is True


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
        {"name": ["This field is required."]},
        {"address": {"zipcode": ["This field is required."]}},
        {
            "message": "The given data was invalid.",
            "errors": {"email": ["The email must be a valid email address."]},
        },
        {
            "type": "...",
            "title": "One or more validation errors occurred.",
            "status": 400,
            "errors": {"Email": ["The Email field is required."]},
        },
        {"messages": ["email - must not be null"]},
        {"detail": [{"loc": ["body", "email"], "msg": "field required", "type": "value_error.missing"}]},
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "top-level-list-of-strings-without-rails-vocab",
        "detail-only",
        "scalar-int-leaf",
        "scalar-bool-leaf",
        "drf-flat",
        "drf-nested",
        "laravel",
        "aspnet-problemdetails",
        "spring-messages",
        "pydantic-detail",
    ],
)
def test_rails_parser_can_parse_rejects_non_rails_bodies(body):
    assert RailsParser().can_parse(body=body) is False


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Email can't be blank", ("email", "can't be blank")),
        ("Name is too short (minimum is 2 characters)", ("name", "is too short (minimum is 2 characters)")),
        ("Terms accepted must be accepted", ("terms_accepted", "must be accepted")),
        ("Password confirmation doesn't match Password", ("password_confirmation", "doesn't match Password")),
        ("Reserved handle is reserved", ("reserved_handle", "is reserved")),
        ("Age is not a number", ("age", "is not a number")),
        ("Age must be greater than or equal to 0", ("age", "must be greater than or equal to 0")),
    ],
    ids=[
        "single-token-blank",
        "single-token-size",
        "two-token-acceptance",
        "two-token-confirmation",
        "two-token-exclusion",
        "single-token-numericality-prose",
        "single-token-numericality-bound",
    ],
)
def test_rails_parser_split_legacy_message(line, expected):
    assert _split_legacy_message(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "nothing recognisable",  # no lead token
        "is invalid",  # leads with a token, no humanised attribute prefix
        "must be accepted",  # same — bare phrasing
    ],
)
def test_rails_parser_split_legacy_message_returns_none(line):
    assert _split_legacy_message(line) is None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            {"email": ["can't be blank"], "base": ["something cross-field"]},
            [(("email",), "can't be blank")],
            id="skips-base-key",
        ),
        pytest.param(
            {"address.street": ["can't be blank"]},
            [(("address", "street"), "can't be blank")],
            id="splits-dotted-key",
        ),
        pytest.param(
            {"email": ["can't be blank", ""]},
            [(("email",), "can't be blank")],
            id="skips-empty-string-message",
        ),
        pytest.param(
            {1: ["ignored"], "email": ["can't be blank"]},
            [(("email",), "can't be blank")],
            id="skips-non-string-key",
        ),
        pytest.param(
            {"email": "not-a-list"},
            [],
            id="skips-non-list-value",
        ),
    ],
)
def test_rails_parser_walk_modern(body, expected):
    assert list(_walk_modern(body)) == expected


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        pytest.param(
            ["Base must not violate constraint", "Email can't be blank"],
            [(("email",), "can't be blank")],
            id="skips-base-message",
        ),
        pytest.param(
            ["nothing recognisable", "Email can't be blank"],
            [(("email",), "can't be blank")],
            id="skips-line-without-lead-token",
        ),
        pytest.param(
            ["", "Email can't be blank"],
            [(("email",), "can't be blank")],
            id="skips-empty-string-line",
        ),
        pytest.param(
            [None, 42, "Email can't be blank"],
            [(("email",), "can't be blank")],
            id="skips-non-string-items",
        ),
    ],
)
def test_rails_parser_walk_legacy(messages, expected):
    assert list(_walk_legacy(messages)) == expected


@pytest.mark.parametrize(
    ("message", "kind", "payload"),
    [
        ("can't be blank", ObservationKind.MUST_NOT_BE_BLANK, None),
        ("can't be empty", ObservationKind.MUST_NOT_BE_BLANK, None),
        ("must be filled", ObservationKind.MUST_NOT_BE_BLANK, None),
        ("must exist", ObservationKind.MUST_NOT_BE_BLANK, None),
        ("is not a number", ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="number")),
        ("must be an integer", ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer")),
    ],
    ids=lambda v: getattr(v, "name", str(v))[:30],
)
def test_rails_parser_classifier_literals(message, kind, payload):
    assert _classify_message(message) == (kind, payload)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param("is too short (minimum is 1 character)", SizeBoundPayload(min=1, max=None), id="min-singular"),
        pytest.param("is too short (minimum is 2 characters)", SizeBoundPayload(min=2, max=None), id="min-plural"),
        pytest.param("is too long (maximum is 1 character)", SizeBoundPayload(min=None, max=1), id="max-singular"),
        pytest.param("is too long (maximum is 50 characters)", SizeBoundPayload(min=None, max=50), id="max-plural"),
        pytest.param(
            "is the wrong length (should be 6 characters)",
            SizeBoundPayload(min=6, max=6),
            id="exact",
        ),
    ],
)
def test_rails_parser_classifier_size_bound(message, expected):
    kind, payload = _classify_message(message)
    assert kind is ObservationKind.SIZE_BOUND
    assert payload == expected


@pytest.mark.parametrize(
    ("message", "bound", "direction", "exclusive"),
    [
        ("must be greater than 0", 0.0, BoundDirection.MIN, True),
        ("must be greater than 0.0", 0.0, BoundDirection.MIN, True),
        ("must be greater than or equal to 0", 0.0, BoundDirection.MIN, False),
        ("must be less than 130", 130.0, BoundDirection.MAX, True),
        ("must be less than or equal to 100", 100.0, BoundDirection.MAX, False),
        ("must be greater than -5", -5.0, BoundDirection.MIN, True),
    ],
    ids=lambda v: str(v)[:30],
)
def test_rails_parser_classifier_numeric_bound(message, bound, direction, exclusive):
    kind, payload = _classify_message(message)
    assert kind is ObservationKind.NUMERIC_BOUND
    assert payload == NumericBoundPayload(bound=bound, direction=direction, exclusive=exclusive)


@pytest.mark.parametrize(
    "message",
    [
        "is invalid",
        "must be accepted",
        "is reserved",
        "is not included in the list",
        "has already been taken",
        "must be even",
        "must be odd",
        "must be other than 5",
        "doesn't match Password",
        "is too long",
        "completely custom application message",
    ],
)
def test_rails_parser_classifier_drops_unactionable_messages(message):
    assert _classify_message(message) is None


@pytest.mark.parametrize(
    ("method", "path", "body", "expected_observations"),
    [
        pytest.param(
            "post",
            "/api/users",
            {"email": ["can't be blank"], "age": ["must be greater than or equal to 0"]},
            (
                ("POST /api/users", ParameterLocation.BODY, ("email",), ObservationKind.MUST_NOT_BE_BLANK, None),
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="modern-shape",
        ),
        pytest.param(
            "post",
            "/api/users",
            {"errors": ["Email can't be blank", "Name is too short (minimum is 2 characters)"]},
            (
                ("POST /api/users", ParameterLocation.BODY, ("email",), ObservationKind.MUST_NOT_BE_BLANK, None),
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("name",),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=2, max=None),
                ),
            ),
            id="legacy-shape",
        ),
        pytest.param(
            "post",
            "/api/users",
            {"errors": {"email": ["can't be blank"]}},
            (("POST /api/users", ParameterLocation.BODY, ("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="ar-wrapped",
        ),
        pytest.param(
            "post",
            "/api/users",
            {
                "address.street": ["can't be blank"],
                "address.zipcode": ["can't be blank", "is invalid"],
            },
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("address", "street"),
                    ObservationKind.MUST_NOT_BE_BLANK,
                    None,
                ),
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("address", "zipcode"),
                    ObservationKind.MUST_NOT_BE_BLANK,
                    None,
                ),
            ),
            id="nested-dotted-path-drops-is-invalid",
        ),
        pytest.param(
            "get",
            "/api/users",
            {"limit": ["must be greater than or equal to 0"]},
            (
                (
                    "GET /api/users",
                    ParameterLocation.QUERY,
                    ("limit",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="get-routes-to-query-location",
        ),
        pytest.param(
            "post",
            "/api/users",
            {"email": ["something custom the application says"]},
            (),
            id="unrecognised-message-yields-empty",
        ),
        pytest.param(
            "post",
            "/api/users",
            {"base": ["something cross-field"]},
            (),
            id="base-key-yields-empty",
        ),
    ],
)
def test_rails_parser_parse(make_operation, method, path, body, expected_observations, case_factory):
    operation = make_operation(method=method, path=path)
    actual = RailsParser().parse(operation=operation, body=body, case=case_factory())
    actual_signatures = tuple((o.operation_label, o.location, o.parameter_path, o.kind, o.payload) for o in actual)
    assert actual_signatures == expected_observations


def _rails_signatures(observations: tuple[Observation, ...]) -> list[tuple]:
    return sorted((o.parameter_path, o.kind.value, o.payload) for o in observations)


@pytest.mark.parametrize(
    ("modern", "legacy"),
    [
        pytest.param(_RAILS_PRESENCE_REQUIRED_MODERN, _RAILS_PRESENCE_REQUIRED_LEGACY, id="presence_required"),
        pytest.param(_RAILS_LENGTH_TOO_SHORT_MODERN, _RAILS_LENGTH_TOO_SHORT_LEGACY, id="length_too_short"),
        pytest.param(_RAILS_LENGTH_TOO_LONG_MODERN, _RAILS_LENGTH_TOO_LONG_LEGACY, id="length_too_long"),
        pytest.param(
            _RAILS_NUMERICALITY_BELOW_MIN_MODERN,
            _RAILS_NUMERICALITY_BELOW_MIN_LEGACY,
            id="numericality_below_min",
        ),
        pytest.param(
            _RAILS_NUMERICALITY_ABOVE_MAX_MODERN,
            _RAILS_NUMERICALITY_ABOVE_MAX_LEGACY,
            id="numericality_above_max",
        ),
        pytest.param(
            _RAILS_MULTI_FIELD_VIOLATIONS_MODERN,
            _RAILS_MULTI_FIELD_VIOLATIONS_LEGACY,
            id="multi_field_violations",
        ),
    ],
)
def test_rails_parser_envelope_shapes_agree_on_observations(make_operation, modern, legacy, case_factory):
    operation = make_operation()
    parser = RailsParser()
    modern_signatures = _rails_signatures(parser.parse(operation=operation, body=modern, case=case_factory()))
    legacy_signatures = _rails_signatures(parser.parse(operation=operation, body=legacy, case=case_factory()))
    wrapped_signatures = _rails_signatures(
        parser.parse(operation=operation, body=_wrap_rails_modern(modern), case=case_factory())
    )
    assert modern_signatures == legacy_signatures == wrapped_signatures


@pytest.mark.parametrize(
    "parser",
    [PydanticParser(), SpringParser(), JacksonParser()],
    ids=lambda p: type(p).__name__,
)
@pytest.mark.parametrize("body", _RAILS_ACCEPTED_BODIES)
def test_other_parsers_reject_rails_bodies(parser, body):
    assert parser.can_parse(body=body) is False


def test_rails_outranks_drf_when_both_claim_a_body():
    body = {"email": ["can't be blank"], "name": ["is too short (minimum is 2 characters)"]}
    assert RailsParser().can_parse(body=body) is True
    assert DRFParser().can_parse(body=body) is True
    assert RailsParser.priority > DRFParser.priority


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
    assert LaravelParser().parse(operation=make_operation(), body={"detail": "nope"}, case=case_factory()) == ()


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


_ASPNET_TYPE_URI = "https://tools.ietf.org/html/rfc9110#section-15.5.1"


def _aspnet_envelope(errors: dict) -> dict:
    return {
        "type": _ASPNET_TYPE_URI,
        "title": "One or more validation errors occurred.",
        "status": 400,
        "errors": errors,
        "traceId": "0HNL96O0F7I3O:00000001",
    }


_ASPNET_REQUIRED = _aspnet_envelope({"Email": ["The Email field is required."]})
_ASPNET_EMAIL_FORMAT = _aspnet_envelope({"Email": ["The Email field is not a valid e-mail address."]})
_ASPNET_STRING_MIN = _aspnet_envelope(
    {"Username": ["The field Username must be a string or array type with a minimum length of '3'."]}
)
_ASPNET_STRING_MAX = _aspnet_envelope(
    {"Username": ["The field Username must be a string or array type with a maximum length of '20'."]}
)
_ASPNET_STRING_LENGTH_RANGE = _aspnet_envelope(
    {"Code": ["The field Code must be a string with a minimum length of 5 and a maximum length of 10."]}
)
_ASPNET_RANGE = _aspnet_envelope({"Age": ["The field Age must be between 0 and 130."]})
_ASPNET_REGEX = _aspnet_envelope({"Slug": ["The field Slug must match the regular expression '^[a-z0-9-]+$'."]})
_ASPNET_FLUENT_NOT_EMPTY = _aspnet_envelope({"Email": ["'Email' must not be empty."]})
_ASPNET_FLUENT_EMAIL = _aspnet_envelope({"Email": ["'Email' is not a valid email address."]})
_ASPNET_FLUENT_MIN_LENGTH = _aspnet_envelope(
    {"Username": ["The length of 'Username' must be at least 3 characters. You entered 1 characters."]}
)
_ASPNET_FLUENT_MAX_LENGTH = _aspnet_envelope(
    {"Username": ["The length of 'Username' must be 20 characters or fewer. You entered 50 characters."]}
)
_ASPNET_FLUENT_GREATER_THAN = _aspnet_envelope({"Score": ["'Score' must be greater than '0'."]})
_ASPNET_FLUENT_LESS_THAN = _aspnet_envelope({"Score": ["'Score' must be less than '100'."]})
_ASPNET_FLUENT_INCLUSIVE_BETWEEN = _aspnet_envelope(
    {"Quantity": ["'Quantity' must be between 1 and 10. You entered 100."]}
)
_ASPNET_TYPE_MISMATCH_DESERIALIZATION = _aspnet_envelope(
    {
        "input": ["The input field is required."],
        "$.age": [
            "The JSON value could not be converted to System.Nullable`1[System.Int32]. "
            "Path: $.age | LineNumber: 0 | BytePositionInLine: 45."
        ],
    }
)
_ASPNET_MULTI_FIELD = _aspnet_envelope(
    {
        "Email": ["The Email field is required.", "The Email field is not a valid e-mail address."],
        "Name": ["The Name field is required."],
    }
)


_ASPNET_ACCEPTED_BODIES = [
    pytest.param(_ASPNET_REQUIRED, id="required"),
    pytest.param(_ASPNET_EMAIL_FORMAT, id="email-format"),
    pytest.param(_ASPNET_STRING_MIN, id="string-min"),
    pytest.param(_ASPNET_STRING_MAX, id="string-max"),
    pytest.param(_ASPNET_STRING_LENGTH_RANGE, id="string-length-range"),
    pytest.param(_ASPNET_RANGE, id="numeric-range"),
    pytest.param(_ASPNET_REGEX, id="regex"),
    pytest.param(_ASPNET_FLUENT_NOT_EMPTY, id="fluent-not-empty"),
    pytest.param(_ASPNET_FLUENT_EMAIL, id="fluent-email"),
    pytest.param(_ASPNET_FLUENT_MIN_LENGTH, id="fluent-min-length"),
    pytest.param(_ASPNET_FLUENT_MAX_LENGTH, id="fluent-max-length"),
    pytest.param(_ASPNET_FLUENT_GREATER_THAN, id="fluent-greater-than"),
    pytest.param(_ASPNET_FLUENT_LESS_THAN, id="fluent-less-than"),
    pytest.param(_ASPNET_FLUENT_INCLUSIVE_BETWEEN, id="fluent-inclusive-between"),
    pytest.param(_ASPNET_TYPE_MISMATCH_DESERIALIZATION, id="type-mismatch-pseudo-fields"),
    pytest.param(_ASPNET_MULTI_FIELD, id="multi-field"),
]


@pytest.mark.parametrize("body", _ASPNET_ACCEPTED_BODIES)
def test_aspnet_parser_can_parse_recognises_envelope(body):
    assert AspNetParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        {"errors": {"Email": ["The Email field is required."]}},
        {"title": "One or more validation errors occurred.", "status": 400},
        _aspnet_envelope({"email": ["Custom validator output."]}),
        {**_aspnet_envelope({}), "errors": {"Email": "The Email field is required."}},
        {**_aspnet_envelope({}), "errors": {"Email": [123]}},
        {"name": ["This field is required."]},
        {"errors": ["Email can't be blank"]},
        {"messages": ["email - must not be null"]},
        {"detail": [{"loc": ["body", "email"], "msg": "field required"}]},
        {"message": "The given data was invalid.", "errors": {"email": ["The email field is required."]}},
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "errors-without-problemdetails-markers",
        "problemdetails-without-errors",
        "envelope-without-aspnet-vocabulary",
        "errors-value-not-list",
        "errors-list-non-string-item",
        "drf",
        "rails-legacy",
        "spring",
        "pydantic",
        "laravel",
    ],
)
def test_aspnet_parser_can_parse_rejects_non_aspnet_bodies(body):
    assert AspNetParser().can_parse(body=body) is False


def _aspnet_signatures(observations: tuple[Observation, ...]) -> list[tuple]:
    return sorted((o.parameter_path, o.kind, o.payload) for o in observations)


@pytest.mark.parametrize(
    ("method", "path", "body", "expected"),
    [
        pytest.param(
            "post",
            "/api/users",
            _ASPNET_REQUIRED,
            (("POST /api/users", ParameterLocation.BODY, ("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="required",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ASPNET_EMAIL_FORMAT,
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
            _ASPNET_STRING_MIN,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("username",),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=3, max=None),
                ),
            ),
            id="string-min",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ASPNET_STRING_MAX,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("username",),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=None, max=20),
                ),
            ),
            id="string-max",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ASPNET_STRING_LENGTH_RANGE,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("code",),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=5, max=10),
                ),
            ),
            id="string-length-range",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ASPNET_RANGE,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=130.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="numeric-range",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ASPNET_REGEX,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("slug",),
                    ObservationKind.PATTERN,
                    PatternPayload(regex="^[a-z0-9-]+$"),
                ),
            ),
            id="regex",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ASPNET_FLUENT_NOT_EMPTY,
            (("POST /api/users", ParameterLocation.BODY, ("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="fluent-not-empty",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ASPNET_FLUENT_EMAIL,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("email",),
                    ObservationKind.FORMAT,
                    FormatPayload(name="email"),
                ),
            ),
            id="fluent-email",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ASPNET_FLUENT_MIN_LENGTH,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("username",),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=3, max=None),
                ),
            ),
            id="fluent-min-length",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ASPNET_FLUENT_MAX_LENGTH,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("username",),
                    ObservationKind.SIZE_BOUND,
                    SizeBoundPayload(min=None, max=20),
                ),
            ),
            id="fluent-max-length",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ASPNET_FLUENT_GREATER_THAN,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
                ),
            ),
            id="fluent-greater-than",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ASPNET_FLUENT_LESS_THAN,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=True),
                ),
            ),
            id="fluent-less-than",
        ),
        pytest.param(
            "post",
            "/api/users",
            _ASPNET_FLUENT_INCLUSIVE_BETWEEN,
            (
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("quantity",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=1.0, direction=BoundDirection.MIN, exclusive=False),
                ),
                (
                    "POST /api/users",
                    ParameterLocation.BODY,
                    ("quantity",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=10.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="fluent-inclusive-between",
        ),
        pytest.param("post", "/api/users", _ASPNET_TYPE_MISMATCH_DESERIALIZATION, (), id="pseudo-fields-dropped"),
        pytest.param(
            "post",
            "/api/users",
            _aspnet_envelope({"email": ["The email field is required."], "Name": ["The Name field is required."]}),
            (
                ("POST /api/users", ParameterLocation.BODY, ("email",), ObservationKind.MUST_NOT_BE_BLANK, None),
                ("POST /api/users", ParameterLocation.BODY, ("name",), ObservationKind.MUST_NOT_BE_BLANK, None),
            ),
            id="already-lowercase-field-passes-through",
        ),
        pytest.param(
            "post",
            "/api/users",
            _aspnet_envelope({"Email": ["", "The Email field is required."], "Name": ["Custom validator output."]}),
            (("POST /api/users", ParameterLocation.BODY, ("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="empty-and-unrecognised-messages-dropped",
        ),
        pytest.param(
            "get",
            "/api/users",
            _ASPNET_REQUIRED,
            (("GET /api/users", ParameterLocation.QUERY, ("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="get-routes-to-query-location",
        ),
    ],
)
def test_aspnet_parser_parse(make_operation, method, path, body, expected, case_factory):
    operation = make_operation(method=method, path=path)
    actual = AspNetParser().parse(operation=operation, body=body, case=case_factory())
    actual_signatures = tuple((o.operation_label, o.location, o.parameter_path, o.kind, o.payload) for o in actual)
    assert actual_signatures == expected


def test_aspnet_parser_parse_multi_field(make_operation, case_factory):
    observations = AspNetParser().parse(operation=make_operation(), body=_ASPNET_MULTI_FIELD, case=case_factory())
    assert _aspnet_signatures(observations) == sorted(
        [
            (("email",), ObservationKind.MUST_NOT_BE_BLANK, None),
            (("email",), ObservationKind.FORMAT, FormatPayload(name="email")),
            (("name",), ObservationKind.MUST_NOT_BE_BLANK, None),
        ]
    )


def test_aspnet_parser_parse_returns_empty_for_non_envelope(make_operation, case_factory):
    assert AspNetParser().parse(operation=make_operation(), body={"detail": "nope"}, case=case_factory()) == ()


@pytest.mark.parametrize(
    "parser",
    [LaravelParser(), RailsParser(), PydanticParser(), SpringParser(), JacksonParser()],
    ids=lambda p: type(p).__name__,
)
@pytest.mark.parametrize("body", _ASPNET_ACCEPTED_BODIES)
def test_other_parsers_reject_aspnet_bodies(parser, body):
    assert parser.can_parse(body=body) is False


def test_aspnet_outranks_drf_when_both_claim_a_body():
    body = _ASPNET_REQUIRED
    assert AspNetParser().can_parse(body=body) is True
    assert DRFParser().can_parse(body=body) is True
    assert AspNetParser.priority > DRFParser.priority


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
    observations = ZodParser().parse(operation=make_operation(), body=_ZOD_MULTI_FIELD, case=case_factory())
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


def test_zod_parser_parse_returns_empty_for_non_envelope(make_operation, case_factory):
    assert ZodParser().parse(operation=make_operation(), body={"detail": "nope"}, case=case_factory()) == ()


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
    actual = ZodParser().parse(operation=make_operation(), body=body, case=case_factory())
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


def _ajv_array_envelope(*errors: dict) -> dict:
    return {"errors": list(errors)}


def _fastify_envelope(message: str) -> dict:
    return {
        "statusCode": 400,
        "code": "FST_ERR_VALIDATION",
        "error": "Bad Request",
        "message": message,
    }


_AJV_FORMAT_EMAIL = _ajv_array_envelope(
    {
        "instancePath": "/email",
        "schemaPath": "#/properties/email/format",
        "keyword": "format",
        "params": {"format": "email"},
        "message": 'must match format "email"',
    }
)
_AJV_FORMAT_URI = _ajv_array_envelope(
    {
        "instancePath": "/url",
        "schemaPath": "#/properties/url/format",
        "keyword": "format",
        "params": {"format": "uri"},
        "message": 'must match format "uri"',
    }
)
_AJV_FORMAT_UUID = _ajv_array_envelope(
    {
        "instancePath": "/token",
        "schemaPath": "#/properties/token/format",
        "keyword": "format",
        "params": {"format": "uuid"},
        "message": 'must match format "uuid"',
    }
)
_AJV_FORMAT_DATETIME = _ajv_array_envelope(
    {
        "instancePath": "/when",
        "schemaPath": "#/properties/when/format",
        "keyword": "format",
        "params": {"format": "date-time"},
        "message": 'must match format "date-time"',
    }
)
_AJV_MIN_LENGTH = _ajv_array_envelope(
    {
        "instancePath": "/username",
        "schemaPath": "#/properties/username/minLength",
        "keyword": "minLength",
        "params": {"limit": 3},
        "message": "must NOT have fewer than 3 characters",
    }
)
_AJV_MAX_LENGTH = _ajv_array_envelope(
    {
        "instancePath": "/username",
        "schemaPath": "#/properties/username/maxLength",
        "keyword": "maxLength",
        "params": {"limit": 20},
        "message": "must NOT have more than 20 characters",
    }
)
_AJV_MIN_ITEMS = _ajv_array_envelope(
    {
        "instancePath": "/tags",
        "schemaPath": "#/properties/tags/minItems",
        "keyword": "minItems",
        "params": {"limit": 1},
        "message": "must NOT have fewer than 1 items",
    }
)
_AJV_MAX_ITEMS = _ajv_array_envelope(
    {
        "instancePath": "/tags",
        "schemaPath": "#/properties/tags/maxItems",
        "keyword": "maxItems",
        "params": {"limit": 5},
        "message": "must NOT have more than 5 items",
    }
)
_AJV_MINIMUM = _ajv_array_envelope(
    {
        "instancePath": "/age",
        "schemaPath": "#/properties/age/minimum",
        "keyword": "minimum",
        "params": {"comparison": ">=", "limit": 0},
        "message": "must be >= 0",
    }
)
_AJV_MAXIMUM = _ajv_array_envelope(
    {
        "instancePath": "/age",
        "schemaPath": "#/properties/age/maximum",
        "keyword": "maximum",
        "params": {"comparison": "<=", "limit": 130},
        "message": "must be <= 130",
    }
)
_AJV_EXCLUSIVE_MINIMUM = _ajv_array_envelope(
    {
        "instancePath": "/score",
        "schemaPath": "#/properties/score/exclusiveMinimum",
        "keyword": "exclusiveMinimum",
        "params": {"comparison": ">", "limit": 0},
        "message": "must be > 0",
    }
)
_AJV_EXCLUSIVE_MAXIMUM = _ajv_array_envelope(
    {
        "instancePath": "/score",
        "schemaPath": "#/properties/score/exclusiveMaximum",
        "keyword": "exclusiveMaximum",
        "params": {"comparison": "<", "limit": 100},
        "message": "must be < 100",
    }
)
_AJV_PATTERN = _ajv_array_envelope(
    {
        "instancePath": "/code",
        "schemaPath": "#/properties/code/pattern",
        "keyword": "pattern",
        "params": {"pattern": "^[A-Z]{3}$"},
        "message": 'must match pattern "^[A-Z]{3}$"',
    }
)
_AJV_ENUM = _ajv_array_envelope(
    {
        "instancePath": "/role",
        "schemaPath": "#/properties/role/enum",
        "keyword": "enum",
        "params": {"allowedValues": ["admin", "user", "guest"]},
        "message": "must be equal to one of the allowed values",
    }
)
_AJV_TYPE = _ajv_array_envelope(
    {
        "instancePath": "/username",
        "schemaPath": "#/properties/username/type",
        "keyword": "type",
        "params": {"type": "string"},
        "message": "must be string",
    }
)
_AJV_ADDITIONAL_PROPERTIES = _ajv_array_envelope(
    {
        "instancePath": "",
        "schemaPath": "#/additionalProperties",
        "keyword": "additionalProperties",
        "params": {"additionalProperty": "unknown"},
        "message": "must NOT have additional properties",
    }
)
_AJV_REQUIRED_ROOT = _ajv_array_envelope(
    {
        "instancePath": "",
        "schemaPath": "#/required",
        "keyword": "required",
        "params": {"missingProperty": "email"},
        "message": "must have required property 'email'",
    }
)
_AJV_REQUIRED_NESTED = _ajv_array_envelope(
    {
        "instancePath": "/user",
        "schemaPath": "#/properties/user/required",
        "keyword": "required",
        "params": {"missingProperty": "email"},
        "message": "must have required property 'email'",
    }
)
_AJV_NESTED_FORMAT = _ajv_array_envelope(
    {
        "instancePath": "/user/email",
        "schemaPath": "#/properties/user/properties/email/format",
        "keyword": "format",
        "params": {"format": "email"},
        "message": 'must match format "email"',
    }
)
_AJV_ARRAY_ELEMENT_TYPE = _ajv_array_envelope(
    {
        "instancePath": "/tags/0",
        "schemaPath": "#/properties/tags/items/type",
        "keyword": "type",
        "params": {"type": "string"},
        "message": "must be string",
    }
)
_AJV_LEGACY_DATAPATH = {
    "errors": [
        {
            "dataPath": ".email",
            "schemaPath": "#/properties/email/format",
            "keyword": "format",
            "params": {"format": "email"},
            "message": 'should match format "email"',
        }
    ]
}
_AJV_MULTI_FIELD = _ajv_array_envelope(
    {
        "instancePath": "/email",
        "schemaPath": "#/properties/email/format",
        "keyword": "format",
        "params": {"format": "email"},
        "message": 'must match format "email"',
    },
    {
        "instancePath": "/username",
        "schemaPath": "#/properties/username/minLength",
        "keyword": "minLength",
        "params": {"limit": 3},
        "message": "must NOT have fewer than 3 characters",
    },
    {
        "instancePath": "/age",
        "schemaPath": "#/properties/age/minimum",
        "keyword": "minimum",
        "params": {"comparison": ">=", "limit": 0},
        "message": "must be >= 0",
    },
)


_FASTIFY_FORMAT = _fastify_envelope('body/email must match format "email"')
_FASTIFY_MIN_LENGTH = _fastify_envelope("body/username must NOT have fewer than 3 characters")
_FASTIFY_MAX_LENGTH = _fastify_envelope("body/username must NOT have more than 20 characters")
_FASTIFY_MIN_ITEMS = _fastify_envelope("body/tags must NOT have fewer than 1 items")
_FASTIFY_MAX_ITEMS = _fastify_envelope("body/tags must NOT have more than 5 items")
_FASTIFY_MINIMUM = _fastify_envelope("body/age must be >= 0")
_FASTIFY_MAXIMUM = _fastify_envelope("body/age must be <= 130")
_FASTIFY_EXCLUSIVE_MINIMUM = _fastify_envelope("body/score must be > 0")
_FASTIFY_EXCLUSIVE_MAXIMUM = _fastify_envelope("body/score must be < 100")
_FASTIFY_PATTERN = _fastify_envelope('body/code must match pattern "^[A-Z]{3}$"')
_FASTIFY_REQUIRED = _fastify_envelope("body must have required property 'email'")
_FASTIFY_REQUIRED_NESTED = _fastify_envelope("body/user must have required property 'email'")
_FASTIFY_NESTED_FORMAT = _fastify_envelope('body/user/email must match format "email"')
_FASTIFY_ENUM = _fastify_envelope("body/role must be equal to one of the allowed values")
_FASTIFY_ADDITIONAL_PROPERTIES = _fastify_envelope("body must NOT have additional properties")
_FASTIFY_MULTI_FIELD = _fastify_envelope(
    'body/email must match format "email", body/username must NOT have fewer than 3 characters, '
    "body/age must be >= 0, body/role must be equal to one of the allowed values, "
    "body/tags must NOT have fewer than 1 items"
)


_AJV_ACCEPTED_BODIES = [
    pytest.param(_AJV_FORMAT_EMAIL, id="ajv-format-email"),
    pytest.param(_AJV_FORMAT_URI, id="ajv-format-uri"),
    pytest.param(_AJV_FORMAT_UUID, id="ajv-format-uuid"),
    pytest.param(_AJV_FORMAT_DATETIME, id="ajv-format-datetime"),
    pytest.param(_AJV_MIN_LENGTH, id="ajv-min-length"),
    pytest.param(_AJV_MAX_LENGTH, id="ajv-max-length"),
    pytest.param(_AJV_MIN_ITEMS, id="ajv-min-items"),
    pytest.param(_AJV_MAX_ITEMS, id="ajv-max-items"),
    pytest.param(_AJV_MINIMUM, id="ajv-minimum"),
    pytest.param(_AJV_MAXIMUM, id="ajv-maximum"),
    pytest.param(_AJV_EXCLUSIVE_MINIMUM, id="ajv-exclusive-minimum"),
    pytest.param(_AJV_EXCLUSIVE_MAXIMUM, id="ajv-exclusive-maximum"),
    pytest.param(_AJV_PATTERN, id="ajv-pattern"),
    pytest.param(_AJV_ENUM, id="ajv-enum"),
    pytest.param(_AJV_TYPE, id="ajv-type"),
    pytest.param(_AJV_ADDITIONAL_PROPERTIES, id="ajv-additional-properties"),
    pytest.param(_AJV_REQUIRED_ROOT, id="ajv-required-root"),
    pytest.param(_AJV_REQUIRED_NESTED, id="ajv-required-nested"),
    pytest.param(_AJV_NESTED_FORMAT, id="ajv-nested-format"),
    pytest.param(_AJV_ARRAY_ELEMENT_TYPE, id="ajv-array-element-type"),
    pytest.param(_AJV_LEGACY_DATAPATH, id="ajv-legacy-datapath"),
    pytest.param(_AJV_MULTI_FIELD, id="ajv-multi-field"),
    pytest.param(_FASTIFY_FORMAT, id="fastify-format"),
    pytest.param(_FASTIFY_MIN_LENGTH, id="fastify-min-length"),
    pytest.param(_FASTIFY_MAX_LENGTH, id="fastify-max-length"),
    pytest.param(_FASTIFY_MIN_ITEMS, id="fastify-min-items"),
    pytest.param(_FASTIFY_MAX_ITEMS, id="fastify-max-items"),
    pytest.param(_FASTIFY_MINIMUM, id="fastify-minimum"),
    pytest.param(_FASTIFY_MAXIMUM, id="fastify-maximum"),
    pytest.param(_FASTIFY_EXCLUSIVE_MINIMUM, id="fastify-exclusive-minimum"),
    pytest.param(_FASTIFY_EXCLUSIVE_MAXIMUM, id="fastify-exclusive-maximum"),
    pytest.param(_FASTIFY_PATTERN, id="fastify-pattern"),
    pytest.param(_FASTIFY_REQUIRED, id="fastify-required"),
    pytest.param(_FASTIFY_REQUIRED_NESTED, id="fastify-required-nested"),
    pytest.param(_FASTIFY_NESTED_FORMAT, id="fastify-nested-format"),
    pytest.param(_FASTIFY_ENUM, id="fastify-enum"),
    pytest.param(_FASTIFY_ADDITIONAL_PROPERTIES, id="fastify-additional-properties"),
    pytest.param(_FASTIFY_MULTI_FIELD, id="fastify-multi-field"),
]


@pytest.mark.parametrize("body", _AJV_ACCEPTED_BODIES)
def test_ajv_parser_can_parse_recognises_envelope(body):
    assert AjvParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        {"errors": []},
        {"errors": [{"code": "invalid_string", "path": ["email"]}]},
        {"errors": [{"keyword": "format"}]},
        {"errors": [{"instancePath": "/x"}]},
        {"statusCode": 500, "error": "Internal Server Error", "message": "Something went wrong"},
        {"statusCode": 400, "error": "Bad Request", "message": "Invalid token"},
        {"name": ["This field is required."]},
        {"detail": [{"loc": ["body", "email"], "msg": "field required"}]},
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "errors-empty-list",
        "zod-issue-shape",
        "ajv-issue-without-instancepath",
        "ajv-issue-without-keyword",
        "fastify-non-validation-status",
        "fastify-message-without-location-prefix",
        "drf",
        "pydantic",
    ],
)
def test_ajv_parser_can_parse_rejects_non_ajv_bodies(body):
    assert AjvParser().can_parse(body=body) is False


def _ajv_signatures(observations: tuple[Observation, ...]) -> list[tuple]:
    return sorted((o.parameter_path, o.kind, o.payload) for o in observations)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            _AJV_FORMAT_EMAIL,
            ((("email",), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="ajv-format-email",
        ),
        pytest.param(
            _AJV_FORMAT_URI,
            ((("url",), ObservationKind.FORMAT, FormatPayload(name="uri")),),
            id="ajv-format-uri",
        ),
        pytest.param(
            _AJV_FORMAT_UUID,
            ((("token",), ObservationKind.FORMAT, FormatPayload(name="uuid")),),
            id="ajv-format-uuid",
        ),
        pytest.param(
            _AJV_FORMAT_DATETIME,
            ((("when",), ObservationKind.FORMAT, FormatPayload(name="date-time")),),
            id="ajv-format-datetime",
        ),
        pytest.param(
            _AJV_MIN_LENGTH,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),),
            id="ajv-min-length",
        ),
        pytest.param(
            _AJV_MAX_LENGTH,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=20)),),
            id="ajv-max-length",
        ),
        pytest.param(
            _AJV_MIN_ITEMS,
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),),
            id="ajv-min-items",
        ),
        pytest.param(
            _AJV_MAX_ITEMS,
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=5)),),
            id="ajv-max-items",
        ),
        pytest.param(
            _AJV_MINIMUM,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="ajv-minimum",
        ),
        pytest.param(
            _AJV_MAXIMUM,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=130.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="ajv-maximum",
        ),
        pytest.param(
            _AJV_EXCLUSIVE_MINIMUM,
            (
                (
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
                ),
            ),
            id="ajv-exclusive-minimum",
        ),
        pytest.param(
            _AJV_EXCLUSIVE_MAXIMUM,
            (
                (
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=True),
                ),
            ),
            id="ajv-exclusive-maximum",
        ),
        pytest.param(
            _AJV_PATTERN,
            ((("code",), ObservationKind.PATTERN, PatternPayload(regex="^[A-Z]{3}$")),),
            id="ajv-pattern",
        ),
        pytest.param(
            _AJV_ENUM,
            ((("role",), ObservationKind.ENUM, EnumPayload(values=("admin", "user", "guest"))),),
            id="ajv-enum",
        ),
        pytest.param(
            _AJV_TYPE,
            ((("username",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="string")),),
            id="ajv-type",
        ),
        pytest.param(_AJV_ADDITIONAL_PROPERTIES, (), id="ajv-additional-properties-dropped"),
        pytest.param(
            _AJV_REQUIRED_ROOT,
            ((("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="ajv-required-root",
        ),
        pytest.param(
            _AJV_REQUIRED_NESTED,
            ((("user", "email"), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="ajv-required-nested",
        ),
        pytest.param(
            _AJV_NESTED_FORMAT,
            ((("user", "email"), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="ajv-nested-format",
        ),
        pytest.param(
            _AJV_ARRAY_ELEMENT_TYPE,
            ((("tags", 0), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="string")),),
            id="ajv-array-element-type",
        ),
        pytest.param(
            _AJV_LEGACY_DATAPATH,
            ((("email",), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="ajv-legacy-datapath",
        ),
        pytest.param(
            _FASTIFY_FORMAT,
            ((("email",), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="fastify-format",
        ),
        pytest.param(
            _FASTIFY_MIN_LENGTH,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),),
            id="fastify-min-length",
        ),
        pytest.param(
            _FASTIFY_MAX_LENGTH,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=20)),),
            id="fastify-max-length",
        ),
        pytest.param(
            _FASTIFY_MIN_ITEMS,
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),),
            id="fastify-min-items",
        ),
        pytest.param(
            _FASTIFY_MAX_ITEMS,
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=5)),),
            id="fastify-max-items",
        ),
        pytest.param(
            _FASTIFY_MINIMUM,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="fastify-minimum",
        ),
        pytest.param(
            _FASTIFY_MAXIMUM,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=130.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="fastify-maximum",
        ),
        pytest.param(
            _FASTIFY_EXCLUSIVE_MINIMUM,
            (
                (
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
                ),
            ),
            id="fastify-exclusive-minimum",
        ),
        pytest.param(
            _FASTIFY_EXCLUSIVE_MAXIMUM,
            (
                (
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=True),
                ),
            ),
            id="fastify-exclusive-maximum",
        ),
        pytest.param(
            _FASTIFY_PATTERN,
            ((("code",), ObservationKind.PATTERN, PatternPayload(regex="^[A-Z]{3}$")),),
            id="fastify-pattern",
        ),
        pytest.param(
            _FASTIFY_REQUIRED,
            ((("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="fastify-required",
        ),
        pytest.param(
            _FASTIFY_REQUIRED_NESTED,
            ((("user", "email"), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="fastify-required-nested",
        ),
        pytest.param(
            _FASTIFY_NESTED_FORMAT,
            ((("user", "email"), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="fastify-nested-format",
        ),
        pytest.param(_FASTIFY_ENUM, (), id="fastify-enum-dropped"),
        pytest.param(_FASTIFY_ADDITIONAL_PROPERTIES, (), id="fastify-additional-properties-dropped"),
    ],
)
def test_ajv_parser_parse(make_operation, body, expected, case_factory):
    operation = make_operation(method="post", path="/api/users")
    actual = AjvParser().parse(operation=operation, body=body, case=case_factory())
    actual_signatures = tuple((o.parameter_path, o.kind, o.payload) for o in actual)
    assert actual_signatures == expected


def test_ajv_parser_parse_multi_field(make_operation, case_factory):
    observations = AjvParser().parse(operation=make_operation(), body=_AJV_MULTI_FIELD, case=case_factory())
    assert _ajv_signatures(observations) == sorted(
        [
            (("email",), ObservationKind.FORMAT, FormatPayload(name="email")),
            (("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),
            (
                ("age",),
                ObservationKind.NUMERIC_BOUND,
                NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
            ),
        ]
    )


def test_ajv_parser_fastify_parse_multi_field_clauses(make_operation, case_factory):
    observations = AjvParser().parse(operation=make_operation(), body=_FASTIFY_MULTI_FIELD, case=case_factory())
    assert _ajv_signatures(observations) == sorted(
        [
            (("email",), ObservationKind.FORMAT, FormatPayload(name="email")),
            (("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),
            (
                ("age",),
                ObservationKind.NUMERIC_BOUND,
                NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
            ),
            (("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),
        ]
    )


def test_ajv_parser_parse_returns_empty_for_non_envelope(make_operation, case_factory):
    assert AjvParser().parse(operation=make_operation(), body={"detail": "nope"}, case=case_factory()) == ()


@pytest.mark.parametrize(
    ("raw_path", "expected_path"),
    [
        (".user.email", ("user", "email")),
        ("[0]", (0,)),
        ("['weird key']", ("weird key",)),
        ("", ()),
    ],
    ids=["dotted", "array-index", "quoted-property", "empty"],
)
def test_ajv_parser_legacy_datapath_segments(make_operation, raw_path, expected_path, case_factory):
    body = {
        "errors": [
            {
                "dataPath": raw_path,
                "keyword": "format",
                "params": {"format": "email"},
                "message": 'must match format "email"',
            }
        ]
    }
    obs = AjvParser().parse(operation=make_operation(), body=body, case=case_factory())
    if expected_path:
        assert obs and obs[0].parameter_path == expected_path
    else:
        assert obs == ()


def test_ajv_parser_fastify_drops_message_without_clauses(make_operation, case_factory):
    body = _fastify_envelope("malformed")
    assert AjvParser().can_parse(body=body) is False
    assert AjvParser().parse(operation=make_operation(), body=body, case=case_factory()) == ()


def test_ajv_parser_get_routes_to_query_location(make_operation, case_factory):
    obs = AjvParser().parse(
        operation=make_operation(method="get", path="/api/users"),
        body=_AJV_FORMAT_EMAIL,
        case=case_factory(),
    )
    assert obs and obs[0].location == ParameterLocation.QUERY


def test_ajv_parser_fastify_query_prefix_routes_to_query_location(make_operation, case_factory):
    body = _fastify_envelope('query/email must match format "email"')
    obs = AjvParser().parse(
        operation=make_operation(method="post", path="/api/users"),
        body=body,
        case=case_factory(),
    )
    assert obs and obs[0].location == ParameterLocation.QUERY and obs[0].parameter_path == ("email",)


@pytest.mark.parametrize(
    "issue",
    [
        {"keyword": "minimum", "instancePath": "/age", "params": {"limit": "abc"}, "message": "must be >= abc"},
        {"keyword": "minLength", "instancePath": "/x", "params": {"limit": True}, "message": "..."},
        {"keyword": "minLength", "instancePath": "/x", "params": {}, "message": "..."},
        {"keyword": "format", "instancePath": "/x", "params": {}, "message": "..."},
        {"keyword": "format", "instancePath": "/x", "params": {"format": 123}, "message": "..."},
        {"keyword": "pattern", "instancePath": "/x", "params": {}, "message": "..."},
        {"keyword": "enum", "instancePath": "/x", "params": {"allowedValues": []}, "message": "..."},
        {"keyword": "enum", "instancePath": "/x", "params": {"allowedValues": [1, 2]}, "message": "..."},
        {"keyword": "type", "instancePath": "/x", "params": {}, "message": "..."},
        {"keyword": "type", "instancePath": "/x", "params": {"type": ["string", "null"]}, "message": "..."},
        {"keyword": "required", "instancePath": "", "params": {}, "message": "..."},
        {"keyword": "required", "instancePath": "", "message": "..."},
        {"keyword": "completely_unknown", "instancePath": "/x", "params": {}, "message": "..."},
        {"keyword": "format", "instancePath": "", "params": {"format": "email"}, "message": "..."},
        {"keyword": "format", "instancePath": "/x", "message": "..."},
        {"keyword": "pattern", "instancePath": "/x", "message": "..."},
        {"keyword": "enum", "instancePath": "/x", "message": "..."},
        {"keyword": "type", "instancePath": "/x", "message": "..."},
        {"keyword": "minLength", "instancePath": "/x", "message": "..."},
        {"keyword": "minimum", "instancePath": "/x", "message": "..."},
    ],
    ids=[
        "non-numeric-limit",
        "bool-limit",
        "missing-limit",
        "missing-format",
        "non-string-format",
        "missing-pattern",
        "empty-enum",
        "non-string-enum",
        "missing-type",
        "list-type-with-null",
        "required-without-missingproperty",
        "required-without-params-key",
        "unknown-keyword",
        "non-required-keyword-with-empty-instance-path",
        "format-without-params-key",
        "pattern-without-params-key",
        "enum-without-params-key",
        "type-without-params-key",
        "size-without-params-key",
        "bound-without-params-key",
    ],
)
def test_ajv_parser_array_form_drops_malformed_issues(make_operation, issue, case_factory):
    body = {
        "errors": [
            issue,
            {
                "instancePath": "/seed",
                "keyword": "format",
                "params": {"format": "email"},
                "message": 'must match format "email"',
            },
        ]
    }
    actual = AjvParser().parse(operation=make_operation(), body=body, case=case_factory())
    actual_paths = tuple(o.parameter_path for o in actual)
    assert actual_paths == (("seed",),)


@pytest.mark.parametrize(
    "parser",
    [AspNetParser(), LaravelParser(), RailsParser(), PydanticParser(), JacksonParser()],
    ids=lambda p: type(p).__name__,
)
@pytest.mark.parametrize("body", _AJV_ACCEPTED_BODIES)
def test_other_parsers_reject_ajv_bodies(parser, body):
    assert parser.can_parse(body=body) is False


def test_ajv_outranks_zod_when_array_signature_collides():
    # Bodies that carry both `keyword` and `code` could match either parser; priority picks AJV.
    mixed = {
        "errors": [
            {
                "instancePath": "/email",
                "keyword": "format",
                "code": "invalid_string",
                "path": ["email"],
                "params": {"format": "email"},
                "message": 'must match format "email"',
            }
        ]
    }
    assert AjvParser().can_parse(body=mixed) is True
    assert ZodParser().can_parse(body=mixed) is True
    assert AjvParser.priority > ZodParser.priority


def _go_default_envelope(message: str) -> dict:
    return {"error": message}


def _go_structured_envelope(*issues: dict) -> dict:
    return {"errors": list(issues)}


_GO_DEFAULT_REQUIRED = _go_default_envelope(
    "Key: 'Body.Email' Error:Field validation for 'Email' failed on the 'required' tag"
)
_GO_DEFAULT_EMAIL = _go_default_envelope(
    "Key: 'Body.Email' Error:Field validation for 'Email' failed on the 'email' tag"
)
_GO_DEFAULT_URL = _go_default_envelope("Key: 'Body.URL' Error:Field validation for 'URL' failed on the 'url' tag")
_GO_DEFAULT_UUID = _go_default_envelope("Key: 'Body.UUID' Error:Field validation for 'UUID' failed on the 'uuid' tag")
_GO_DEFAULT_MIN = _go_default_envelope(
    "Key: 'Body.Username' Error:Field validation for 'Username' failed on the 'min' tag"
)
_GO_DEFAULT_MAX = _go_default_envelope(
    "Key: 'Body.Username' Error:Field validation for 'Username' failed on the 'max' tag"
)
_GO_DEFAULT_GTE = _go_default_envelope("Key: 'Body.Age' Error:Field validation for 'Age' failed on the 'gte' tag")
_GO_DEFAULT_LTE = _go_default_envelope("Key: 'Body.Age' Error:Field validation for 'Age' failed on the 'lte' tag")
_GO_DEFAULT_GT = _go_default_envelope("Key: 'Body.Score' Error:Field validation for 'Score' failed on the 'gt' tag")
_GO_DEFAULT_LT = _go_default_envelope("Key: 'Body.Score' Error:Field validation for 'Score' failed on the 'lt' tag")
_GO_DEFAULT_ONEOF = _go_default_envelope("Key: 'Body.Role' Error:Field validation for 'Role' failed on the 'oneof' tag")
_GO_DEFAULT_DIVE_INDEX = _go_default_envelope(
    "Key: 'Body.Tags[0]' Error:Field validation for 'Tags[0]' failed on the 'required' tag"
)
_GO_DEFAULT_NESTED = _go_default_envelope(
    "Key: 'Body.NestedUser.Email' Error:Field validation for 'Email' failed on the 'email' tag"
)
_GO_DEFAULT_DATETIME = _go_default_envelope(
    "Key: 'Body.When' Error:Field validation for 'When' failed on the 'datetime' tag"
)
_GO_DEFAULT_LEN = _go_default_envelope("Key: 'Body.Code' Error:Field validation for 'Code' failed on the 'len' tag")
_GO_DEFAULT_ALPHANUM = _go_default_envelope(
    "Key: 'Body.Code' Error:Field validation for 'Code' failed on the 'alphanum' tag"
)
_GO_DEFAULT_MULTI_FIELD = _go_default_envelope(
    "Key: 'Body.Email' Error:Field validation for 'Email' failed on the 'email' tag\n"
    "Key: 'Body.Username' Error:Field validation for 'Username' failed on the 'min' tag\n"
    "Key: 'Body.Age' Error:Field validation for 'Age' failed on the 'gte' tag\n"
    "Key: 'Body.Role' Error:Field validation for 'Role' failed on the 'oneof' tag\n"
    "Key: 'Body.Tags' Error:Field validation for 'Tags' failed on the 'min' tag"
)


_GO_STRUCTURED_REQUIRED = _go_structured_envelope(
    {
        "field": "Email",
        "kind": "string",
        "namespace": "Body.Email",
        "param": "",
        "tag": "required",
        "type": "string",
        "value": "",
    }
)
_GO_STRUCTURED_EMAIL = _go_structured_envelope(
    {
        "field": "Email",
        "kind": "string",
        "namespace": "Body.Email",
        "param": "",
        "tag": "email",
        "type": "string",
        "value": "garbage",
    }
)
_GO_STRUCTURED_URL = _go_structured_envelope(
    {
        "field": "URL",
        "kind": "string",
        "namespace": "Body.URL",
        "param": "",
        "tag": "url",
        "type": "string",
        "value": "garbage",
    }
)
_GO_STRUCTURED_UUID = _go_structured_envelope(
    {
        "field": "UUID",
        "kind": "string",
        "namespace": "Body.UUID",
        "param": "",
        "tag": "uuid",
        "type": "string",
        "value": "garbage",
    }
)
_GO_STRUCTURED_DATETIME_DATE = _go_structured_envelope(
    {
        "field": "When",
        "kind": "string",
        "namespace": "Body.When",
        "param": "2006-01-02",
        "tag": "datetime",
        "type": "string",
        "value": "yesterday",
    }
)
_GO_STRUCTURED_DATETIME_DATETIME = _go_structured_envelope(
    {
        "field": "When",
        "kind": "string",
        "namespace": "Body.When",
        "param": "2006-01-02T15:04:05Z",
        "tag": "datetime",
        "type": "string",
        "value": "yesterday",
    }
)
_GO_STRUCTURED_MIN_STRING = _go_structured_envelope(
    {
        "field": "Username",
        "kind": "string",
        "namespace": "Body.Username",
        "param": "3",
        "tag": "min",
        "type": "string",
        "value": "ab",
    }
)
_GO_STRUCTURED_MAX_STRING = _go_structured_envelope(
    {
        "field": "Username",
        "kind": "string",
        "namespace": "Body.Username",
        "param": "20",
        "tag": "max",
        "type": "string",
        "value": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    }
)
_GO_STRUCTURED_MIN_SLICE = _go_structured_envelope(
    {
        "field": "Tags",
        "kind": "slice",
        "namespace": "Body.Tags",
        "param": "1",
        "tag": "min",
        "type": "[]string",
        "value": "[]",
    }
)
_GO_STRUCTURED_MAX_SLICE = _go_structured_envelope(
    {
        "field": "Tags",
        "kind": "slice",
        "namespace": "Body.Tags",
        "param": "5",
        "tag": "max",
        "type": "[]string",
        "value": "[a b c d e f]",
    }
)
_GO_STRUCTURED_GTE = _go_structured_envelope(
    {"field": "Age", "kind": "int", "namespace": "Body.Age", "param": "0", "tag": "gte", "type": "int", "value": "-1"}
)
_GO_STRUCTURED_LTE = _go_structured_envelope(
    {
        "field": "Age",
        "kind": "int",
        "namespace": "Body.Age",
        "param": "130",
        "tag": "lte",
        "type": "int",
        "value": "200",
    }
)
_GO_STRUCTURED_GT = _go_structured_envelope(
    {
        "field": "Score",
        "kind": "float64",
        "namespace": "Body.Score",
        "param": "0",
        "tag": "gt",
        "type": "float64",
        "value": "0",
    }
)
_GO_STRUCTURED_LT = _go_structured_envelope(
    {
        "field": "Score",
        "kind": "float64",
        "namespace": "Body.Score",
        "param": "100",
        "tag": "lt",
        "type": "float64",
        "value": "100",
    }
)
_GO_STRUCTURED_ONEOF = _go_structured_envelope(
    {
        "field": "Role",
        "kind": "string",
        "namespace": "Body.Role",
        "param": "admin user guest",
        "tag": "oneof",
        "type": "string",
        "value": "superuser",
    }
)
_GO_STRUCTURED_LEN = _go_structured_envelope(
    {
        "field": "Code",
        "kind": "string",
        "namespace": "Body.Code",
        "param": "3",
        "tag": "len",
        "type": "string",
        "value": "abcd",
    }
)
_GO_STRUCTURED_DIVE_INDEX = _go_structured_envelope(
    {
        "field": "Tags[0]",
        "kind": "string",
        "namespace": "Body.Tags[0]",
        "param": "",
        "tag": "required",
        "type": "string",
        "value": "",
    }
)
_GO_STRUCTURED_NESTED = _go_structured_envelope(
    {
        "field": "Email",
        "kind": "string",
        "namespace": "Body.NestedUser.Email",
        "param": "",
        "tag": "email",
        "type": "string",
        "value": "garbage",
    }
)
_GO_STRUCTURED_ALPHANUM = _go_structured_envelope(
    {
        "field": "Code",
        "kind": "string",
        "namespace": "Body.Code",
        "param": "",
        "tag": "alphanum",
        "type": "string",
        "value": "a-b",
    }
)
_GO_STRUCTURED_MULTI_FIELD = _go_structured_envelope(
    {
        "field": "Email",
        "kind": "string",
        "namespace": "Body.Email",
        "param": "",
        "tag": "email",
        "type": "string",
        "value": "garbage",
    },
    {
        "field": "Username",
        "kind": "string",
        "namespace": "Body.Username",
        "param": "3",
        "tag": "min",
        "type": "string",
        "value": "ab",
    },
    {"field": "Age", "kind": "int", "namespace": "Body.Age", "param": "0", "tag": "gte", "type": "int", "value": "-1"},
)


_GO_ACCEPTED_BODIES = [
    pytest.param(_GO_DEFAULT_REQUIRED, id="default-required"),
    pytest.param(_GO_DEFAULT_EMAIL, id="default-email"),
    pytest.param(_GO_DEFAULT_URL, id="default-url"),
    pytest.param(_GO_DEFAULT_UUID, id="default-uuid"),
    pytest.param(_GO_DEFAULT_MIN, id="default-min"),
    pytest.param(_GO_DEFAULT_MAX, id="default-max"),
    pytest.param(_GO_DEFAULT_GTE, id="default-gte"),
    pytest.param(_GO_DEFAULT_LTE, id="default-lte"),
    pytest.param(_GO_DEFAULT_GT, id="default-gt"),
    pytest.param(_GO_DEFAULT_LT, id="default-lt"),
    pytest.param(_GO_DEFAULT_ONEOF, id="default-oneof"),
    pytest.param(_GO_DEFAULT_DIVE_INDEX, id="default-dive-index"),
    pytest.param(_GO_DEFAULT_NESTED, id="default-nested"),
    pytest.param(_GO_DEFAULT_DATETIME, id="default-datetime"),
    pytest.param(_GO_DEFAULT_LEN, id="default-len"),
    pytest.param(_GO_DEFAULT_ALPHANUM, id="default-alphanum"),
    pytest.param(_GO_DEFAULT_MULTI_FIELD, id="default-multi-field"),
    pytest.param(_GO_STRUCTURED_REQUIRED, id="structured-required"),
    pytest.param(_GO_STRUCTURED_EMAIL, id="structured-email"),
    pytest.param(_GO_STRUCTURED_URL, id="structured-url"),
    pytest.param(_GO_STRUCTURED_UUID, id="structured-uuid"),
    pytest.param(_GO_STRUCTURED_DATETIME_DATE, id="structured-datetime-date"),
    pytest.param(_GO_STRUCTURED_DATETIME_DATETIME, id="structured-datetime-datetime"),
    pytest.param(_GO_STRUCTURED_MIN_STRING, id="structured-min-string"),
    pytest.param(_GO_STRUCTURED_MAX_STRING, id="structured-max-string"),
    pytest.param(_GO_STRUCTURED_MIN_SLICE, id="structured-min-slice"),
    pytest.param(_GO_STRUCTURED_MAX_SLICE, id="structured-max-slice"),
    pytest.param(_GO_STRUCTURED_GTE, id="structured-gte"),
    pytest.param(_GO_STRUCTURED_LTE, id="structured-lte"),
    pytest.param(_GO_STRUCTURED_GT, id="structured-gt"),
    pytest.param(_GO_STRUCTURED_LT, id="structured-lt"),
    pytest.param(_GO_STRUCTURED_ONEOF, id="structured-oneof"),
    pytest.param(_GO_STRUCTURED_LEN, id="structured-len"),
    pytest.param(_GO_STRUCTURED_DIVE_INDEX, id="structured-dive-index"),
    pytest.param(_GO_STRUCTURED_NESTED, id="structured-nested"),
    pytest.param(_GO_STRUCTURED_ALPHANUM, id="structured-alphanum"),
    pytest.param(_GO_STRUCTURED_MULTI_FIELD, id="structured-multi-field"),
]


@pytest.mark.parametrize("body", _GO_ACCEPTED_BODIES)
def test_go_validator_parser_can_parse_recognises_envelope(body):
    assert GoValidatorParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        {"error": "some unrelated error string"},
        {"error": ""},
        {"errors": []},
        {"errors": [{"keyword": "format", "instancePath": "/x", "params": {}}]},
        {"errors": [{"code": "invalid_string", "path": ["email"]}]},
        {"errors": [{"tag": "email"}]},
        {"errors": [{"tag": "required", "kind": "string", "param": ""}]},
        {"name": ["This field is required."]},
        {"detail": [{"loc": ["body", "email"], "msg": "field required"}]},
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "non-validator-error-string",
        "empty-error-string",
        "errors-empty-list",
        "ajv-shape",
        "zod-shape",
        "issue-without-namespace-or-field",
        "issue-without-namespace-key",
        "drf",
        "pydantic",
    ],
)
def test_go_validator_parser_can_parse_rejects_non_go_bodies(body):
    assert GoValidatorParser().can_parse(body=body) is False


def _go_signatures(observations: tuple[Observation, ...]) -> list[tuple]:
    return sorted((o.parameter_path, o.kind, o.payload) for o in observations)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            _GO_DEFAULT_REQUIRED,
            ((("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="default-required",
        ),
        pytest.param(
            _GO_DEFAULT_EMAIL,
            ((("email",), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="default-email",
        ),
        pytest.param(
            _GO_DEFAULT_URL,
            ((("uRL",), ObservationKind.FORMAT, FormatPayload(name="uri")),),
            id="default-url",
        ),
        pytest.param(
            _GO_DEFAULT_UUID,
            ((("uUID",), ObservationKind.FORMAT, FormatPayload(name="uuid")),),
            id="default-uuid",
        ),
        pytest.param(_GO_DEFAULT_GTE, (), id="default-gte-without-param-dropped"),
        pytest.param(_GO_DEFAULT_MIN, (), id="default-min-without-param-dropped"),
        pytest.param(
            _GO_DEFAULT_DIVE_INDEX,
            ((("tags", 0), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="default-dive-index",
        ),
        pytest.param(
            _GO_DEFAULT_NESTED,
            ((("nestedUser", "email"), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="default-nested",
        ),
        pytest.param(_GO_DEFAULT_ALPHANUM, (), id="default-alphanum-dropped"),
        pytest.param(
            _GO_STRUCTURED_REQUIRED,
            ((("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="structured-required",
        ),
        pytest.param(
            _GO_STRUCTURED_EMAIL,
            ((("email",), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="structured-email",
        ),
        pytest.param(
            _GO_STRUCTURED_URL,
            ((("uRL",), ObservationKind.FORMAT, FormatPayload(name="uri")),),
            id="structured-url",
        ),
        pytest.param(
            _GO_STRUCTURED_UUID,
            ((("uUID",), ObservationKind.FORMAT, FormatPayload(name="uuid")),),
            id="structured-uuid",
        ),
        pytest.param(
            _GO_STRUCTURED_DATETIME_DATE,
            ((("when",), ObservationKind.FORMAT, FormatPayload(name="date")),),
            id="structured-datetime-date",
        ),
        pytest.param(
            _GO_STRUCTURED_DATETIME_DATETIME,
            ((("when",), ObservationKind.FORMAT, FormatPayload(name="date-time")),),
            id="structured-datetime-datetime",
        ),
        pytest.param(
            _GO_STRUCTURED_MIN_STRING,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),),
            id="structured-min-string",
        ),
        pytest.param(
            _GO_STRUCTURED_MAX_STRING,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=20)),),
            id="structured-max-string",
        ),
        pytest.param(
            _GO_STRUCTURED_MIN_SLICE,
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),),
            id="structured-min-slice",
        ),
        pytest.param(
            _GO_STRUCTURED_MAX_SLICE,
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=5)),),
            id="structured-max-slice",
        ),
        pytest.param(
            _GO_STRUCTURED_GTE,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="structured-gte",
        ),
        pytest.param(
            _GO_STRUCTURED_LTE,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=130.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="structured-lte",
        ),
        pytest.param(
            _GO_STRUCTURED_GT,
            (
                (
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
                ),
            ),
            id="structured-gt",
        ),
        pytest.param(
            _GO_STRUCTURED_LT,
            (
                (
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=True),
                ),
            ),
            id="structured-lt",
        ),
        pytest.param(
            _GO_STRUCTURED_ONEOF,
            ((("role",), ObservationKind.ENUM, EnumPayload(values=("admin", "user", "guest"))),),
            id="structured-oneof",
        ),
        pytest.param(
            _GO_STRUCTURED_LEN,
            ((("code",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=3)),),
            id="structured-len",
        ),
        pytest.param(
            _GO_STRUCTURED_DIVE_INDEX,
            ((("tags", 0), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="structured-dive-index",
        ),
        pytest.param(
            _GO_STRUCTURED_NESTED,
            ((("nestedUser", "email"), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="structured-nested",
        ),
        pytest.param(_GO_STRUCTURED_ALPHANUM, (), id="structured-alphanum-dropped"),
        pytest.param(
            _go_structured_envelope(
                {
                    "field": "Age",
                    "kind": "int",
                    "namespace": "Body.Age",
                    "param": "5",
                    "tag": "min",
                    "type": "int",
                    "value": "1",
                }
            ),
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=5.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="min-on-numeric-kind",
        ),
        pytest.param(
            _go_structured_envelope(
                {
                    "field": "email",
                    "kind": "string",
                    "namespace": "Body.email",
                    "param": "",
                    "tag": "email",
                    "type": "string",
                    "value": "garbage",
                }
            ),
            ((("email",), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="already-lowercase-field-passthrough",
        ),
        pytest.param(
            _go_default_envelope("Key: 'Body' Error:Field validation for 'Body' failed on the 'required' tag"),
            (),
            id="default-struct-only-namespace-dropped",
        ),
        pytest.param(
            _go_structured_envelope(
                {
                    "field": "X",
                    "kind": "string",
                    "namespace": "Body.[0]",
                    "param": "",
                    "tag": "email",
                    "type": "string",
                    "value": "g",
                }
            ),
            (),
            id="segment-starting-with-bracket-dropped",
        ),
    ],
)
def test_go_validator_parser_parse(make_operation, body, expected, case_factory):
    operation = make_operation(method="post", path="/api/users")
    actual = GoValidatorParser().parse(operation=operation, body=body, case=case_factory())
    actual_signatures = tuple((o.parameter_path, o.kind, o.payload) for o in actual)
    assert actual_signatures == expected


def test_go_validator_parser_default_multi_field(make_operation, case_factory):
    # Default form lacks `param` — only constraints that don't need it (required/email/url/uuid)
    # survive. `min`/`max`/`gte`/`lte`/`oneof` clauses drop cleanly.
    observations = GoValidatorParser().parse(
        operation=make_operation(), body=_GO_DEFAULT_MULTI_FIELD, case=case_factory()
    )
    assert _go_signatures(observations) == [
        (("email",), ObservationKind.FORMAT, FormatPayload(name="email")),
    ]


def test_go_validator_parser_structured_multi_field(make_operation, case_factory):
    observations = GoValidatorParser().parse(
        operation=make_operation(), body=_GO_STRUCTURED_MULTI_FIELD, case=case_factory()
    )
    assert _go_signatures(observations) == sorted(
        [
            (("email",), ObservationKind.FORMAT, FormatPayload(name="email")),
            (("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),
            (
                ("age",),
                ObservationKind.NUMERIC_BOUND,
                NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
            ),
        ]
    )


def test_go_validator_parser_parse_returns_empty_for_non_envelope(make_operation, case_factory):
    assert GoValidatorParser().parse(operation=make_operation(), body={"detail": "nope"}, case=case_factory()) == ()


def test_go_validator_parser_get_routes_to_query_location(make_operation, case_factory):
    obs = GoValidatorParser().parse(
        operation=make_operation(method="get", path="/api/users"),
        body=_GO_STRUCTURED_EMAIL,
        case=case_factory(),
    )
    assert obs and obs[0].location == ParameterLocation.QUERY


@pytest.mark.parametrize(
    "issue",
    [
        {"namespace": "Body.X", "tag": "min", "kind": "string", "param": ""},
        {"namespace": "Body.X", "tag": "min", "kind": "string", "param": "abc"},
        {"namespace": "Body.X", "tag": "min", "kind": "int", "param": "abc"},
        {"namespace": "Body.X", "tag": "gte", "kind": "int", "param": "abc"},
        {"namespace": "Body.X", "tag": "oneof", "kind": "string", "param": ""},
        {"namespace": "Body.X", "tag": "len", "kind": "string", "param": "abc"},
        {"namespace": "Body.X", "tag": "len", "kind": "string", "param": ""},
        {"namespace": "Body.X", "tag": "datetime", "kind": "string", "param": ""},
        {"namespace": "Body.X", "tag": "completely_unknown", "kind": "string", "param": ""},
        {"namespace": "Body.X", "tag": "min"},
        {"namespace": "Body.X", "tag": "min", "kind": "bool", "param": "5"},
        {"namespace": "Body.X", "tag": "oneof", "kind": "string", "param": "   "},
        {"namespace": "Body.X", "tag": "len", "kind": "string"},
        {"namespace": "", "tag": "required", "kind": "string", "param": ""},
    ],
    ids=[
        "min-empty-param",
        "min-non-numeric-param",
        "numeric-min-non-numeric-param",
        "gte-non-numeric-param",
        "oneof-empty-param",
        "len-non-numeric-param",
        "len-empty-param",
        "datetime-empty-param",
        "unknown-tag",
        "min-without-kind",
        "min-with-non-size-non-numeric-kind",
        "oneof-whitespace-only-param",
        "len-without-param-key",
        "empty-namespace",
    ],
)
def test_go_validator_parser_structured_drops_malformed(make_operation, issue, case_factory):
    body = {
        "errors": [
            issue,
            {
                "field": "Seed",
                "kind": "string",
                "namespace": "Body.Seed",
                "param": "",
                "tag": "email",
                "type": "string",
                "value": "garbage",
            },
        ]
    }
    actual = GoValidatorParser().parse(operation=make_operation(), body=body, case=case_factory())
    actual_paths = tuple(o.parameter_path for o in actual)
    assert actual_paths == (("seed",),)


@pytest.mark.parametrize(
    "parser",
    [AjvParser(), AspNetParser(), LaravelParser(), RailsParser(), PydanticParser(), JacksonParser()],
    ids=lambda p: type(p).__name__,
)
@pytest.mark.parametrize("body", _GO_ACCEPTED_BODIES)
def test_other_parsers_reject_go_bodies(parser, body):
    assert parser.can_parse(body=body) is False


_SYMFONY_NOT_BLANK_CODE = "c1051bb4-d103-4f74-8988-acbcafc7fdc3"
_SYMFONY_EMAIL_CODE = "bd79c0ab-ddba-46cc-a703-a7a4b08de310"
_SYMFONY_URL_CODE = "57c2f299-1154-4870-89bb-ef3b1f5ad229"
_SYMFONY_UUID_CODE = "51120b12-a2bc-41bf-aa53-cd73daf330d0"
_SYMFONY_DATE_CODE = "69819696-02ac-4a99-9ff0-14e127c4d1bc"
_SYMFONY_DATETIME_CODE = "1a9da513-2640-4f84-9b6a-4d99dcddc628"
_SYMFONY_LENGTH_MIN_CODE = "9ff3fdc4-b214-49db-8718-39c315e33d45"
_SYMFONY_LENGTH_MAX_CODE = "d94b19cc-114f-4f44-9cc4-4138e80a87b9"
_SYMFONY_GTE_CODE = "ea4e51d1-3342-48bd-87f1-9e672cd90cad"
_SYMFONY_LTE_CODE = "30fbb013-d015-4232-8b3b-8f3be97a7e14"
_SYMFONY_GT_CODE = "778b7ae0-84d3-481a-9dec-35fdb64b1d78"
_SYMFONY_LT_CODE = "079d7420-2d13-460c-8756-de810eeb37d2"
_SYMFONY_RANGE_CODE = "04b91c99-a946-4221-afc5-e65ebac401eb"
_SYMFONY_CHOICE_CODE = "8e179f1b-97aa-4560-a02f-2a8b42e49df7"
_SYMFONY_REGEX_CODE = "de1e3db3-5ed4-4941-aae4-59f3667cc3a3"
_SYMFONY_TYPE_CODE = "ba785a8c-82cb-4283-967c-3cf342181b40"
_SYMFONY_COUNT_MIN_CODE = "bef8e338-6ae5-4caf-b8e2-50e7b0579e69"
_SYMFONY_COUNT_MAX_CODE = "756b1212-697c-468d-a9ad-50dd783bb169"


def _symfony_default(*violations: dict) -> list[dict]:
    return list(violations)


def _symfony_api_platform(*violations: dict) -> dict:
    return {
        "type": "https://symfony.com/errors/validation",
        "title": "Validation Failed",
        "detail": " | ".join(v.get("title", v.get("message", "")) for v in violations),
        "violations": list(violations),
    }


def _violation(property_path: str, code: str, message: str = "", **parameters: object) -> dict:
    return {
        "propertyPath": property_path,
        "message": message or "validation failed",
        "code": code,
        "parameters": {f"{{{{ {k} }}}}": v for k, v in parameters.items()},
    }


def _api_platform_violation(property_path: str, code: str, **parameters: object) -> dict:
    return {
        "propertyPath": property_path,
        "title": "validation failed",
        "template": "validation failed",
        "type": f"urn:uuid:{code}",
        "parameters": {f"{{{{ {k} }}}}": v for k, v in parameters.items()},
    }


_SYMFONY_DEFAULT_NOT_BLANK = _symfony_default(_violation("email", _SYMFONY_NOT_BLANK_CODE))
_SYMFONY_DEFAULT_EMAIL = _symfony_default(_violation("email", _SYMFONY_EMAIL_CODE))
_SYMFONY_DEFAULT_URL = _symfony_default(_violation("url", _SYMFONY_URL_CODE))
_SYMFONY_DEFAULT_UUID = _symfony_default(_violation("token", _SYMFONY_UUID_CODE))
_SYMFONY_DEFAULT_DATE = _symfony_default(_violation("when", _SYMFONY_DATE_CODE))
_SYMFONY_DEFAULT_DATETIME = _symfony_default(_violation("started", _SYMFONY_DATETIME_CODE, format='"Y-m-d H:i:s"'))
_SYMFONY_DEFAULT_LENGTH_MIN = _symfony_default(
    _violation("username", _SYMFONY_LENGTH_MIN_CODE, max="20", value='"ab"', limit="3", min="3", value_length="2")
)
_SYMFONY_DEFAULT_LENGTH_MAX = _symfony_default(
    _violation("username", _SYMFONY_LENGTH_MAX_CODE, min="3", limit="20", max="20", value_length="50")
)
_SYMFONY_DEFAULT_GTE = _symfony_default(_violation("age", _SYMFONY_GTE_CODE, value="-1", compared_value="0"))
_SYMFONY_DEFAULT_LTE = _symfony_default(_violation("age", _SYMFONY_LTE_CODE, value="200", compared_value="130"))
_SYMFONY_DEFAULT_GT = _symfony_default(_violation("score", _SYMFONY_GT_CODE, value="0", compared_value="0"))
_SYMFONY_DEFAULT_LT = _symfony_default(_violation("score", _SYMFONY_LT_CODE, value="100", compared_value="100"))
_SYMFONY_DEFAULT_RANGE = _symfony_default(_violation("quantity", _SYMFONY_RANGE_CODE, value="200", min="1", max="100"))
_SYMFONY_DEFAULT_CHOICE = _symfony_default(
    _violation("role", _SYMFONY_CHOICE_CODE, value='"superuser"', choices='"admin", "user", "guest"')
)
_SYMFONY_DEFAULT_REGEX = _symfony_default(
    _violation("code", _SYMFONY_REGEX_CODE, value='"lower"', pattern="/^[A-Z]{3}$/")
)
_SYMFONY_DEFAULT_TYPE = _symfony_default(_violation("count", _SYMFONY_TYPE_CODE, value='"twenty"', type="integer"))
_SYMFONY_DEFAULT_COUNT_MIN = _symfony_default(_violation("tags", _SYMFONY_COUNT_MIN_CODE, count="0", limit="1"))
_SYMFONY_DEFAULT_COUNT_MAX = _symfony_default(_violation("tags", _SYMFONY_COUNT_MAX_CODE, count="6", limit="5"))
_SYMFONY_DEFAULT_NESTED_PATH = _symfony_default(_violation("user.email", _SYMFONY_EMAIL_CODE))
_SYMFONY_DEFAULT_ARRAY_INDEX = _symfony_default(_violation("tags[0]", _SYMFONY_NOT_BLANK_CODE))
_SYMFONY_DEFAULT_MULTI_FIELD = _symfony_default(
    _violation("email", _SYMFONY_EMAIL_CODE),
    _violation("username", _SYMFONY_LENGTH_MIN_CODE, limit="3", min="3", max="20"),
    _violation("name", _SYMFONY_NOT_BLANK_CODE),
    _violation("age", _SYMFONY_GTE_CODE, compared_value="0"),
)


_SYMFONY_API_NOT_BLANK = _symfony_api_platform(_api_platform_violation("email", _SYMFONY_NOT_BLANK_CODE))
_SYMFONY_API_EMAIL = _symfony_api_platform(_api_platform_violation("email", _SYMFONY_EMAIL_CODE))
_SYMFONY_API_LENGTH_MIN = _symfony_api_platform(
    _api_platform_violation("username", _SYMFONY_LENGTH_MIN_CODE, limit="3", min="3", max="20")
)
_SYMFONY_API_GTE = _symfony_api_platform(_api_platform_violation("age", _SYMFONY_GTE_CODE, compared_value="0"))
_SYMFONY_API_RANGE = _symfony_api_platform(_api_platform_violation("quantity", _SYMFONY_RANGE_CODE, min="1", max="100"))
_SYMFONY_API_CHOICE = _symfony_api_platform(
    _api_platform_violation("role", _SYMFONY_CHOICE_CODE, choices='"admin", "user", "guest"')
)
_SYMFONY_API_REGEX = _symfony_api_platform(_api_platform_violation("code", _SYMFONY_REGEX_CODE, pattern="/^[A-Z]{3}$/"))
_SYMFONY_API_MULTI_FIELD = _symfony_api_platform(
    _api_platform_violation("email", _SYMFONY_EMAIL_CODE),
    _api_platform_violation("username", _SYMFONY_LENGTH_MIN_CODE, limit="3", min="3", max="20"),
    _api_platform_violation("name", _SYMFONY_NOT_BLANK_CODE),
    _api_platform_violation("tags", _SYMFONY_COUNT_MIN_CODE, count="0", limit="1"),
)


_SYMFONY_ACCEPTED_BODIES = [
    pytest.param(_SYMFONY_DEFAULT_NOT_BLANK, id="default-not-blank"),
    pytest.param(_SYMFONY_DEFAULT_EMAIL, id="default-email"),
    pytest.param(_SYMFONY_DEFAULT_URL, id="default-url"),
    pytest.param(_SYMFONY_DEFAULT_UUID, id="default-uuid"),
    pytest.param(_SYMFONY_DEFAULT_DATE, id="default-date"),
    pytest.param(_SYMFONY_DEFAULT_DATETIME, id="default-datetime"),
    pytest.param(_SYMFONY_DEFAULT_LENGTH_MIN, id="default-length-min"),
    pytest.param(_SYMFONY_DEFAULT_LENGTH_MAX, id="default-length-max"),
    pytest.param(_SYMFONY_DEFAULT_GTE, id="default-gte"),
    pytest.param(_SYMFONY_DEFAULT_LTE, id="default-lte"),
    pytest.param(_SYMFONY_DEFAULT_GT, id="default-gt"),
    pytest.param(_SYMFONY_DEFAULT_LT, id="default-lt"),
    pytest.param(_SYMFONY_DEFAULT_RANGE, id="default-range"),
    pytest.param(_SYMFONY_DEFAULT_CHOICE, id="default-choice"),
    pytest.param(_SYMFONY_DEFAULT_REGEX, id="default-regex"),
    pytest.param(_SYMFONY_DEFAULT_TYPE, id="default-type"),
    pytest.param(_SYMFONY_DEFAULT_COUNT_MIN, id="default-count-min"),
    pytest.param(_SYMFONY_DEFAULT_COUNT_MAX, id="default-count-max"),
    pytest.param(_SYMFONY_DEFAULT_NESTED_PATH, id="default-nested-path"),
    pytest.param(_SYMFONY_DEFAULT_ARRAY_INDEX, id="default-array-index"),
    pytest.param(_SYMFONY_DEFAULT_MULTI_FIELD, id="default-multi-field"),
    pytest.param(_SYMFONY_API_NOT_BLANK, id="api-not-blank"),
    pytest.param(_SYMFONY_API_EMAIL, id="api-email"),
    pytest.param(_SYMFONY_API_LENGTH_MIN, id="api-length-min"),
    pytest.param(_SYMFONY_API_GTE, id="api-gte"),
    pytest.param(_SYMFONY_API_RANGE, id="api-range"),
    pytest.param(_SYMFONY_API_CHOICE, id="api-choice"),
    pytest.param(_SYMFONY_API_REGEX, id="api-regex"),
    pytest.param(_SYMFONY_API_MULTI_FIELD, id="api-multi-field"),
]


@pytest.mark.parametrize("body", _SYMFONY_ACCEPTED_BODIES)
def test_symfony_parser_can_parse_recognises_envelope(body):
    assert SymfonyParser().can_parse(body=body) is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        None,
        "",
        [],
        [{"propertyPath": "email"}],
        [{"propertyPath": "email", "code": ""}],
        [{"message": "no propertyPath"}],
        {"violations": []},
        {"violations": [{"propertyPath": "email"}]},
        {"name": ["This field is required."]},
        {"detail": [{"loc": ["body", "email"], "msg": "field required"}]},
        {"errors": [{"keyword": "format", "instancePath": "/x", "params": {}}]},
        {"errors": [{"code": "invalid_string", "path": ["email"]}]},
    ],
    ids=[
        "empty-dict",
        "none",
        "empty-string",
        "empty-list",
        "violation-without-code",
        "violation-with-empty-code",
        "violation-without-property-path",
        "violations-empty-list",
        "violations-no-code-or-type",
        "drf",
        "pydantic",
        "ajv-shape",
        "zod-shape",
    ],
)
def test_symfony_parser_can_parse_rejects_non_symfony_bodies(body):
    assert SymfonyParser().can_parse(body=body) is False


def _symfony_signatures(observations: tuple[Observation, ...]) -> list[tuple]:
    return sorted((o.parameter_path, o.kind, o.payload) for o in observations)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            _SYMFONY_DEFAULT_NOT_BLANK,
            ((("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="default-not-blank",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_EMAIL,
            ((("email",), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="default-email",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_URL,
            ((("url",), ObservationKind.FORMAT, FormatPayload(name="uri")),),
            id="default-url",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_UUID,
            ((("token",), ObservationKind.FORMAT, FormatPayload(name="uuid")),),
            id="default-uuid",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_DATE,
            ((("when",), ObservationKind.FORMAT, FormatPayload(name="date")),),
            id="default-date",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_DATETIME,
            ((("started",), ObservationKind.FORMAT, FormatPayload(name="date-time")),),
            id="default-datetime",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_LENGTH_MIN,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),),
            id="default-length-min",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_LENGTH_MAX,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=20)),),
            id="default-length-max",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_GTE,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="default-gte",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_LTE,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=130.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="default-lte",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_GT,
            (
                (
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
                ),
            ),
            id="default-gt",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_LT,
            (
                (
                    ("score",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=True),
                ),
            ),
            id="default-lt",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_RANGE,
            (
                (
                    ("quantity",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=1.0, direction=BoundDirection.MIN, exclusive=False),
                ),
                (
                    ("quantity",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=False),
                ),
            ),
            id="default-range",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_CHOICE,
            ((("role",), ObservationKind.ENUM, EnumPayload(values=("admin", "user", "guest"))),),
            id="default-choice",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_REGEX,
            ((("code",), ObservationKind.PATTERN, PatternPayload(regex="^[A-Z]{3}$")),),
            id="default-regex",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_TYPE,
            ((("count",), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer")),),
            id="default-type",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_COUNT_MIN,
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),),
            id="default-count-min",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_COUNT_MAX,
            ((("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=5)),),
            id="default-count-max",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_NESTED_PATH,
            ((("user", "email"), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="default-nested-path",
        ),
        pytest.param(
            _SYMFONY_DEFAULT_ARRAY_INDEX,
            ((("tags", 0), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="default-array-index",
        ),
        pytest.param(
            _SYMFONY_API_NOT_BLANK,
            ((("email",), ObservationKind.MUST_NOT_BE_BLANK, None),),
            id="api-not-blank",
        ),
        pytest.param(
            _SYMFONY_API_EMAIL,
            ((("email",), ObservationKind.FORMAT, FormatPayload(name="email")),),
            id="api-email",
        ),
        pytest.param(
            _SYMFONY_API_LENGTH_MIN,
            ((("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),),
            id="api-length-min",
        ),
        pytest.param(
            _SYMFONY_API_GTE,
            (
                (
                    ("age",),
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
                ),
            ),
            id="api-gte",
        ),
        pytest.param(
            _SYMFONY_API_CHOICE,
            ((("role",), ObservationKind.ENUM, EnumPayload(values=("admin", "user", "guest"))),),
            id="api-choice",
        ),
        pytest.param(
            _SYMFONY_API_REGEX,
            ((("code",), ObservationKind.PATTERN, PatternPayload(regex="^[A-Z]{3}$")),),
            id="api-regex",
        ),
        pytest.param(
            [_violation("rank", _SYMFONY_CHOICE_CODE, value="0", choices="1, 2, 3")],
            ((("rank",), ObservationKind.ENUM, EnumPayload(values=("1", "2", "3"))),),
            id="choice-with-unquoted-integer-values",
        ),
    ],
)
def test_symfony_parser_parse(make_operation, body, expected, case_factory):
    operation = make_operation(method="post", path="/api/users")
    actual = SymfonyParser().parse(operation=operation, body=body, case=case_factory())
    actual_signatures = tuple((o.parameter_path, o.kind, o.payload) for o in actual)
    assert actual_signatures == expected


def test_symfony_parser_default_multi_field(make_operation, case_factory):
    observations = SymfonyParser().parse(
        operation=make_operation(), body=_SYMFONY_DEFAULT_MULTI_FIELD, case=case_factory()
    )
    assert _symfony_signatures(observations) == sorted(
        [
            (("email",), ObservationKind.FORMAT, FormatPayload(name="email")),
            (("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),
            (("name",), ObservationKind.MUST_NOT_BE_BLANK, None),
            (
                ("age",),
                ObservationKind.NUMERIC_BOUND,
                NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
            ),
        ]
    )


def test_symfony_parser_api_platform_multi_field(make_operation, case_factory):
    observations = SymfonyParser().parse(operation=make_operation(), body=_SYMFONY_API_MULTI_FIELD, case=case_factory())
    assert _symfony_signatures(observations) == sorted(
        [
            (("email",), ObservationKind.FORMAT, FormatPayload(name="email")),
            (("username",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=3, max=None)),
            (("name",), ObservationKind.MUST_NOT_BE_BLANK, None),
            (("tags",), ObservationKind.SIZE_BOUND, SizeBoundPayload(min=1, max=None)),
        ]
    )


def test_symfony_parser_parse_returns_empty_for_non_envelope(make_operation, case_factory):
    assert SymfonyParser().parse(operation=make_operation(), body={"detail": "nope"}, case=case_factory()) == ()


def test_symfony_parser_get_routes_to_query_location(make_operation, case_factory):
    obs = SymfonyParser().parse(
        operation=make_operation(method="get", path="/api/users"),
        body=_SYMFONY_DEFAULT_EMAIL,
        case=case_factory(),
    )
    assert obs and obs[0].location == ParameterLocation.QUERY


@pytest.mark.parametrize(
    "violation",
    [
        _violation("name", "11111111-2222-3333-4444-555555555555"),
        _violation("name", _SYMFONY_LENGTH_MIN_CODE),
        _violation("name", _SYMFONY_LENGTH_MIN_CODE, limit="abc"),
        _violation("name", _SYMFONY_GTE_CODE),
        _violation("name", _SYMFONY_GTE_CODE, compared_value="abc"),
        _violation("name", _SYMFONY_RANGE_CODE),
        _violation("name", _SYMFONY_RANGE_CODE, min="1"),
        _violation("name", _SYMFONY_RANGE_CODE, min="abc", max="100"),
        _violation("name", _SYMFONY_CHOICE_CODE),
        _violation("name", _SYMFONY_REGEX_CODE),
        _violation("name", _SYMFONY_TYPE_CODE),
        {**_violation("name", _SYMFONY_LENGTH_MIN_CODE, limit="3"), "parameters": "not a dict"},
        _violation("", _SYMFONY_NOT_BLANK_CODE),
        {"propertyPath": "name", "message": "no code field"},
    ],
    ids=[
        "unknown-code",
        "length-without-limit",
        "length-non-integer-limit",
        "gte-without-compared-value",
        "gte-non-numeric-compared-value",
        "range-without-min-max",
        "range-only-min",
        "range-non-numeric-min",
        "choice-without-choices-param",
        "regex-without-pattern-param",
        "type-without-type-param",
        "non-dict-parameters",
        "empty-property-path",
        "no-code-or-type-key",
    ],
)
def test_symfony_parser_drops_malformed_violation(make_operation, violation, case_factory):
    body = [violation, _violation("seed", _SYMFONY_EMAIL_CODE)]
    actual = SymfonyParser().parse(operation=make_operation(), body=body, case=case_factory())
    actual_paths = tuple(o.parameter_path for o in actual)
    assert actual_paths == (("seed",),)


@pytest.mark.parametrize(
    "parser",
    [
        AjvParser(),
        AspNetParser(),
        GoValidatorParser(),
        LaravelParser(),
        RailsParser(),
        PydanticParser(),
        JacksonParser(),
        ZodParser(),
    ],
    ids=lambda p: type(p).__name__,
)
@pytest.mark.parametrize("body", _SYMFONY_ACCEPTED_BODIES)
def test_other_parsers_reject_symfony_bodies(parser, body):
    assert parser.can_parse(body=body) is False


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
    obs = JacksonParser().parse(operation=make_operation(), body=body, case=case_factory())
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (expected_path, ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name=expected_type)),
    ]


def test_jackson_parser_emits_both_type_and_enum_for_enum_message(make_operation, case_factory):
    body = {"msg": _JACKSON_ENUM_USERTYPE}
    obs = JacksonParser().parse(operation=make_operation(), body=body, case=case_factory())
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
    obs = JacksonParser().parse(operation=make_operation(), body=body, case=case_factory())
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
    obs = JacksonParser().parse(operation=make_operation(), body={"msg": message}, case=case_factory())
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
    assert JacksonParser().parse(operation=make_operation(), body=body, case=case_factory()) == ()


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
    assert JacksonParser().parse(operation=make_operation(), body=body, case=case_factory()) == ()


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
    obs = JacksonParser().parse(operation=make_operation(), body=body, case=case_factory())
    assert [o.payload for o in obs] == [TypeMismatchPayload(type_name="java.time.LocalDate")]


def test_jackson_parser_skips_non_dict_array_items(make_operation, case_factory):
    body = {"errors": ["string-item", 123, None, {"message": _JACKSON_LOCAL_DATE}]}
    obs = JacksonParser().parse(operation=make_operation(), body=body, case=case_factory())
    assert [o.payload for o in obs] == [TypeMismatchPayload(type_name="java.time.LocalDate")]


def test_jackson_parser_extracts_one_observation_per_carrier_key(make_operation, case_factory):
    # Different carrier keys can each carry a Jackson error — `_carrier_strings`
    # walks them in order and emits one observation per match.
    body = {
        "msg": _JACKSON_LOCAL_DATE,
        "detail": _JACKSON_UUID,
    }
    obs = JacksonParser().parse(operation=make_operation(), body=body, case=case_factory())
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
    obs = JacksonParser().parse(operation=make_operation(), body={"msg": _JACKSON_OVERFLOW_INT}, case=case_factory())
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
    obs = JacksonParser().parse(operation=make_operation(), body={"msg": message}, case=case_factory())
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
    obs = SpringParser().parse(operation=make_operation(), body=json.loads(body), case=case_factory())
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
    obs = SpringParser().parse(operation=make_operation(), body=body, case=case_factory())
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
    obs = SpringParser().parse(operation=make_operation(), body=body, case=case_factory())
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
    obs = SpringParser().parse(operation=make_operation(), body=body, case=case_factory())
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
    assert SpringParser().parse(operation=make_operation(), body=body, case=case_factory()) == ()


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
    obs = SpringParser().parse(operation=make_operation(), body=body, case=case_factory())
    assert [o.parameter_path for o in obs] == [("valid",), ("another",)]


@pytest.mark.parametrize(
    "body",
    [[1, 2, 3], "not a dict", None, 42, 1.5, True],
    ids=["list", "string", "none", "int", "float", "bool"],
)
def test_spring_parser_returns_empty_for_non_dict_body(body, make_operation, case_factory):
    assert SpringParser().parse(operation=make_operation(), body=body, case=case_factory()) == ()


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
def test_pydantic_parser_extracts_observation(entry, expected, make_operation, case_factory):
    obs = PydanticParser().parse(operation=make_operation(), body={"detail": [entry]}, case=case_factory())
    assert obs == (expected,)


def test_pydantic_parser_coerces_decimal_numeric_bounds(make_operation, case_factory):
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
    obs = PydanticParser().parse(operation=make_operation(), body=body, case=case_factory())
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
def test_pydantic_parser_maps_loc_prefix_to_location(loc_prefix, expected_location, make_operation, case_factory):
    body = {"detail": [{"type": "missing", "loc": [loc_prefix, "x"], "msg": "Field required"}]}
    obs = PydanticParser().parse(operation=make_operation(), body=body, case=case_factory())
    assert len(obs) == 1
    assert obs[0].location == expected_location
    assert obs[0].parameter_path == ("x",)


def test_pydantic_parser_defaults_to_body_when_loc_prefix_unrecognized(make_operation, case_factory):
    body = {"detail": [{"type": "missing", "loc": ["unknown", "x"], "msg": "Field required"}]}
    obs = PydanticParser().parse(operation=make_operation(), body=body, case=case_factory())
    assert obs[0].location == ParameterLocation.BODY
    assert obs[0].parameter_path == ("unknown", "x")


def test_pydantic_parser_handles_int_path_segments(make_operation, case_factory):
    # FastAPI emits int segments for list-element validation failures.
    body = {"detail": [{"type": "missing", "loc": ["body", "items", 0, "qty"], "msg": "Field required"}]}
    obs = PydanticParser().parse(operation=make_operation(), body=body, case=case_factory())
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
def test_pydantic_parser_parse_returns_empty_for_uninteresting_bodies(body, make_operation, case_factory):
    assert PydanticParser().parse(operation=make_operation(), body=body, case=case_factory()) == ()


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
def test_pydantic_parser_skips_handler_with_invalid_context(type_code, context, make_operation, case_factory):
    body = {"detail": [{"type": type_code, "loc": ["body", "x"], "msg": "...", "ctx": context}]}
    assert PydanticParser().parse(operation=make_operation(), body=body, case=case_factory()) == ()


def test_pydantic_parser_skips_non_dict_detail_entry(make_operation, case_factory):
    body = {"detail": [42, {"type": "missing", "loc": ["body", "x"], "msg": "Field required"}]}
    obs = PydanticParser().parse(operation=make_operation(), body=body, case=case_factory())
    assert obs[0].parameter_path == ("x",)
    assert len(obs) == 1


def test_pydantic_parser_emits_one_observation_per_detail_entry(make_operation, case_factory):
    body = {
        "detail": [
            {"type": "missing", "loc": ["body", "name"], "msg": "Field required"},
            {"type": "string_too_short", "loc": ["body", "code"], "msg": "...", "ctx": {"min_length": 3}},
        ]
    }
    obs = PydanticParser().parse(operation=make_operation(), body=body, case=case_factory())
    assert [(o.parameter_path, o.kind) for o in obs] == [
        (("name",), ObservationKind.MUST_NOT_BE_BLANK),
        (("code",), ObservationKind.SIZE_BOUND),
    ]


def test_pipeline_forwards_case_to_parser(case_factory, response_factory):
    received: list[Case] = []

    class _RecordingSpringParser(SpringParser):
        def parse(self, *, operation, body, case):
            received.append(case)
            return super().parse(operation=operation, body=body, case=case)

    pipeline = FeedbackPipeline([_RecordingSpringParser()])
    case = case_factory()
    response = Response.from_any(
        response_factory.requests(
            content=SPRING_MESSAGES,
            content_type="application/json",
            status_code=400,
        )
    )
    pipeline.parse(operation=case.operation, case=case, response=response)
    assert received == [case]


def _record(store, *, operation, case, body, response_factory):
    _reset_pipeline_for_tests()
    response = Response.from_any(
        response_factory.requests(
            content=json.dumps(body).encode(),
            content_type="application/json",
            status_code=400,
        )
    )
    record_response(store=store, operation=operation, case=case, response=response)


@pytest.mark.parametrize(
    ("operation_path", "case_kwargs", "rejected_value", "type_name", "expected_location", "expected_path"),
    [
        # Two body fields share the rejected value -> ambiguous, no observation.
        (
            "/api/items",
            {"body": {"identifier": "DELTA-2026-XYZ", "fallback_id": "DELTA-2026-XYZ", "count": 99}, "method": "POST"},
            "DELTA-2026-XYZ",
            "java.time.LocalDate",
            ParameterLocation.BODY,
            None,
        ),
        # Rejected value lives in a query parameter, not the body.
        (
            "/api/reports",
            {"query": {"from": "dd-MM-yyyy-HHmm", "limit": "10"}, "method": "GET"},
            "dd-MM-yyyy-HHmm",
            "java.time.LocalDateTime",
            ParameterLocation.QUERY,
            ("from",),
        ),
        # Rejected value isn't in the request — likely a server-side default.
        (
            "/api/users",
            {"body": {"name": "alice", "score": 42}, "method": "POST"},
            "2024-01-15",
            "java.time.LocalDate",
            ParameterLocation.BODY,
            None,
        ),
        # Nested ambiguity: same value in both `shipping.trackingNumber` and `items[0].sku`.
        (
            "/api/orders",
            {
                "body": {"shipping": {"trackingNumber": "TRK-2026-AABB"}, "items": [{"sku": "TRK-2026-AABB"}]},
                "method": "POST",
            },
            "TRK-2026-AABB",
            "java.time.LocalDate",
            ParameterLocation.BODY,
            None,
        ),
        # Distinct nested match — only one candidate.
        (
            "/api/orders",
            {
                "body": {"shipping": {"trackingNumber": "TRK-2026-AABB"}, "items": [{"sku": "DIFFERENT-VALUE"}]},
                "method": "POST",
            },
            "TRK-2026-AABB",
            "java.time.LocalDate",
            ParameterLocation.BODY,
            ("shipping", "trackingNumber"),
        ),
    ],
    ids=[
        "ambiguous-flat-body",
        "query-attribution",
        "value-not-in-request",
        "ambiguous-nested",
        "distinct-nested",
    ],
)
def test_field_inference_attribution_through_pipeline(
    make_operation,
    case_factory,
    response_factory,
    operation_path,
    case_kwargs,
    rejected_value,
    type_name,
    expected_location,
    expected_path,
):
    method = case_kwargs["method"]
    operation = make_operation(method=method.lower(), path=operation_path)
    case = case_factory(operation=operation, **case_kwargs)
    response_body = {"message": f'Cannot deserialize value of type `{type_name}` from String "{rejected_value}"'}

    store = ErrorFeedbackStore()
    for _ in range(2):
        _record(store, operation=operation, case=case, body=response_body, response_factory=response_factory)

    expected: tuple[Observation, ...] = (
        ()
        if expected_path is None
        else (
            Observation(
                operation_label=operation.label,
                location=expected_location,
                parameter_path=expected_path,
                kind=ObservationKind.TYPE_MISMATCH,
                raw_message=response_body["message"],
                payload=TypeMismatchPayload(type_name=type_name),
            ),
        )
    )
    assert store.observations(operation_label=operation.label, location=expected_location) == expected


# Realistic Jackson 400 envelopes for typical Spring deployments without `INCLUDE_FIELD_PATH_IN_ERRORS`.
_JACKSON_NO_REFERENCE_CHAIN_CASES: tuple[dict, ...] = (
    {
        "id": "localdate-dash-format",
        "method": "POST",
        "path": "/api/records",
        "body": {"employeeId": 7, "projectId": 12, "commitDate": "dd-MM-yyyy", "comment": "x", "billable": True},
        "response": {
            "message": 'JSON parse error: Cannot deserialize value of type `java.time.LocalDate` from String "dd-MM-yyyy"'
        },
        "expected_path": ("commitDate",),
        "expected_payload": TypeMismatchPayload(type_name="java.time.LocalDate"),
    },
    {
        "id": "localdate-slash-format",
        "method": "POST",
        "path": "/api/profiles",
        "body": {"name": "Alice", "hireDate": "MM/dd/yyyy", "departmentId": 3},
        "response": {"message": 'Cannot deserialize value of type `java.time.LocalDate` from String "MM/dd/yyyy"'},
        "expected_path": ("hireDate",),
        "expected_payload": TypeMismatchPayload(type_name="java.time.LocalDate"),
    },
)


@pytest.mark.parametrize("payload", _JACKSON_NO_REFERENCE_CHAIN_CASES, ids=lambda p: p["id"])
def test_field_inference_jackson_envelopes_without_reference_chain(
    payload, make_operation, case_factory, response_factory
):
    operation = make_operation(method=payload["method"].lower(), path=payload["path"])
    case = case_factory(operation=operation, body=payload["body"], method=payload["method"])

    store = ErrorFeedbackStore()
    for _ in range(2):
        _record(store, operation=operation, case=case, body=payload["response"], response_factory=response_factory)

    assert store.observations(operation_label=operation.label, location=ParameterLocation.BODY) == (
        Observation(
            operation_label=operation.label,
            location=ParameterLocation.BODY,
            parameter_path=payload["expected_path"],
            kind=ObservationKind.TYPE_MISMATCH,
            raw_message=payload["response"]["message"],
            payload=payload["expected_payload"],
        ),
    )
