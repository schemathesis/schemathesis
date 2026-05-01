from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from schemathesis.core.error_feedback import (
    MAX_ENTRIES_PER_BUCKET,
    ErrorFeedbackStore,
    FormatPayload,
    Observation,
    ObservationKind,
    SizeBoundPayload,
)
from schemathesis.core.error_feedback.collector import record_response
from schemathesis.core.error_feedback.parsers import PARSERS
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
    RequiredFieldAdjustment,
    SizeBoundAdjustment,
    apply_adjustments,
)


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
