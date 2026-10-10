from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

import pytest
from pydantic import AnyUrl, BaseModel, Field, ValidationError

from schemathesis.core.error_feedback import (
    BoundDirection,
    EnumPayload,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    PatternPayload,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.error_feedback.parsers.pydantic import PydanticParser, _parse_expected
from schemathesis.core.parameters import ParameterLocation
from test.core.error_feedback.parsers.helpers import parse_observations


@pytest.mark.parametrize(
    "body",
    [
        {"detail": [{"type": "missing", "loc": ["body", "x"], "msg": "Field required"}]},
        {"detail": [{"type": "string_too_short", "loc": ["body", "x"], "msg": "...", "ctx": {"min_length": 3}}]},
        {"exception_detail": [{"type": "missing", "loc": ["body", "x"], "msg": "Field required"}]},
    ],
    ids=["missing", "string-too-short", "exception-detail-envelope"],
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


_PYDANTIC_V1_FIXTURES: tuple[tuple[dict, Observation], ...] = (
    (
        {"type": "value_error.missing", "loc": ["body", "name"], "msg": "field required"},
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("name",),
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="field required",
        ),
    ),
    (
        {"type": "type_error.integer", "loc": ["body", "age"], "msg": "value is not a valid integer"},
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("age",),
            kind=ObservationKind.TYPE_MISMATCH,
            raw_message="value is not a valid integer",
            payload=TypeMismatchPayload(type_name="integer"),
        ),
    ),
    (
        {
            "type": "value_error.any_str.min_length",
            "loc": ["body", "name"],
            "msg": "ensure this value has at least 3 characters",
            "ctx": {"limit_value": 3},
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("name",),
            kind=ObservationKind.SIZE_BOUND,
            raw_message="ensure this value has at least 3 characters",
            payload=SizeBoundPayload(min=3, max=None),
        ),
    ),
    (
        {
            "type": "value_error.any_str.max_length",
            "loc": ["body", "name"],
            "msg": "ensure this value has at most 20 characters",
            "ctx": {"limit_value": 20},
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("name",),
            kind=ObservationKind.SIZE_BOUND,
            raw_message="ensure this value has at most 20 characters",
            payload=SizeBoundPayload(min=None, max=20),
        ),
    ),
    (
        {
            "type": "value_error.number.not_gt",
            "loc": ["body", "qty"],
            "msg": "ensure this value is greater than 0",
            "ctx": {"limit_value": 0},
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("qty",),
            kind=ObservationKind.NUMERIC_BOUND,
            raw_message="ensure this value is greater than 0",
            payload=NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
        ),
    ),
    (
        {
            "type": "value_error.number.not_ge",
            "loc": ["body", "qty"],
            "msg": "ensure this value is greater than or equal to 1",
            "ctx": {"limit_value": 1},
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("qty",),
            kind=ObservationKind.NUMERIC_BOUND,
            raw_message="ensure this value is greater than or equal to 1",
            payload=NumericBoundPayload(bound=1.0, direction=BoundDirection.MIN, exclusive=False),
        ),
    ),
    (
        {
            "type": "value_error.number.not_lt",
            "loc": ["body", "qty"],
            "msg": "ensure this value is less than 100",
            "ctx": {"limit_value": 100},
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("qty",),
            kind=ObservationKind.NUMERIC_BOUND,
            raw_message="ensure this value is less than 100",
            payload=NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=True),
        ),
    ),
    (
        {
            "type": "value_error.number.not_le",
            "loc": ["body", "qty"],
            "msg": "ensure this value is less than or equal to 100",
            "ctx": {"limit_value": 100},
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("qty",),
            kind=ObservationKind.NUMERIC_BOUND,
            raw_message="ensure this value is less than or equal to 100",
            payload=NumericBoundPayload(bound=100.0, direction=BoundDirection.MAX, exclusive=False),
        ),
    ),
    (
        {
            "type": "value_error.str.regex",
            "loc": ["body", "code"],
            "msg": 'string does not match regex "^[A-Z]+$"',
            "ctx": {"pattern": "^[A-Z]+$"},
        },
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("code",),
            kind=ObservationKind.PATTERN,
            raw_message='string does not match regex "^[A-Z]+$"',
            payload=PatternPayload(regex="^[A-Z]+$"),
        ),
    ),
    (
        {"type": "value_error.email", "loc": ["body", "email"], "msg": "value is not a valid email address"},
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=("email",),
            kind=ObservationKind.FORMAT,
            raw_message="value is not a valid email address",
            payload=FormatPayload(name="email"),
        ),
    ),
)


@pytest.mark.parametrize(
    "entry, expected",
    _PYDANTIC_FIXTURES,
    ids=[entry["type"] for entry, _ in _PYDANTIC_FIXTURES],
)
def test_pydantic_parser_extracts_v2_observation(entry, expected, make_operation, case_factory):
    obs = parse_observations(PydanticParser(), {"detail": [entry]}, make_operation, case_factory)
    assert obs == (expected,)


@pytest.mark.parametrize(
    "entry, expected",
    _PYDANTIC_V1_FIXTURES,
    ids=[entry["type"] for entry, _ in _PYDANTIC_V1_FIXTURES],
)
def test_pydantic_parser_extracts_v1_observation(entry, expected, make_operation, case_factory):
    obs = parse_observations(PydanticParser(), {"detail": [entry]}, make_operation, case_factory)
    assert obs == (expected,)


