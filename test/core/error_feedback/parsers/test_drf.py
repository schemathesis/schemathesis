from __future__ import annotations

import pytest

from schemathesis.core.error_feedback import (
    BoundDirection,
    FormatPayload,
    NumericBoundPayload,
    ObservationKind,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.error_feedback.parsers.drf import DRFParser, _classify, _walk
from schemathesis.core.error_feedback.parsers.extractors import location_for_method
from schemathesis.core.parameters import ParameterLocation
from test.core.error_feedback.parsers.helpers import DRF_DETAIL_WRAPPED_BODY, drf_obs, parse_observations


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


def test_drf_parser_parse_flat_field(make_operation, case_factory):
    body = {"name": ["This field is required."]}
    assert parse_observations(DRFParser(), body, make_operation, case_factory) == (
        drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("name",),
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="This field is required.",
        ),
    )


def test_drf_parser_parse_nested_with_size_bound(make_operation, case_factory):
    body = {"address": {"zipcode": ["Ensure this field has at least 5 characters."]}}
    assert parse_observations(DRFParser(), body, make_operation, case_factory) == (
        drf_obs(
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
        drf_obs(
            op="GET /api/users",
            location=ParameterLocation.QUERY,
            path=("limit",),
            kind=ObservationKind.TYPE_MISMATCH,
            raw_message="A valid integer is required.",
            payload=TypeMismatchPayload(type_name="integer"),
        ),
    )


def test_drf_parser_keeps_nested_detail_body_field(make_operation, case_factory):
    assert parse_observations(DRFParser(), DRF_DETAIL_WRAPPED_BODY, make_operation, case_factory) == (
        drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("detail", "tags"),
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="This field may not be blank.",
        ),
    )


def test_drf_parser_parse_skips_unrecognised_messages(make_operation, case_factory):
    body = {"name": ["Custom validate_name message."]}
    assert parse_observations(DRFParser(), body, make_operation, case_factory) == ()


def test_drf_parser_parse_non_field_errors_only_yields_empty(make_operation, case_factory):
    body = {"non_field_errors": ["Passwords do not match."]}
    assert parse_observations(DRFParser(), body, make_operation, case_factory) == ()


def test_drf_parser_parse_list_with_failing_index(make_operation, case_factory):
    body = {"emails": [{}, {}, {"value": ["Enter a valid email address."]}]}
    assert parse_observations(DRFParser(), body, make_operation, case_factory) == (
        drf_obs(
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
    obs = parse_observations(DRFParser(), _DRF_MULTI_ERROR_BODY, make_operation, case_factory)
    assert obs == (
        drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("username",),
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="This field may not be blank.",
        ),
        drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("username",),
            kind=ObservationKind.SIZE_BOUND,
            raw_message="Ensure this field has at least 3 characters.",
            payload=SizeBoundPayload(min=3, max=None),
        ),
    )


def test_drf_parser_end_to_end_django_bridge_max_length(make_operation, case_factory):
    obs = parse_observations(DRFParser(), _DRF_DJANGO_BRIDGE_BODY, make_operation, case_factory)
    assert obs == (
        drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("email",),
            kind=ObservationKind.SIZE_BOUND,
            raw_message="Ensure this value has at most 20 characters (it has 25).",
            payload=SizeBoundPayload(min=None, max=20),
        ),
    )


def test_drf_parser_end_to_end_list_index_attribution(make_operation, case_factory):
    obs = parse_observations(DRFParser(), _DRF_LIST_INDEX_BODY, make_operation, case_factory)
    assert obs == (
        drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("emails", 2, "value"),
            kind=ObservationKind.FORMAT,
            raw_message="Enter a valid email address.",
            payload=FormatPayload(name="email"),
        ),
    )


def test_drf_parser_end_to_end_nested_serializer(make_operation, case_factory):
    obs = parse_observations(DRFParser(), _DRF_NESTED_BODY, make_operation, case_factory)
    # Only the recognised "This field is required." emits — "Enter a valid value." is unmapped.
    assert obs == (
        drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("address", "zipcode"),
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="This field is required.",
        ),
    )


def test_drf_parser_end_to_end_integer_min_value(make_operation, case_factory):
    obs = parse_observations(DRFParser(), _DRF_INTEGER_BODY, make_operation, case_factory)
    assert obs == (
        drf_obs(
            op="POST /api/users",
            location=ParameterLocation.BODY,
            path=("age",),
            kind=ObservationKind.NUMERIC_BOUND,
            raw_message="Ensure this value is greater than or equal to 0.",
            payload=NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
        ),
    )
