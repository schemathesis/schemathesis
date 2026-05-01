from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from schemathesis.core.error_feedback import (
    MAX_ENTRIES_PER_BUCKET,
    BoundDirection,
    ErrorFeedbackStore,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    PatternPayload,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.error_feedback.collector import record_response
from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.parsers.jackson import JacksonParser
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
    FormatAdjustment,
    NumericBoundAdjustment,
    PatternAdjustment,
    RequiredFieldAdjustment,
    SizeBoundAdjustment,
    TypeMismatchAdjustment,
    apply_adjustments,
)
from schemathesis.specs.openapi.patterns import normalize_regex


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


@pytest.mark.parametrize(
    "body, expected_paths",
    [
        (SPRING_MESSAGES, [("zipcode",), ("city",)]),
        (SPRING_SUBERRORS, [("password",)]),
        (SPRING_PROBLEMDETAIL, [("telephone",)]),
        (SPRING_ERRORS, [("email",)]),
        (SPRING_FIELDERRORS, [("name",)]),
        (SPRING_FIELDFIELD_PREFIX, [("name",)]),
    ],
    ids=[
        "messages",
        "subErrors",
        "problemDetail",
        "errors",
        "fieldErrors",
        "subErrors-with-fieldname-prefix",
    ],
)
def test_spring_parser_extracts_observations(body, expected_paths):
    obs = SpringParser().parse(
        operation_label="POST /api/users",
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
def test_spring_parser_recognizes_non_blank_message_variants(message):
    body = {"subErrors": [{"field": "x", "message": message}]}
    obs = SpringParser().parse(operation_label="POST /api/users", body=body)
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
def test_spring_parser_skips_unrecognized_messages(message):
    body = {"subErrors": [{"field": "x", "message": message}]}
    assert SpringParser().parse(operation_label="POST /api/users", body=body) == ()


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
def test_spring_parser_recognizes_size_bound_message_variants(message, expected_min, expected_max):
    body = {"subErrors": [{"field": "username", "message": message}]}
    obs = SpringParser().parse(operation_label="POST /api/users", body=body)
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
def test_spring_parser_recognizes_format_message_variants(message, expected_name):
    body = {"subErrors": [{"field": "contact", "message": message}]}
    obs = SpringParser().parse(operation_label="POST /api/users", body=body)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (("contact",), ObservationKind.FORMAT, FormatPayload(name=expected_name)),
    ]


def test_spring_parser_uuid_takes_precedence_over_uri_when_both_match():
    # Defensive: a contrived "must be a valid URI UUID" string would match both
    # the URI and UUID regexes. The classifier checks UUID first so the more
    # specific format wins.
    body = {"subErrors": [{"field": "x", "message": "must be a valid UUID"}]}
    obs = SpringParser().parse(operation_label="POST /api/users", body=body)
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
    message, expected_bound, expected_direction, expected_exclusive
):
    body = {"subErrors": [{"field": "score", "message": message}]}
    obs = SpringParser().parse(operation_label="POST /api/users", body=body)
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
def test_spring_parser_recognizes_pattern_message_variants(message, expected_regex):
    body = {"subErrors": [{"field": "code", "message": message}]}
    obs = SpringParser().parse(operation_label="POST /api/users", body=body)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (("code",), ObservationKind.PATTERN, PatternPayload(regex=expected_regex)),
    ]


def test_parsers_registry_contains_jackson_parser():
    assert JacksonParser in PARSERS.get_all()


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
    ],
)
def test_jackson_parser_extracts_observations(carrier_key, message, expected_path, expected_type):
    body = {carrier_key: message}
    obs = JacksonParser().parse(operation_label="POST /api/users", body=body)
    assert [(o.parameter_path, o.kind, o.payload) for o in obs] == [
        (expected_path, ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(java_type=expected_type)),
    ]


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


def test_jackson_parser_skips_message_without_reference_chain():
    # Field attribution requires the chain — a bare type message can't be routed.
    body = {"msg": 'Cannot deserialize value of type `java.time.LocalDate` from String "x"'}
    assert JacksonParser().can_parse(body=body) is True
    assert JacksonParser().parse(operation_label="POST /api/users", body=body) == ()


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
def test_jackson_parser_parse_returns_empty_for_unparsable_bodies(body):
    assert JacksonParser().parse(operation_label="POST /api/users", body=body) == ()


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
def test_jackson_parser_walks_into_array_shape_envelopes(array_key, item_key):
    # Custom `@ControllerAdvice` handlers sometimes funnel Jackson parse errors
    # alongside Bean-validation results into a single `errors[]` array.
    body = {array_key: [{item_key: _JACKSON_LOCAL_DATE}]}
    obs = JacksonParser().parse(operation_label="POST /api/users", body=body)
    assert [o.payload for o in obs] == [TypeMismatchPayload(java_type="java.time.LocalDate")]


def test_jackson_parser_skips_non_dict_array_items():
    body = {"errors": ["string-item", 123, None, {"message": _JACKSON_LOCAL_DATE}]}
    obs = JacksonParser().parse(operation_label="POST /api/users", body=body)
    assert [o.payload for o in obs] == [TypeMismatchPayload(java_type="java.time.LocalDate")]


def test_jackson_parser_extracts_one_observation_per_carrier_key():
    # Different carrier keys can each carry a Jackson error — `_carrier_strings`
    # walks them in order and emits one observation per match.
    body = {
        "msg": _JACKSON_LOCAL_DATE,
        "detail": _JACKSON_UUID,
    }
    obs = JacksonParser().parse(operation_label="POST /api/users", body=body)
    assert [(o.parameter_path, o.payload) for o in obs] == [
        (("hire_date",), TypeMismatchPayload(java_type="java.time.LocalDate")),
        (("id",), TypeMismatchPayload(java_type="java.util.UUID")),
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
def test_spring_parser_extracts_multiple_entries_per_shape(body, expected_paths):
    obs = SpringParser().parse(operation_label="POST /api/users", body=json.loads(body))
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
def test_spring_parser_splits_dotted_paths_into_tuples(body, expected_path):
    obs = SpringParser().parse(operation_label="POST /api/users", body=body)
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
def test_spring_parser_errors_field_and_message_priority(entry, expected):
    body = {"errors": [entry]}
    obs = SpringParser().parse(operation_label="POST /api/users", body=body)
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
def test_spring_parser_field_errors_locator_and_message_priority(entry, expected):
    body = {"fieldErrors": [entry]}
    obs = SpringParser().parse(operation_label="POST /api/users", body=body)
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
def test_spring_parser_skips_invalid_or_unrecognized_entries(body):
    assert SpringParser().parse(operation_label="POST /api/users", body=body) == ()


def test_spring_parser_mixes_valid_and_invalid_messages():
    body = {
        "messages": [
            "valid - must not be blank",
            123,
            "no_dash_here",
            "another - is required",
            "x - just some prose",
        ]
    }
    obs = SpringParser().parse(operation_label="POST /api/users", body=body)
    assert [o.parameter_path for o in obs] == [("valid",), ("another",)]


@pytest.mark.parametrize(
    "body",
    [[1, 2, 3], "not a dict", None, 42, 1.5, True],
    ids=["list", "string", "none", "int", "float", "bool"],
)
def test_spring_parser_returns_empty_for_non_dict_body(body):
    assert SpringParser().parse(operation_label="POST /api/users", body=body) == ()


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
            raw_message=f'Cannot deserialize value of type `{java_type}` from String "..."',
            payload=TypeMismatchPayload(java_type=java_type),
        )
        for path, java_type in items
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