@pytest.mark.parametrize("type_code", ["missing", "value_error.missing"], ids=["v2", "v1"])
def test_pydantic_parser_emits_body_required_for_root_missing(type_code, make_operation, case_factory):
    entry = {"type": type_code, "loc": ["body"], "msg": "Field required"}
    obs = parse_observations(PydanticParser(), {"detail": [entry]}, make_operation, case_factory)
    assert obs == (
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=(),
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="Field required",
        ),
    )


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
    obs = parse_observations(PydanticParser(), body, make_operation, case_factory)
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


def test_pydantic_parser_reads_exception_detail_envelope(make_operation, case_factory):
    # Both envelopes must yield identical observations.
    entry = {"type": "missing", "loc": ["body", "x"], "msg": "Field required"}
    standard = parse_observations(PydanticParser(), {"detail": [entry]}, make_operation, case_factory)
    wrapped = parse_observations(PydanticParser(), {"exception_detail": [entry]}, make_operation, case_factory)
    assert wrapped == standard
    assert wrapped != ()


@pytest.mark.parametrize(
    "loc, expected_path",
    [
        (["body", "extra"], ()),
        (["body", "address", "extra"], ("address",)),
        (["extra"], ()),
    ],
    ids=["top-level", "nested", "no-location-prefix"],
)
def test_pydantic_parser_extracts_extra_forbidden(loc, expected_path, make_operation, case_factory):
    body = {"detail": [{"type": "extra_forbidden", "loc": loc, "msg": "Extra inputs are not permitted"}]}
    obs = parse_observations(PydanticParser(), body, make_operation, case_factory)
    assert obs == (
        Observation(
            operation_label="POST /api/users",
            location=ParameterLocation.BODY,
            parameter_path=expected_path,
            kind=ObservationKind.FORBIDS_ADDITIONAL_PROPERTIES,
            raw_message="Extra inputs are not permitted",
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
    obs = parse_observations(PydanticParser(), body, make_operation, case_factory)
    assert len(obs) == 1
    assert obs[0].location == expected_location
    assert obs[0].parameter_path == ("x",)


def test_pydantic_parser_defaults_to_body_when_loc_prefix_unrecognized(make_operation, case_factory):
    body = {"detail": [{"type": "missing", "loc": ["unknown", "x"], "msg": "Field required"}]}
    obs = parse_observations(PydanticParser(), body, make_operation, case_factory)
    assert obs[0].location == ParameterLocation.BODY
    assert obs[0].parameter_path == ("unknown", "x")


def test_pydantic_parser_handles_int_path_segments(make_operation, case_factory):
    # FastAPI emits int segments for list-element validation failures.
    body = {"detail": [{"type": "missing", "loc": ["body", "items", 0, "qty"], "msg": "Field required"}]}
    obs = parse_observations(PydanticParser(), body, make_operation, case_factory)
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
    ],
    ids=[
        "none",
        "string-body",
        "list-body",
        "detail-string",
        "missing-loc",
        "unknown-type-code",
        "empty-loc",
    ],
)
def test_pydantic_parser_parse_returns_empty_for_uninteresting_bodies(body, make_operation, case_factory):
    assert parse_observations(PydanticParser(), body, make_operation, case_factory) == ()


@pytest.mark.parametrize(
    "type_code, context",
    [
        ("string_too_short", {}),
        ("value_error.any_str.min_length", {}),
        ("value_error.any_str.max_length", {}),
        ("string_too_long", {}),
        ("greater_than", {}),
        ("greater_than", {"gt": True}),
        ("value_error.number.not_ge", {}),
        ("value_error.number.not_le", {"limit_value": "100"}),
        ("less_than_equal", {"le": "100"}),
        ("string_pattern_mismatch", {}),
        ("value_error.str.regex", {}),
        ("enum", {"expected": "no quoted values here"}),
        ("literal_error", {}),
    ],
    ids=[
        "size-min-missing",
        "v1-size-min-missing",
        "v1-size-max-missing",
        "size-max-missing",
        "numeric-missing",
        "numeric-bool",
        "v1-numeric-missing",
        "v1-numeric-string",
        "numeric-string",
        "pattern-missing",
        "v1-pattern-missing",
        "enum-no-tokens",
        "literal-no-context",
    ],
)
def test_pydantic_parser_skips_handler_with_invalid_context(type_code, context, make_operation, case_factory):
    body = {"detail": [{"type": type_code, "loc": ["body", "x"], "msg": "...", "ctx": context}]}
    assert parse_observations(PydanticParser(), body, make_operation, case_factory) == ()


def test_pydantic_parser_skips_non_dict_detail_entry(make_operation, case_factory):
    body = {"detail": [42, {"type": "missing", "loc": ["body", "x"], "msg": "Field required"}]}
    obs = parse_observations(PydanticParser(), body, make_operation, case_factory)
    assert obs[0].parameter_path == ("x",)
    assert len(obs) == 1


def test_pydantic_parser_emits_one_observation_per_detail_entry(make_operation, case_factory):
    body = {
        "detail": [
            {"type": "missing", "loc": ["body", "name"], "msg": "Field required"},
            {"type": "string_too_short", "loc": ["body", "code"], "msg": "...", "ctx": {"min_length": 3}},
        ]
    }
    obs = parse_observations(PydanticParser(), body, make_operation, case_factory)
    assert [(o.parameter_path, o.kind) for o in obs] == [
        (("name",), ObservationKind.MUST_NOT_BE_BLANK),
        (("code",), ObservationKind.SIZE_BOUND),
    ]
