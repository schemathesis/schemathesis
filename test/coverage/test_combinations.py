from __future__ import annotations

import json
import re
import sys
from math import inf, nextafter
from unittest.mock import ANY

import hypothesis.strategies as st
import jsonschema_rs
import pytest
from hypothesis import given, settings
from hypothesis.errors import Unsatisfiable

from schemathesis.core import MAX_GENERATED_PATTERN_LENGTH
from schemathesis.core.cache import MISSING
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY, make_validator_for
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import deepclone, transform
from schemathesis.generation import GenerationMode
from schemathesis.generation.coverage import MAX_PINNED_REGISTRIES, GenerationSession
from schemathesis.openapi.generation.filters import is_invalid_path_parameter
from schemathesis.specs.openapi.converter import to_json_schema
from schemathesis.specs.openapi.coverage import _schema
from schemathesis.specs.openapi.coverage._schema import (
    MAX_DRAWN_ARRAY_ITEMS,
    CoverageContext,
    CoverageScenario,
    GeneratedValue,
    _apply_pattern_optimizations,
    _cover_positive_for_type,
    _negative_format,
    _positive_number,
    _positive_string,
    cover_schema_iter,
)
from schemathesis.specs.openapi.patterns import update_quantifier
from test.coverage.helpers import scenario_values
from test.utils import to_float32

UUID_PATTERN = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

SKIP_BEFORE_PY11 = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Possessive repeats and atomic groups are only available in Python 3.11+"
)

PATTERN = "^\\d+$"


def cover_schema(ctx: CoverageContext, schema: dict) -> list:
    return [value.value for value in cover_schema_iter(ctx, schema)]


def assert_unique(values: list):
    seen = set()
    for value in values:
        if isinstance(value, GeneratedValue):
            value = value.value
        if isinstance(value, (dict | list)):
            try:
                serialized = jsonschema_rs.canonical.json.to_string(value)
            except ValueError:
                serialized = json.dumps(value, sort_keys=True)
            key = (type(value), serialized)
        else:
            key = (type(value), value)
        assert key not in seen
        seen.add(key)


def assert_conform(values: list, schema: dict):
    try:
        validator = jsonschema_rs.Draft7Validator(schema, validate_formats=True)
    except jsonschema_rs.ValidationError:
        # Schema itself is invalid (e.g., pattern: 0.0), skip validation
        return
    for value in values:
        if isinstance(value, GeneratedValue):
            value = value.value
        validator.validate(value)


def assert_not_conform(values: list, schema: dict):
    if isinstance(schema, dict) and schema.get("format") == "unknown":
        # Can't validate the format
        return
    try:
        validator = jsonschema_rs.Draft7Validator(schema, validate_formats=True)
    except jsonschema_rs.ValidationError:
        # Schema itself is invalid (e.g., pattern: 0.0), skip validation
        return
    for entry in values:
        try:
            validator.validate(entry)
            raise AssertionError(f"Value {entry} conforms to {schema}")
        except (jsonschema_rs.ValidationError, ValueError):
            pass


def assert_covers(ctx: CoverageContext, schema: dict, expected: list):
    covered = cover_schema(ctx, schema)
    assert covered == expected
    assert_unique(covered)
    assert_conform(covered, schema)


def assert_covers_negative(ctx: CoverageContext, schema: dict, expected: list):
    covered = cover_schema(ctx, schema)
    assert covered == expected
    assert_unique(covered)
    assert_not_conform(covered, schema)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (True, [None, True, False, "", 0, [None, None], {}]),
        ({}, [None, True, False, "", 0, [None, None], {}]),
        (False, []),
        ({"type": "null"}, [None]),
        ({"type": "boolean"}, [True, False]),
        ({"type": ["boolean", "null"]}, [True, False, None]),
        ({"enum": [1, 2]}, [1, 2]),
        ({"const": 42}, [42]),
        ({"not": {}}, []),
        ({"not": {"type": "null"}}, [0, 0.5, "true", "AAA", ["null", "null"]]),
    ],
)
def test_positive_primitive_schemas(pctx, schema, expected):
    assert_covers(pctx, schema, expected)


class AnyString:
    def __eq__(self, value: object, /) -> bool:
        return isinstance(value, str)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (False, [None, True, False, "", 0, [None, None], {}]),
        (True, []),
        ({}, []),
        ({"type": "null"}, [0, 0.5, "true", "AAA", ["null", "null"]]),
        # 0/1 coerce to booleans in lenient query/path parsers, so the numeric type violations
        # are non-coercible values instead.
        ({"type": "boolean"}, [2, 0.5, "null", "AAA", ["null", "null"]]),
        ({"type": ["boolean", "null"]}, [2, 0.5, "AAA", ["null", "null"]]),
        # canonicalish drops `type` when `enum` is present; infer it from the values so type
        # violations still appear alongside the enum violation. The enum-negative "AAA"
        # collides with the type-negative "AAA" and dedupes to one entry.
        ({"enum": [1, 2]}, ["AAA", 0.5, "true", "null", ["null", "null"]]),
        ({"enum": [1, 2, {}]}, ["AAA", 0.5, "true", "null", ["null", "null"]]),
        ({"enum": ["a", "b"]}, ["AAA", 0, 0.5, "true", "null", ["null", "null"]]),
        ({"multipleOf": 2}, lambda x: x % 2 != 0),
        ({"format": "date-time"}, [AnyString()]),
        ({"format": "hostname"}, [AnyString()]),
        # Unknown formats have no validation semantics, so no negative cases can be generated
        ({"format": "unknown"}, []),
        ({"uniqueItems": True}, [["null", "null"]]),
        ({"maximum": 5}, [6]),
        ({"minimum": 5}, [4]),
        ({"exclusiveMinimum": 5}, [5]),
        ({"exclusiveMaximum": 5}, [5]),
        ({"minimum": 5, "exclusiveMinimum": True}, [5]),
        ({"maximum": 10, "exclusiveMaximum": True}, [10]),
        ({"required": ["a"]}, [{}]),
        ({"not": {}}, [None, True, False, "", 0, [None, None], {}]),
        ({"not": {"type": "null"}}, [None]),
    ],
)
def test_negative_primitive_schemas(nctx, schema, expected):
    covered = cover_schema(nctx, schema)
    if callable(expected):
        assert len(covered) == 1
        assert expected(covered[0])
    else:
        assert covered == expected
    assert_unique(covered)
    assert_not_conform(covered, schema)


def test_negative_const(ctx_factory):
    # `const` arrived in Draft 6; only dialects whose validator enforces it get the negation.
    nctx = ctx_factory(generation_modes=[GenerationMode.NEGATIVE], validator_cls=jsonschema_rs.Draft202012Validator)
    assert_covers_negative(nctx, {"const": 42}, ["AAA", 0.5, "true", "null", ["null", "null"]])


@pytest.mark.parametrize(
    "schema",
    [
        # `default: null` — Python `None` after JSON load. Must round-trip through the `null`
        # type branch and not be skipped as "absent". Sentinel-based read ensures that.
        {"type": ["string", "null"], "default": None},
        {"type": ["integer", "null"], "example": None},
    ],
)
def test_positive_null_default_or_example_round_trips(pctx, schema):
    covered = cover_schema(pctx, schema)
    assert None in covered, f"`null` default/example was dropped: {covered!r}"


def test_unbounded_array_positive_baseline_is_non_empty(pctx):
    # An empty list never exercises items-level keywords on the wire; coverage must emit a populated baseline first.
    covered = cover_schema(pctx, {"type": "array", "items": {"type": "integer", "format": "int32"}})
    assert covered
    assert covered[0], covered


@given(value=st.dictionaries(st.text(), st.text() | st.integers() | st.booleans() | st.none(), max_size=5))
@settings(max_examples=50)
def test_dicts_always_invalid_as_path_parameters(value):
    # dict.__repr__ always contains `{` and `}`, making all dicts invalid path parameters.
    assert is_invalid_path_parameter(value)


@pytest.mark.parametrize(
    "location",
    [ParameterLocation.QUERY, ParameterLocation.HEADER, ParameterLocation.BODY],
    ids=["query", "header", "body"],
)
def test_negative_type_string_for_integer_is_non_empty(ctx_factory, location):
    # `_negative_type` draws `st.text()` for the string-type negative on a non-string
    # parameter; Hypothesis shrinks to "" and `_is_not_numeric_string` passes it through.
    # `?param=` / empty header / empty body collapse to absent on the wire, so the
    # negative can't demonstrate a type violation against the declared `integer` type.
    ctx = ctx_factory(generation_modes=[GenerationMode.NEGATIVE], location=location)
    values = cover_schema(ctx, {"type": "integer", "format": "int32"})
    assert "" not in values, f"{location}: empty string emitted as string-type negative; got {values!r}"


@pytest.mark.parametrize("location", [ParameterLocation.QUERY, ParameterLocation.BODY], ids=["query", "body"])
@pytest.mark.parametrize("allow_extra_parameters", [True, False])
def test_unexpected_parameters_control(ctx_factory, location, allow_extra_parameters):
    schema = {
        "type": "object",
        "properties": {"token": {"type": "string"}},
        "required": ["token"],
        "additionalProperties": False,
    }
    ctx = ctx_factory(
        location=location,
        generation_modes=[GenerationMode.NEGATIVE],
        allow_extra_parameters=allow_extra_parameters,
    )
    scenarios = {value.scenario for value in cover_schema_iter(ctx, schema)}
    if allow_extra_parameters:
        assert CoverageScenario.OBJECT_UNEXPECTED_PROPERTIES in scenarios
    else:
        assert CoverageScenario.OBJECT_UNEXPECTED_PROPERTIES not in scenarios


@pytest.mark.parametrize(
    ("schema", "lengths"),
    [
        ({"type": "string"}, {0}),
        ({"type": "string", "example": "test"}, {4}),
        ({"type": "string", "example": "test", "default": "test"}, {4}),
        ({"type": "string", "example": "test", "default": "another"}, {4, 7}),
        ({"type": "string", "default": "test"}, {4}),
        ({"type": "string", "examples": ["A", "BB"]}, {1, 2}),
        ({"type": "string", "minLength": 0}, {0}),
        ({"type": "string", "pattern": "^[\\w\\W]+$"}, {1}),
        ({"type": "string", "minLength": 5}, {5, 6}),
        ({"type": "string", "maxLength": 10}, {9, 10}),
        ({"type": "string", "minLength": 5, "maxLength": 10}, {5, 6, 9, 10}),
        ({"type": "string", "minLength": 5, "maxLength": 6}, {5, 6}),
        ({"type": "string", "minLength": 5, "maxLength": 5}, {5}),
        ({"type": "string", "minLength": 0, "maxLength": 512, "pattern": r"^[\w\W]+$"}, {1}),
        # Nullable string: union type must not leak into boundary generation,
        # otherwise generation may pick null and skip both length variants.
        ({"type": ["string", "null"], "maxLength": 10}, {9, 10}),
        ({"type": ["string", "null"], "minLength": 5, "maxLength": 10}, {5, 6, 9, 10}),
        # Falsy `default`/`example` are still set: empty string must be exercised.
        ({"type": "string", "default": ""}, {0}),
        ({"type": "string", "example": ""}, {0}),
        # Falsy `default` alongside truthy `examples`: both must be emitted.
        ({"type": "string", "default": "", "examples": ["a"]}, {0, 1}),
    ],
)
def test_positive_string(ctx_factory, schema, lengths):
    covered = list(_positive_string(ctx_factory(), schema))
    assert_unique(covered)
    for length in lengths:
        assert len([x for x in covered if isinstance(x.value, str) and len(x.value) == length]) == 1
    for value in covered:
        assert isinstance(value.value, str), f"non-string from _positive_string: {value.value!r}"
    assert_conform(covered, schema)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        # Too permissing - all values will be stringified anyway
        ({"type": "string"}, []),
        ({"type": "string", "minLength": 5}, [0, 0.5, "true", "null", "0000"]),
        ({"type": "string", "maxLength": 10}, [["null", "null"], "00000000000"]),
        (
            {"type": "string", "minLength": 5, "maxLength": 10},
            [0, 0.5, "true", "null", ["null", "null"], "0000", "00000000000"],
        ),
        (
            {"type": "string", "pattern": "^[0-9]", "minLength": 1},
            ["true", "null", ["null", "null"], AnyString(), ""],
        ),
        ({"type": "string", "pattern": "^[0-9]"}, ["true", "null", ["null", "null"], AnyString()]),
        ({"type": "string", "format": "date-time"}, [0, 0.5, "true", "null", ["null", "null"], ""]),
    ],
)
def test_negative_string(nctx, schema, expected):
    assert_covers_negative(nctx, schema, expected)


def test_negative_string_with_pattern(nctx):
    schema = {
        "type": "string",
        "minLength": 5,
        "maxLength": 8,
        "pattern": r"^[\da-z]+$",
    }
    assert_covers_negative(nctx, schema, [0, 0.5, "true", "null", ["null", "null"], "0000", "000000000", AnyString()])


def test_negative_maxitems_when_unique_items_exhaust_enum(nctx):
    # `uniqueItems: true` + `items.enum` of size `maxItems` makes a length-(max+1) unique
    # array unsatisfiable. The maxItems negative is still meaningful (server may reject
    # on length first), so emit one with duplicates from the enum domain.
    schema = {
        "type": "array",
        "uniqueItems": True,
        "minItems": 1,
        "maxItems": 11,
        "items": {"type": "string", "enum": ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"]},
    }
    above_max = scenario_values(nctx, schema, CoverageScenario.ARRAY_ABOVE_MAX_ITEMS)
    assert above_max, "Expected an above-maxItems negative case"
    assert all(len(v) == 12 for v in above_max)
    assert all(item in schema["items"]["enum"] for v in above_max for item in v)


def test_negative_pattern_for_header_with_permissive_pattern(ctx_factory):
    # `^[A-Z0-9_]*$` accepts the empty string; the negative emitter must still find one
    # header-safe value that violates the pattern.
    ctx = ctx_factory(location=ParameterLocation.HEADER, generation_modes=[GenerationMode.NEGATIVE])
    schema = {"type": "string", "pattern": "^[A-Z0-9_]*$"}
    out = scenario_values(ctx, schema, CoverageScenario.INVALID_PATTERN)
    assert out, "Expected at least one pattern-violation negative for header parameter"
    compiled = re.compile(schema["pattern"])
    for value in out:
        assert isinstance(value, str)
        assert not compiled.fullmatch(value), f"Value {value!r} matches the pattern"


def test_negative_maxlength_emitted_with_unsatisfiable_pattern(nctx):
    # An unsatisfiable `pattern` would block the length-violation generator; the maxLength
    # rule is still server-side enforceable, so emit a too-long string even if it also
    # violates the broken pattern.
    schema = {"type": "string", "maxLength": 90, "minLength": 1, "pattern": r" ^[-\w\._\(\)]+[^\.]$"}
    above_max = scenario_values(nctx, schema, CoverageScenario.STRING_ABOVE_MAX_LENGTH)
    assert above_max, "Expected an above-maxLength negative case"
    assert all(len(s) == 91 for s in above_max)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "format": "email", "minLength": 6},
        {"type": "string", "format": "uuid", "minLength": 50},
    ],
    ids=["email", "uuid"],
)
def test_negative_minlength_emitted_with_constraining_format(nctx, schema):
    # No valid email of length 5; the minLength violation must still be emitted.
    below_min = scenario_values(nctx, schema, CoverageScenario.STRING_BELOW_MIN_LENGTH)
    assert below_min, "Expected a below-minLength negative case"
    assert all(isinstance(s, str) and len(s) == schema["minLength"] - 1 for s in below_min)


def test_negative_maxlength_emitted_with_constraining_format(nctx):
    # uuid is fixed at 36 chars; the 11-char maxLength violation must still be emitted.
    schema = {"type": "string", "format": "uuid", "maxLength": 10}
    above_max = scenario_values(nctx, schema, CoverageScenario.STRING_ABOVE_MAX_LENGTH)
    assert above_max, "Expected an above-maxLength negative case"
    assert all(isinstance(s, str) and len(s) == 11 for s in above_max)


def test_negative_maxlength_emitted_with_constraining_format_large_limit(nctx):
    # unknown format can't produce a 2001-char string; the violation must still be emitted.
    schema = {"type": "string", "format": "duration", "maxLength": 2000, "minLength": 1}
    above_max = scenario_values(nctx, schema, CoverageScenario.STRING_ABOVE_MAX_LENGTH)
    assert above_max, "Expected an above-maxLength negative case"
    assert all(isinstance(s, str) and len(s) == 2001 for s in above_max)


@pytest.mark.parametrize("max_length", [65536, 350000])
def test_negative_maxlength_above_buffer(nctx, max_length):
    schema = {"type": "string", "maxLength": max_length}
    above_max = scenario_values(nctx, schema, CoverageScenario.STRING_ABOVE_MAX_LENGTH)
    assert len(above_max) == 1
    assert len(above_max[0]) == max_length + 1


@pytest.mark.parametrize("multiple_of", [None, 2])
@pytest.mark.parametrize(
    ("schema", "values", "with_multiple_of"),
    [
        ({"type": "integer"}, [0], [0]),
        ({"type": "integer", "example": 2}, [2], [2]),
        ({"type": "integer", "example": 2, "default": 2}, [2], [2]),
        ({"type": "integer", "example": 2, "default": 4}, [2, 4], [2, 4]),
        ({"type": "integer", "default": 2}, [2], [2]),
        # `default: 0` / `example: 0` are valid spec hints; falsy must not skip them.
        ({"type": "integer", "default": 0}, [0], [0]),
        ({"type": "integer", "example": 0}, [0], [0]),
        # Falsy `default` alongside truthy `examples`: both must be emitted.
        ({"type": "integer", "default": 0, "examples": [1]}, [1, 0], [0]),
        ({"type": "integer", "examples": [42, 44]}, [42, 44], [42, 44]),
        ({"type": "number"}, [0], [0]),
        ({"type": "integer", "minimum": 5}, [5, 6], [6, 8]),
        ({"type": "number", "minimum": 5.5}, [5.5, 6.5], [6, 8]),
        ({"type": "integer", "maximum": 10}, [10, 9], [10, 8]),
        ({"type": "number", "maximum": 11.5}, [11.5, 10.5], [10, 8]),
        ({"type": "integer", "minimum": 5, "maximum": 10}, [5, 6, 10, 9], [6, 8, 10]),
        ({"type": "integer", "minimum": 5, "maximum": 6}, [5, 6], [6]),
        ({"type": "integer", "minimum": 5, "maximum": 5}, [5], None),
        (
            {"type": "integer", "minimum": 0, "exclusiveMinimum": False, "maximum": 30, "exclusiveMaximum": False},
            [0, 1, 30, 29],
            [0, 2, 30, 28],
        ),
        (
            {"type": "integer", "minimum": 0, "exclusiveMinimum": True, "maximum": 30, "exclusiveMaximum": True},
            [1, 2, 29, 28],
            [2, 4, 28, 26],
        ),
        (
            {"type": "number", "minimum": 0, "exclusiveMinimum": True, "maximum": 1, "exclusiveMaximum": True},
            [nextafter(0.0, inf), nextafter(1.0, -inf)],
            [],
        ),
        ({"type": "integer", "exclusiveMinimum": 5}, [6, 7], [6, 8]),
        ({"type": "integer", "exclusiveMaximum": 10}, [9, 8], [8, 6]),
        ({"type": "integer", "exclusiveMinimum": 5, "exclusiveMaximum": 10}, [6, 7, 9, 8], [6, 8]),
    ],
)
def test_positive_number(ctx_factory, schema, multiple_of, values, with_multiple_of):
    if with_multiple_of is None and multiple_of is not None:
        pytest.skip("This test is not applicable for multiple_of=None")
    if multiple_of is not None:
        schema = {**schema, "multipleOf": multiple_of}
        values = with_multiple_of
    covered = [value.value for value in _positive_number(ctx_factory(), schema)]
    assert_unique(covered)
    assert covered == values
    assert_conform(covered, schema)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "object"}, [{}]),
        ({"type": "object", "example": {"A": 42}}, [{"A": 42}]),
        ({"type": "object", "example": {"A": 42}, "default": {"A": 42}}, [{"A": 42}]),
        ({"type": "object", "example": {"A": 42}, "default": {"A": 43}}, [{"A": 42}, {"A": 43}]),
        ({"type": "object", "default": {"A": 42}}, [{"A": 42}]),
        ({"type": "object", "examples": [{"A": 42}, {"B": 43}]}, [{"A": 42}, {"B": 43}]),
        (
            {
                "type": "object",
                "properties": {"foo": True},
                "required": ["foo"],
            },
            [
                {"foo": ANY},
                {"foo": ANY},
                {"foo": ANY},
                {"foo": ANY},
                {"foo": ANY},
                {"foo": ANY},
                {"foo": ANY},
            ],
        ),
        (
            {
                "type": "object",
                "properties": {"foo": {}},
                "required": ["foo"],
            },
            [
                {"foo": ANY},
                {"foo": ANY},
                {"foo": ANY},
                {"foo": ANY},
                {"foo": ANY},
                {"foo": ANY},
                {"foo": ANY},
            ],
        ),
        (
            {
                "type": "object",
                "properties": {"foo": {"type": "integer", "example": 42}},
                "required": ["foo"],
            },
            [
                {"foo": 42},
            ],
        ),
        (
            {
                # No `type`
                "properties": {"foo": {"type": "integer", "example": 42}},
                "required": ["foo"],
            },
            [
                {"foo": 42},
            ],
        ),
        # Nested object declared with just `properties` (no `type: object`):
        # per-property example must lift into the template, not just appear in per-property variants.
        (
            {
                "type": "object",
                "properties": {
                    "settings": {"properties": {"active": {"type": "boolean", "example": True}}},
                },
                "required": ["settings"],
            },
            [
                {"settings": {"active": True}},
                {"settings": {}},
                {"settings": {"active": False}},
            ],
        ),
        (
            {
                "type": "object",
                "properties": {"foo": {"type": "integer", "default": 42}},
                "required": ["foo"],
            },
            [
                {"foo": 42},
            ],
        ),
        (
            {
                "type": "object",
                "properties": {"foo": {"type": "integer", "examples": [42, 43]}},
                "required": ["foo"],
            },
            [
                {"foo": 42},
                {"foo": 43},
            ],
        ),
        (
            {
                "type": "object",
                "required": ["foo"],
                "properties": {
                    "foo": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["bar"],
                            "properties": {
                                "bar": {
                                    "allOf": [
                                        {
                                            "type": "string",
                                        },
                                        {
                                            "minLength": 1,
                                            "maxLength": 100,
                                        },
                                    ]
                                },
                            },
                        },
                    }
                },
            },
            [
                {"foo": []},
                {
                    "foo": [
                        {
                            "bar": "0",
                        },
                    ],
                },
                {
                    "foo": [
                        {
                            "bar": "00",
                        },
                    ],
                },
                {
                    "foo": [
                        {
                            "bar": "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
                        },
                    ],
                },
                {
                    "foo": [
                        {
                            "bar": "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
                        },
                    ],
                },
            ],
        ),
        (
            {
                "type": "object",
                "required": ["foo"],
                "properties": {
                    "foo": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
            },
            [
                {"foo": []},
                {
                    "foo": [
                        "",
                    ],
                },
            ],
        ),
        (
            {
                "type": "object",
                "properties": {"foo": {"type": "integer"}, "bar": {"type": "string"}},
                "required": ["foo"],
            },
            [
                {"bar": "", "foo": 0},
                {"foo": 0},
            ],
        ),
        (
            {
                "type": "object",
                "properties": {
                    "foo-1": {
                        "type": "object",
                        "properties": {"foo-2": {"type": "integer"}},
                    }
                },
                "required": ["foo-1"],
            },
            [
                {"foo-1": {"foo-2": 0}},
                {"foo-1": {}},
            ],
        ),
        (
            {
                "type": "object",
                "properties": {
                    "foo-1": {
                        "type": "object",
                        "properties": {
                            "foo-2": {
                                "type": "object",
                                "properties": {
                                    "foo-3": {"type": "integer"},
                                },
                            },
                        },
                    }
                },
                "required": ["foo-1"],
            },
            [
                {"foo-1": {"foo-2": {"foo-3": 0}}},
                {"foo-1": {}},
                {"foo-1": {"foo-2": {}}},
            ],
        ),
        (
            {
                "type": "object",
                "properties": {
                    "foo-1": {"type": "integer", "minimum": 2},
                    "foo-2": {"type": "string", "minLength": 2},
                },
                "required": ["foo-1"],
            },
            [
                {"foo-1": 2, "foo-2": "00"},
                {"foo-1": 2},
                {"foo-1": 3, "foo-2": "00"},
                {"foo-1": 2, "foo-2": "000"},
            ],
        ),
        # 3 properties, 2 required
        (
            {
                "type": "object",
                "properties": {
                    "req1": {"type": "string"},
                    "req2": {"type": "integer"},
                    "opt1": {"type": "string"},
                },
                "required": ["req1", "req2"],
            },
            [
                {"req1": "", "req2": 0, "opt1": ""},
                {"req1": "", "req2": 0},
            ],
        ),
        # 6 properties, 2 required
        (
            {
                "type": "object",
                "properties": {
                    "req1": {"type": "string"},
                    "req2": {"type": "integer"},
                    "opt1": {"type": "string"},
                    "opt2": {"type": "number"},
                    "opt3": {"type": "array"},
                    "opt4": {"type": "boolean"},
                },
                "required": ["req1", "req2"],
            },
            [
                {"req1": "", "req2": 0, "opt1": "", "opt2": 0.0, "opt3": [None, None], "opt4": False},
                {"req1": "", "req2": 0, "opt1": ""},
                {"req1": "", "req2": 0, "opt2": 0.0},
                {"req1": "", "req2": 0, "opt3": [None, None]},
                {"req1": "", "req2": 0, "opt4": False},
                {"req1": "", "req2": 0, "opt1": "", "opt2": 0.0},
                {"req1": "", "req2": 0, "opt1": "", "opt2": 0.0, "opt3": [None, None]},
                {"req1": "", "req2": 0},
                {"opt1": "", "opt2": 0.0, "opt3": [None, None], "opt4": True, "req1": "", "req2": 0},
            ],
        ),
        # Nested object with optional properties
        (
            {
                "type": "object",
                "properties": {
                    "req1": {"type": "string"},
                    "opt1": {
                        "type": "object",
                        "properties": {
                            "nested_req": {"type": "integer"},
                            "nested_opt": {"type": "boolean"},
                        },
                        "required": ["nested_req"],
                    },
                },
                "required": ["req1"],
            },
            [
                {"req1": "", "opt1": {"nested_req": 0, "nested_opt": False}},
                {"req1": ""},
                {"req1": "", "opt1": {"nested_req": 0}},
                {"req1": "", "opt1": {"nested_req": 0, "nested_opt": True}},
            ],
        ),
        # Object with all optional properties
        (
            {
                "type": "object",
                "properties": {
                    "opt1": {"type": "string"},
                    "opt2": {"type": "integer"},
                    "opt3": {"type": "boolean"},
                },
            },
            [
                {"opt1": "", "opt2": 0, "opt3": False},
                {"opt1": ""},
                {"opt2": 0},
                {"opt3": False},
                {"opt1": "", "opt2": 0},
                {},
                {"opt1": "", "opt2": 0, "opt3": True},
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "maxItems": 5},
            [
                [0],
                [],
                [0, 0, 0, 0, 0],
                [0, 0, 0, 0],
            ],
        ),
        # Multi-branch items must be exercised individually; boundary-size arrays
        # repeat one branch and miss the other.
        (
            {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "anyOf": [
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["a"],
                            "properties": {"a": {"type": "string"}},
                        },
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["b"],
                            "properties": {"b": {"type": "string"}},
                        },
                    ],
                },
            },
            [
                [{"a": ""}],
                [],
                [{"a": ""}, {"a": ""}, {"a": ""}],
                [{"a": ""}, {"a": ""}],
                [{"b": ""}],
            ],
        ),
        (
            {"type": "array", "items": {"enum": ["FOO"]}},
            [["FOO"], []],
        ),
        (
            {"type": "array", "items": {"enum": ["FOO"]}, "minItems": 1},
            [
                ["FOO"],
                ["FOO", "FOO"],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "example": [1, 2, 3]},
            [
                [1, 2, 3],
                [0],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "example": [1, 2, 3], "default": [1, 2, 3]},
            [
                [1, 2, 3],
                [0],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "example": [1, 2, 3], "default": [4, 5, 6]},
            [
                [1, 2, 3],
                [4, 5, 6],
                [0],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "default": [1, 2, 3]},
            [
                [1, 2, 3],
                [0],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "examples": [[1, 2, 3], [4, 5, 6]]},
            [
                [1, 2, 3],
                [4, 5, 6],
                [0],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "minItems": 2},
            [
                [0, 0],
                [0, 0, 0],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 5},
            [
                [0, 0],
                [0, 0, 0],
                [0, 0, 0, 0, 0],
                [0, 0, 0, 0],
            ],
        ),
        (
            {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"foo": {"type": "integer", "minimum": 5}},
                    "required": ["foo"],
                },
                "minItems": 1,
                "maxItems": 2,
            },
            [
                [{"foo": 5}],
                [{"foo": 5}, {"foo": 5}],
                [{"foo": 6}],
            ],
        ),
        (
            {"type": "array", "items": [{"type": "integer"}, {"type": "string"}], "minItems": 2, "maxItems": 5},
            [
                [0, ""],
                [0, "", None],
                [0, "", None, None, None],
                [0, "", None, None],
            ],
        ),
        # Single anyOf subschema
        ({"anyOf": [{"type": "integer"}]}, [0]),
        ({"anyOf": [{"type": "boolean"}]}, [True, False]),
        # Multiple anyOf subschemas
        ({"anyOf": [{"type": "integer", "minimum": 2}, {"type": "boolean"}]}, [2, 3, True, False]),
        ({"anyOf": [{"type": "integer"}, {"type": "string"}]}, [0, ""]),
        ({"anyOf": [{"type": "boolean"}, {"type": "string"}]}, [True, False, ""]),
        # Nested anyOf
        (
            {
                "anyOf": [
                    {"type": "integer", "minimum": 2},
                    {
                        "anyOf": [
                            {"type": "boolean"},
                            {"type": "string"},
                        ]
                    },
                ]
            },
            [2, 3, True, False, ""],
        ),
        (
            {
                "anyOf": [
                    {"type": "integer", "minimum": 2},
                    {
                        "anyOf": [
                            {"type": "boolean"},
                            {"type": "string"},
                            {"type": "null"},
                        ]
                    },
                ]
            },
            [2, 3, True, False, "", None],
        ),
        # anyOf with other keywords
        (
            {
                "anyOf": [
                    {"type": "integer", "minimum": 5},
                    {"type": "integer", "maximum": 10},
                ]
            },
            [5, 6, 10, 9],
        ),
        # Single allOf subschema
        ({"allOf": [{"type": "integer"}]}, [0]),
        ({"allOf": [{"type": "boolean"}]}, [True, False]),
        # Multiple allOf subschemas
        (
            {
                "allOf": [
                    {"type": "integer"},
                    {"minimum": 5},
                ]
            },
            [5, 6],
        ),
        (
            {
                "allOf": [
                    {"type": "string"},
                    {"minLength": 3},
                ]
            },
            ["000", "0000"],
        ),
        (
            {
                "allOf": [
                    {"type": "integer"},
                    {"minimum": 5},
                    {"maximum": 10},
                ]
            },
            [5, 6, 10, 9],
        ),
        # Nested allOf
        (
            {
                "allOf": [
                    {"type": "integer"},
                    {
                        "allOf": [
                            {"minimum": 5},
                            {"maximum": 10},
                        ]
                    },
                ]
            },
            [5, 6, 10, 9],
        ),
        (
            {
                "allOf": [
                    {"type": "string"},
                    {
                        "allOf": [
                            {"minLength": 3},
                            {"maxLength": 5},
                        ]
                    },
                ]
            },
            ["000", "0000", "00000"],
        ),
        # allOf with other keywords
        (
            {
                "allOf": [
                    {"type": "integer"},
                    {"minimum": 5},
                    {"maximum": 10},
                ],
                "exclusiveMinimum": 5,
            },
            [6, 7, 10, 9],
        ),
        # Unsatisfiable allOf - PCRE pattern not supported by Python regex
        (
            {
                "allOf": [
                    {"type": "string", "pattern": "^\\p{Alnum}$"},
                    {"maxLength": 160},
                ]
            },
            [],
        ),
        # Unsatisfiable allOf - invalid pattern type
        (
            {
                "allOf": [
                    {"type": "string", "pattern": 0.0},
                    {"maxLength": 160},
                ]
            },
            [],
        ),
    ],
)
def test_positive_other(pctx, schema, expected):
    assert_covers(pctx, schema, expected)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            # These are query parameters and strings are not possible to negate
            {
                "properties": {
                    "foo": {"type": "string"},
                    "bar": {"type": "string"},
                },
                "required": ["foo", "bar"],
            },
            [
                {
                    "bar": "",
                },
                {
                    "foo": "",
                },
            ],
        ),
        (
            {
                "properties": {
                    "foo": {"type": "string", "maxLength": 3},
                    "bar": {"type": "string", "maxLength": 3},
                },
                "required": ["foo", "bar"],
            },
            [
                {
                    "bar": "",
                    "foo": "true",
                },
                {
                    "bar": "",
                    "foo": "null",
                },
                {
                    "bar": "",
                    "foo": ["null", "null"],
                },
                {
                    "bar": "",
                    "foo": "0000",
                },
                {
                    "bar": "true",
                    "foo": "",
                },
                {
                    "bar": "null",
                    "foo": "",
                },
                {
                    "bar": ["null", "null"],
                    "foo": "",
                },
                {
                    "bar": "0000",
                    "foo": "",
                },
                {
                    "bar": "",
                },
                {
                    "foo": "",
                },
            ],
        ),
        (
            {
                "properties": {
                    "foo": {"type": "string", "maxLength": 3},
                    "bar": {"type": "string", "maxLength": 3},
                },
            },
            [
                {
                    "bar": "",
                    "foo": "true",
                },
                {
                    "bar": "",
                    "foo": "null",
                },
                {
                    "bar": "",
                    "foo": ["null", "null"],
                },
                {
                    "bar": "",
                    "foo": "0000",
                },
                {
                    "bar": "true",
                    "foo": "",
                },
                {
                    "bar": "null",
                    "foo": "",
                },
                {
                    "bar": ["null", "null"],
                    "foo": "",
                },
                {
                    "bar": "0000",
                    "foo": "",
                },
            ],
        ),
        (
            {
                "properties": {
                    "foo": {"type": "string", "maxLength": 3},
                },
                "additionalProperties": False,
            },
            [
                {
                    "foo": "true",
                },
                {
                    "foo": "null",
                },
                {
                    "foo": ["null", "null"],
                },
                {
                    "foo": "0000",
                },
                {
                    "foo": "",
                    "x-schemathesis-unknown-property": 42,
                },
            ],
        ),
    ],
)
def test_negative_objects(nctx, schema, expected):
    assert_covers_negative(nctx, schema, expected)


SCHEMA_WITH_PATTERN = {"minLength": 2, "pattern": "^A{2}$"}


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        # Top-level pattern
        (SCHEMA_WITH_PATTERN, ["A", "00"]),
        # Pattern inside properties
        ({"properties": {"username": SCHEMA_WITH_PATTERN}}, [{"username": "A"}, {"username": "00"}]),
        # Pattern inside items
        ({"items": SCHEMA_WITH_PATTERN}, [["A"], ["00"]]),
        # Pattern inside nested properties
        (
            {
                "properties": {"user": {"properties": {"id": SCHEMA_WITH_PATTERN}}},
            },
            [{"user": {"id": "A"}}, {"user": {"id": "00"}}],
        ),
        # Pattern inside items of an array property
        (
            {
                "properties": {"tags": {"items": SCHEMA_WITH_PATTERN}},
            },
            [{"tags": ["A"]}, {"tags": ["00"]}],
        ),
        # Multiple patterns in different locations
        (
            {
                "properties": {
                    "id": SCHEMA_WITH_PATTERN,
                    "items": {"items": SCHEMA_WITH_PATTERN},
                },
                "patternProperties": {"^meta_": SCHEMA_WITH_PATTERN},
            },
            [
                {"id": "A", "items": None},
                {"id": "00", "items": None},
                {"id": None, "items": ["A"]},
                {"id": None, "items": ["00"]},
                {"id": None, "items": None, "meta_": "A"},
                {"id": None, "items": None, "meta_": "00"},
            ],
        ),
        # Pattern in combination with other keywords
        ({"pattern": "^A{2}$", "minLength": 3, "maxLength": 20}, ["000", "AA", "a" * 21]),
        # Pattern inside allOf
        ({"allOf": [SCHEMA_WITH_PATTERN, {"minLength": 5}]}, ["AA", "00000"]),
    ],
)
def test_negative_pattern(nctx, schema, expected):
    covered = cover_schema(nctx, schema)
    assert covered == expected
    assert_unique(covered)


# Nothing satisfies `{"not": {}}`, so the values covering it are described by what they are not, not by their type.
@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"not": {}}, "Value is not allowed"),
        (
            {"type": "object", "properties": {"a": {"not": {}}}},
            "a: Value is not allowed",
        ),
    ],
    ids=["root", "property"],
)
def test_negative_forbidden_schema_description(nctx, schema, expected):
    descriptions = {value.description for value in cover_schema_iter(nctx, schema)}
    assert expected in descriptions, descriptions


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            {"type": "object", "propertyNames": {"maxLength": 3}},
            [0, 0.5, "true", "null", "AAA", ["null", "null"], {"0000": ""}],
        ),
        (
            {"type": "object", "propertyNames": {"pattern": "^[a-z]+$"}},
            [0, 0.5, "true", "null", "AAA", ["null", "null"], {"": ""}],
        ),
        (
            {"type": "object", "propertyNames": {"minLength": 3}},
            [0, 0.5, "true", "null", "AAA", ["null", "null"], {"00": ""}],
        ),
    ],
)
def test_negative_property_names(ctx_factory, schema, expected):
    # `propertyNames` arrived in Draft 6; only dialects whose validator enforces it get the negation.
    nctx = ctx_factory(generation_modes=[GenerationMode.NEGATIVE], validator_cls=jsonschema_rs.Draft202012Validator)
    assert_covers_negative(nctx, schema, expected)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {"a": {"type": "null"}}, "propertyNames": {"pattern": "^[0-9]{3}$"}},
        {"type": "object", "properties": {"abc": {"type": "null"}}, "propertyNames": {"maxLength": 2}},
        {
            "type": "object",
            "properties": {"a": {"type": "null"}, "bbb": {"type": "boolean"}},
            "propertyNames": {"minLength": 3},
        },
    ],
    ids=["pattern", "max-length", "one-name-admitted"],
)
def test_positive_object_respects_property_names(ctx_factory, schema):
    ctx = ctx_factory(
        location=ParameterLocation.BODY,
        generation_modes=[GenerationMode.POSITIVE],
        validator_cls=jsonschema_rs.Draft202012Validator,
    )
    validator = jsonschema_rs.Draft202012Validator(schema)
    values = cover_schema(ctx, schema)
    assert values
    for value in values:
        assert validator.is_valid(value), value


# Draft 4 validators ignore `propertyNames`, so the properties beside it stay coverable.
def test_positive_object_covers_properties_beside_ignored_property_names(ctx_factory):
    schema = {"type": "object", "properties": {"a": {"type": "null"}}, "propertyNames": {"pattern": "^[0-9]{3}$"}}
    ctx = ctx_factory(
        location=ParameterLocation.BODY,
        generation_modes=[GenerationMode.POSITIVE],
        validator_cls=jsonschema_rs.Draft4Validator,
    )
    assert {"a": None} in cover_schema(ctx, schema)


def test_positive_pattern(pctx):
    schema = {"pattern": r"^[a-zA-Z0-9]{2,4}-\d{4,15}$", "minLength": 7, "maxLength": 20, "type": "string"}
    assert_covers(pctx, schema, ["0000-0000", "00-0000", "00-00000", "0000-000000000000000", "000-000000000000000"])


def test_positive_pattern_with_wildcard_prefix_and_digit_limit(pctx):
    # Regression: https://github.com/schemathesis/schemathesis/issues/3154
    # Pattern with `.*` prefix and bounded digit quantifier `{1,10}`.
    # st.from_regex(pattern) without fullmatch=True allowed strings with a trailing \n
    # (Python's `$` matches before \n; JSON Schema / ECMAScript `$` does not),
    # producing values that fail the schema but passed the Python-side filter,
    # causing the hook to see a schema-conformant value while the URL carried an
    # invalid one.
    schema = {"type": "string", "pattern": r"^.*Id,([0-9]{1,10})$"}
    assert_conform(cover_schema(pctx, schema), schema)


def test_positive_pattern_with_char_class_and_min_length(pctx):
    # Regression: update_quantifier can't encode minLength into patterns with bare
    # character classes like [a-z] (IN opcode). The .filter() safety net ensures
    # generated strings still respect minLength.
    schema = {"pattern": r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", "minLength": 3, "type": "string"}
    covered = cover_schema(pctx, schema)
    for value in covered:
        assert len(value) >= 3, f"Generated string {value!r} violates minLength=3"


def test_apply_pattern_optimizations_skips_non_keyword_property_names():
    # JSON Schema meta-schemas (e.g. Kubernetes CRD `JSONSchemaProps`) declare sub-schemas
    # whose property *names* happen to be `pattern` / `minLength` / `maxLength`. The walker
    # must skip these — they are sub-schema dicts, not regex strings / integer bounds.
    bundle = {
        "JSONSchemaProps": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "minLength": {"type": "integer", "format": "int64"},
                "maxLength": {"type": "integer", "format": "int64"},
            },
        }
    }
    _apply_pattern_optimizations(bundle, update_quantifier)


def test_negative_pattern_reuse_stamps_current_location(nctx):
    # Two properties share a regex, so the second answers from the first's search - with its own pointer.
    inner = {"type": "string", "pattern": "^[QZ]{4}-memo-probe$"}
    assert [
        (value.value, value.location)
        for value in cover_schema_iter(nctx, {"type": "object", "properties": {"alpha": inner, "beta": dict(inner)}})
        if value.scenario is CoverageScenario.INVALID_PATTERN
    ] == [
        ({"alpha": "", "beta": "QQQQ-memo-probe"}, "/properties/alpha/pattern"),
        ({"alpha": "QQQQ-memo-probe", "beta": ""}, "/properties/beta/pattern"),
    ]


def test_negative_type_reuse_stamps_current_location(nctx):
    # The same property schema recurs at two pointers; the reused type violation must carry its own.
    inner = {"type": "string", "minLength": 3}
    assert [
        (value.value, value.location)
        for value in cover_schema_iter(nctx, {"type": "object", "properties": {"alpha": inner, "beta": dict(inner)}})
        if value.scenario is CoverageScenario.INCORRECT_TYPE and isinstance(value.value, dict)
    ] == [
        ({"alpha": 0, "beta": "000"}, "/properties/alpha/type"),
        ({"alpha": "000", "beta": 0}, "/properties/beta/type"),
    ]


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        (".*", []),
        ("[\\s\\S]", []),
        ("a\\Z", ["0"]),
        ("^[a-z]+$", ["0"]),
    ],
    ids=["matched-by-everything", "matched-by-every-non-empty-string", "unreadable-by-the-validator", "violable"],
)
def test_pattern_violations_only_where_a_value_can_break_the_pattern(nctx, pattern, expected):
    assert (
        scenario_values(nctx, {"type": "string", "minLength": 1, "pattern": pattern}, CoverageScenario.INVALID_PATTERN)
        == expected
    )


def test_no_pattern_violation_for_either_property_sharing_an_unbreakable_pattern(nctx):
    # The second property answers from the first one's exhausted search rather than repeating it.
    inner = {"type": "string", "minLength": 1, "pattern": "[\\s\\S]"}
    schema = {"type": "object", "properties": {"alpha": inner, "beta": dict(inner)}}
    assert scenario_values(nctx, schema, CoverageScenario.INVALID_PATTERN) == []


# Ten optional characters plus a dash, then a fixed 36-character tail: 36 or 47 characters, never anything between.
SPLIT_UUID_PATTERN = r"^([0-9a-f]{10}-|)[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}$"


@pytest.mark.parametrize(
    ("pattern", "length"),
    [
        (r"aws\.partner(/[\.\-_A-Za-z0-9]+){2,}", 256),
        (r"^[a-zA-Z0-9](-*[a-zA-Z0-9])*$", 256),
        (r"^(https?):\/\/([^\s]*)", 2048),
        (r"^(?!\s).+@([a-zA-Z0-9_\-\.]+)\.([a-zA-Z]{2,5})$", 256),
        (SPLIT_UUID_PATTERN, 47),
        # A repeat spanning two lengths blows up into catastrophic backtracking unless the rewrite pins one.
        (r"^([A-Za-z](-|_|.)?)+$", 101),
    ],
)
def test_positive_string_reaches_lengths_far_from_what_the_pattern_emits_naturally(pctx, pattern, length):
    values = cover_schema(pctx, {"type": "string", "pattern": pattern, "minLength": length, "maxLength": length})
    assert values
    for value in values:
        assert len(value) == length, value
        assert re.search(pattern, value), value


@pytest.mark.parametrize(
    ("pattern", "length"),
    [
        (r"^(a+)\1$", 8),
        (r"^(a)(?:\1)*$", 40),
        (r"^(?:ab|abcd)+$", 8),
        (r"^(?:ab|abcd){2}$", 8),
        pytest.param(r"^(?:ab)++$", 40, marks=SKIP_BEFORE_PY11),
        (r"^(?:^)*[a-z]+$", 8),
        (r"^(?:abc){1,2}$", 6),
    ],
)
def test_positive_string_conforms_for_a_pattern_the_length_walk_cannot_pin(pctx, pattern, length):
    # Back-references, possessive runs and gapped repeats resist the rewrite, so the length is drawn
    # for rather than spelled out - and every length here sits above what the pattern emits on its own.
    schema = {"type": "string", "pattern": pattern, "minLength": length, "maxLength": length}
    values = cover_schema(pctx, schema)
    assert values
    assert_conform(values, schema)


@pytest.mark.parametrize(
    "pattern",
    [pytest.param(r"^(?>[a-z]+)[0-9]$", marks=SKIP_BEFORE_PY11), r"^(?=[a-z]{3})[a-z]+$"],
    ids=["atomic", "lookahead"],
)
def test_positive_string_absent_where_a_length_bound_meets_a_shape_neither_path_handles(pctx, pattern):
    # Both patterns generate freely on their own; adding a length the rewrite cannot spell out leaves
    # only drawing and discarding, which never lands.
    assert cover_schema(pctx, {"type": "string", "pattern": pattern}) != []
    assert cover_schema(pctx, {"type": "string", "pattern": pattern, "minLength": 8, "maxLength": 8}) == []


def test_positive_string_skips_a_window_no_length_can_satisfy(pctx):
    # `minLength` above `maxLength` leaves nothing to aim at, and searching for it never ends.
    assert cover_schema(pctx, {"type": "string", "pattern": r"^[a-z]+$", "minLength": 40, "maxLength": 8}) == []


def test_positive_string_skips_a_length_the_pattern_cannot_produce(pctx):
    assert cover_schema(pctx, {"type": "string", "pattern": SPLIT_UUID_PATTERN, "minLength": 46, "maxLength": 46}) == []


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string"},
        {"type": "string", "maxLength": 2147483647},
        {"type": "string", "minLength": 1, "description": "text", "xml": {"name": "X"}},
    ],
    ids=["bare-string", "huge-max-length", "annotations-only"],
)
def test_no_type_violation_when_the_schema_accepts_every_stringified_value(nctx, schema):
    # A query value reaches the server as text, so a schema accepting every string has no type violation.
    assert scenario_values(nctx, schema, CoverageScenario.INCORRECT_TYPE) == []


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "minLength": 2},
        {"type": "string", "maxLength": 8},
        {"type": "string", "enum": ["a"]},
        {"type": "integer"},
    ],
    ids=["min-length", "max-length", "enum", "non-string"],
)
def test_type_violations_break_the_schema_once_stringified(nctx, schema):
    values = scenario_values(nctx, schema, CoverageScenario.INCORRECT_TYPE)
    validator = jsonschema_rs.Draft4Validator(schema)
    assert values and not any(validator.is_valid(str(value)) for value in values)


@pytest.mark.parametrize(
    ("location", "schema", "expected"),
    [
        (ParameterLocation.QUERY, {"type": "string", "pattern": "[0-9]"}, ["true", "null", ["null", "null"]]),
        (ParameterLocation.QUERY, {"type": "string", "minLength": 5}, [0, 0.5, "true", "null"]),
        (ParameterLocation.QUERY, {"type": "string", "enum": ["alpha"]}, [0, 0.5, "true", "null", ["null", "null"]]),
        (ParameterLocation.QUERY, {"type": "integer", "minimum": 5}, [0.5, "true", "null", "AAA", ["null", "null"]]),
        (ParameterLocation.QUERY, {"type": "boolean"}, [2, 0.5, "null", "AAA", ["null", "null"]]),
        (ParameterLocation.PATH, {"type": "string", "pattern": "[0-9]"}, ["true", "null", "null,null"]),
        (ParameterLocation.HEADER, {"type": "string", "pattern": "[0-9]"}, [True, None, [None, None], {}]),
        (ParameterLocation.HEADER, {"type": "string", "maxLength": 3}, [True, None, [None, None]]),
        (ParameterLocation.HEADER, {"type": "boolean"}, [0, 0.5, None, "AAA", [None, None], {}]),
    ],
    ids=[
        "query-pattern",
        "query-min-length",
        "query-enum",
        "query-integer-bound",
        "query-boolean",
        "path-pattern",
        "header-pattern",
        "header-max-length",
        "header-boolean",
    ],
)
def test_type_violations_cover_every_wrong_type_the_schema_turns_down(ctx_factory, location, schema, expected):
    ctx = ctx_factory(location=location, generation_modes=[GenerationMode.NEGATIVE])
    values = scenario_values(ctx, schema, CoverageScenario.INCORRECT_TYPE)
    assert values == expected
    validator = jsonschema_rs.Draft4Validator(schema)
    assert not any(validator.is_valid(str(value)) for value in values)


def test_negative_pattern_with_incompatible_length(nctx):
    schema = {
        "minLength": 6,
        "maxLength": 20,
        "pattern": "^[a-zA-Z]{4}-\\d{4,15}$",
    }
    assert_covers_negative(nctx, schema, ["AAAA-", "a" * 21, "000000"])


def test_negative_multiple_types(nctx):
    schema = {"type": ["integer", "number", "string"]}
    assert not cover_schema(nctx, schema)


def test_positive_multiple_types(pctx):
    # Nullable date-time: string branch must honour `format` (null branch yields `None`).
    schema = {"type": ["string", "null"], "format": "date-time"}
    covered = cover_schema(pctx, schema)
    assert covered == [AnyString(), None]
    assert_conform(covered, schema)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            {
                "allOf": [
                    {"minimum": 5},
                ],
            },
            [4],
        ),
        (
            {
                "allOf": [
                    {"type": "integer"},
                    {"minimum": 5},
                ],
            },
            [4, 0.5, "true", "null", "AAA", ["null", "null"]],
        ),
        (
            {
                "anyOf": [
                    {"minimum": 5},
                    {"type": "string"},
                ],
            },
            [4],
        ),
        (
            {
                "anyOf": [
                    {"minimum": 5},
                    {"type": "string", "maxLength": 5},
                ],
            },
            [4],
        ),
        (
            {
                "allOf": [
                    {
                        "maxLength": 10,
                        "type": "string",
                    },
                    {
                        "anyOf": [
                            {"maxLength": 10},
                            {"type": "null"},
                        ]
                    },
                ]
            },
            # `aaaaaaaaaaa` is the synthesized maxLength violation when the merged allOf can't satisfy length-11.
            ["00000000000", "aaaaaaaaaaa"],
        ),
        (
            {
                "allOf": [
                    {"type": "string", "pattern": 0.0},
                    {"maxLength": 160},
                ]
            },
            [],
        ),
    ],
)
def test_negative_combinators(nctx, schema, expected):
    covered = cover_schema(nctx, schema)
    for exp in expected if isinstance(expected, tuple) else (expected,):
        if covered == exp:
            assert_unique(covered)
            assert_not_conform(covered, schema)
            break
    else:
        pytest.fail(f"Expected value didn't match\nGot: {covered!r}\nExpected: {expected!r}")


@pytest.mark.parametrize(
    ["schema", "expected"],
    [
        (
            {
                "anyOf": [
                    {"type": "number"},
                    {"type": "null"},
                ]
            },
            [
                False,
                "AAA",
                [
                    None,
                    None,
                ],
                {},
            ],
        ),
        (
            {
                "oneOf": [
                    {"type": "number"},
                    {"type": "integer"},
                    {"type": "null"},
                ]
            },
            [
                False,
                "AAA",
                [
                    None,
                    None,
                ],
                {},
                # Matching both, "number" and "integer", hence invalid
                0,
            ],
        ),
    ],
)
def test_negative_one_of(ctx_factory, schema, expected):
    # See GH-2975
    nctx = ctx_factory(
        root_schema=schema,
        location=ParameterLocation.BODY,
        generation_modes=[GenerationMode.NEGATIVE],
        validator_cls=jsonschema_rs.Draft202012Validator,
    )
    covered = cover_schema(nctx, schema)
    assert_not_conform(covered, schema)
    assert covered == expected


@pytest.mark.parametrize(
    "pattern",
    [
        "^[A-Za-z0-9]$|^[A-Za-z0-9][\\w-\\.]*[A-Za-z0-9]$",
        "^[-._\\p{L}\\p{N}]+$",
    ],
)
@pytest.mark.filterwarnings("ignore::UserWarning")
def test_unsupported_patterns(nctx, pattern):
    assert cover_schema(nctx, {"type": "string", "pattern": pattern}) == []
    assert not cover_schema(nctx, {"patternProperties": {pattern: {"type": "string"}}})


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "integer", "format": "int32"}, [0]),
        ({"type": "string", "format": "unknown"}, [""]),
    ],
)
def test_ignoring_unknown_formats(pctx, schema, expected):
    assert cover_schema(pctx, schema) == expected


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "string", "minLength": 5, "maxLength": 10}, {"/minLength", "/maxLength", "/type"}),
        ({"type": "number", "minimum": 0, "maximum": 100}, {"/minimum", "/maximum", "/type"}),
        (
            {"type": "array", "items": {"type": "string", "pattern": "^[a-z]+$"}},
            {"/items/pattern", "/items/type", "/type"},
        ),
        (
            {"type": "object", "properties": {"name": {"type": "string", "minLength": 3}}},
            {"/properties/name/minLength", "/type", "/properties/name/type"},
        ),
        ({"type": "string", "enum": ["red", "green", "blue"]}, {"/enum", "/type"}),
        (
            {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}},
            {"/required", "/type", "/properties/id/type"},
        ),
        ({"type": "string", "format": "email"}, {"/format", "/type"}),
        ({"anyOf": [{"type": "string"}, {"type": "number"}]}, {"/anyOf/1/type"}),
        (
            {"type": "object", "additionalProperties": False},
            {"/additionalProperties", "/type"},
        ),
        (
            {"type": "object", "patternProperties": {"^meta": {"type": "string"}}},
            {"/type"},
        ),
        (
            {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "object",
                        "properties": {
                            "address": {"type": "object", "properties": {"street": {"type": "string", "minLength": 5}}}
                        },
                    }
                },
            },
            {
                "/properties/user/properties/address/properties/street/minLength",
                "/properties/user/properties/address/properties/street/type",
                "/properties/user/properties/address/type",
                "/properties/user/type",
                "/type",
            },
        ),
    ],
)
def test_negative_value_locations(nctx, schema, expected):
    assert {v.location for v in cover_schema_iter(nctx, schema)} == expected


@pytest.mark.parametrize(
    "ctx, expected",
    (
        (
            "pctx",
            [
                {"name": "0"},
                {"name": "00"},
                {"name": "0" * 4000},
                {"name": "0" * 3999},
            ],
        ),
        (
            "nctx",
            [
                {"name": "0" * 4001},
                {
                    "name": "",
                },
                {},
                0,
                0.5,
                "true",
                "null",
                "AAA",
                [
                    "null",
                    "null",
                ],
            ],
        ),
    ),
)
def test_generate_large_string(request, ctx, expected):
    ctx = request.getfixturevalue(ctx)
    schema = {
        "properties": {
            "name": {"maxLength": 4000, "minLength": 1, "pattern": "^[\\w\\W]+$", "type": "string"},
        },
        "required": ["name"],
        "type": "object",
    }
    assert cover_schema(ctx, schema) == expected


def test_generate_very_large_string(nctx):
    schema = {
        "properties": {
            "name": {"maxLength": 10000, "minLength": 1, "pattern": "^[\\w\\W]*$", "type": "string"},
        },
        "required": ["name"],
        "type": "object",
    }

    assert 10001 in {
        len(item["name"])
        for item in cover_schema(nctx, schema)
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def test_oversized_string_still_matches_an_ambiguous_pattern(nctx):
    # A group spanning two lengths has no quantifier range that pins one, so the length has to be
    # worked into the pattern rather than padded on after generating a shorter match.
    pattern = "^([a-z]{2}|[a-z]{3})+$"
    schema = {"type": "string", "pattern": pattern, "maxLength": 10}
    values = scenario_values(nctx, schema, CoverageScenario.STRING_ABOVE_MAX_LENGTH)
    assert values
    for value in values:
        assert len(value) == 11, value
        assert re.search(pattern, value), value


def test_large_string_with_complex_pattern(nctx):
    schema = {
        "maxLength": 4000,
        "minLength": 1,
        "pattern": "^question\\.custom\\.[^,]+(?:,question\\.custom\\.[^,]+)*$",
        "type": "string",
    }
    assert cover_schema(nctx, schema) == [
        "0" * 4001,
        "",
        "0",
        0,
        0.5,
        "true",
        "null",
        [
            "null",
            "null",
        ],
    ]


def test_deeply_nested_values(pctx):
    schema = {
        "properties": {
            "customer": {
                "properties": {
                    "contacts": {
                        "properties": {
                            "contact": {
                                "items": {
                                    "properties": {
                                        "name": {
                                            "maxLength": 10,
                                            "minLength": 1,
                                            "type": "string",
                                        },
                                        "phone": {
                                            "items": {
                                                "properties": {
                                                    "phoneNumber": {
                                                        "maxLength": 15,
                                                        "minLength": 1,
                                                        "type": "string",
                                                    }
                                                },
                                                "type": "object",
                                            },
                                            "type": "array",
                                        },
                                    },
                                    "required": ["name"],
                                    "type": "object",
                                },
                                "type": "array",
                            }
                        },
                        "type": "object",
                    }
                },
                "type": "object",
            },
        },
        "type": "object",
    }
    assert cover_schema(pctx, schema) == [
        {
            "customer": {
                "contacts": {
                    "contact": [],
                },
            },
        },
        {},
        {
            "customer": {},
        },
        {
            "customer": {
                "contacts": {},
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0",
                            "phone": [],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0",
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "00",
                            "phone": [],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0000000000",
                            "phone": [],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "000000000",
                            "phone": [],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0",
                            "phone": [
                                {
                                    "phoneNumber": "0",
                                },
                            ],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0",
                            "phone": [
                                {},
                            ],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0",
                            "phone": [
                                {
                                    "phoneNumber": "00",
                                },
                            ],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0",
                            "phone": [
                                {
                                    "phoneNumber": "000000000000000",
                                },
                            ],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0",
                            "phone": [
                                {
                                    "phoneNumber": "00000000000000",
                                },
                            ],
                        },
                    ],
                },
            },
        },
    ]


def test_large_arrays(nctx):
    schema = {
        "properties": {
            "questions": {
                "items": {
                    "properties": {
                        "id": {"minLength": 6, "pattern": "^[0-9]+$", "type": "string"},
                    },
                    "required": ["id"],
                    "type": "object",
                    "additionalProperties": False,
                },
                "maxItems": 500,
                "minItems": 0,
                "type": "array",
            },
        },
        "type": "object",
    }

    assert 501 in {
        len(item["questions"])
        for item in cover_schema(nctx, schema)
        if isinstance(item, dict) and isinstance(item["questions"], list)
    }


def test_large_arrays_nested(nctx):
    schema = {
        "properties": {
            "questions": {
                "items": {
                    "properties": {
                        "answers": {
                            "items": {
                                "type": "null",
                            },
                            "maxItems": 100,
                            "type": "array",
                        },
                        "id": {"minLength": 6, "pattern": "^[0-9]+$", "type": "string"},
                    },
                    "required": ["id"],
                    "type": "object",
                },
                "maxItems": 500,
                "minItems": 1,
                "type": "array",
            },
        },
        "required": ["questions"],
        "type": "object",
    }

    assert 501 in {
        len(item["questions"])
        for item in cover_schema(nctx, schema)
        if isinstance(item, dict) and isinstance(item.get("questions"), list)
    }


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        # Basic $ref to simple type in $defs
        (
            {"$defs": {"SimpleString": {"type": "string", "minLength": 2}}, "$ref": "#/$defs/SimpleString"},
            ["00", "000"],
        ),
        # $ref in object properties
        (
            {
                "$defs": {"UserId": {"type": "integer", "minimum": 1}},
                "type": "object",
                "properties": {"id": {"$ref": "#/$defs/UserId"}},
                "required": ["id"],
            },
            [{"id": 1}, {"id": 2}],
        ),
        # $ref in array items
        (
            {
                "$defs": {"Tag": {"type": "string", "enum": ["red", "blue"]}},
                "type": "array",
                "items": {"$ref": "#/$defs/Tag"},
            },
            [["red"], [], ["blue"]],
        ),
        # Nested $refs - reference pointing to another reference
        (
            {
                "$defs": {
                    "BaseString": {"type": "string"},
                    "LimitedString": {"allOf": [{"$ref": "#/$defs/BaseString"}, {"maxLength": 3}]},
                },
                "$ref": "#/$defs/LimitedString",
            },
            ["000", "00"],
        ),
        # $ref in combinators
        (
            {
                "$defs": {
                    "PositiveInt": {"type": "integer", "minimum": 1},
                    "NegativeInt": {"type": "integer", "maximum": -1},
                },
                "anyOf": [
                    {"$ref": "#/$defs/PositiveInt"},
                    {"$ref": "#/$defs/NegativeInt"},
                ],
            },
            [1, 2, -1, -2],
        ),
        # $ref to boolean schema
        (
            {
                "$defs": {"Anything": True},
                "$ref": "#/$defs/Anything",
            },
            [
                None,
                True,
                False,
                "",
                0,
                [
                    None,
                    None,
                ],
                {},
            ],
        ),
    ],
    ids=["basic", "properties", "array", "nested", "combinators", "bool"],
)
def test_positive_bundled_schema_refs(pctx, schema, expected):
    pctx.root_schema = schema
    assert_covers(pctx, schema, expected)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        # Basic $ref negative case
        (
            {"$defs": {"PositiveInt": {"type": "integer", "minimum": 1}}, "$ref": "#/$defs/PositiveInt"},
            [0.5, "true", "null", "AAA", ["null", "null"], 0],
        ),
        # $ref in object properties - missing required property
        (
            {
                "$defs": {"RequiredString": {"type": "string", "minLength": 3}},
                "type": "object",
                "properties": {"name": {"$ref": "#/$defs/RequiredString"}},
                "required": ["name"],
            },
            [
                0,
                0.5,
                "true",
                "null",
                "AAA",
                ["null", "null"],
                {"name": 0},
                {"name": "00"},
                {},
            ],
        ),
        # $ref with complex validation
        (
            {
                "$defs": {"Email": {"type": "string", "format": "email", "maxLength": 10}},
                "type": "object",
                "properties": {"contact": {"$ref": "#/$defs/Email"}},
            },
            [
                0,
                0.5,
                "true",
                "null",
                "AAA",
                [
                    "null",
                    "null",
                ],
                {
                    "contact": 0,
                },
                {
                    "contact": 0.5,
                },
                {
                    "contact": "true",
                },
                {
                    "contact": "null",
                },
                {
                    "contact": [
                        "null",
                        "null",
                    ],
                },
                {"contact": ""},
                {"contact": AnyString()},
            ],
        ),
    ],
    ids=["basic", "properties", "nested"],
)
def test_negative_bundled_schema_refs(nctx, schema, expected):
    nctx.root_schema = schema
    assert_covers_negative(nctx, schema, expected)


@pytest.mark.parametrize(
    ("schema", "min_expected_negative_count", "should_have_positive"),
    [
        # "not" schema: anything except strings with maxLength=10
        # Negative cases are values that MATCH the inner schema (strings ≤10 chars)
        ({"not": {"type": "string", "maxLength": 10}}, 1, True),
        # "not" schema: anything except null
        # Negative case is null (matches inner schema)
        ({"not": {"type": "null"}}, 1, True),
        # "not" schema with empty inner schema (nothing is valid)
        # All values match the empty schema, so all are negative for "not"
        # No positive cases possible (can't violate an empty schema)
        ({"not": {}}, 1, False),
        # "not" schema with type constraint
        # Negative case is an integer (matches inner schema)
        ({"not": {"type": "integer"}}, 1, True),
    ],
    ids=["maxLength", "null", "empty", "integer"],
)
def test_not_schema_generation_modes_consistency(
    ctx_factory, schema, min_expected_negative_count, should_have_positive
):
    # Test with NEGATIVE mode only
    nctx = ctx_factory(generation_modes=[GenerationMode.NEGATIVE])
    negative_mode_values = list(cover_schema_iter(nctx, schema))

    negative_only_negative = [v for v in negative_mode_values if v.generation_mode == GenerationMode.NEGATIVE]
    negative_only_positive = [v for v in negative_mode_values if v.generation_mode == GenerationMode.POSITIVE]

    # Test with ALL modes (both POSITIVE and NEGATIVE)
    all_ctx = ctx_factory(generation_modes=[GenerationMode.POSITIVE, GenerationMode.NEGATIVE])
    all_mode_values = list(cover_schema_iter(all_ctx, schema))

    all_negative = [v for v in all_mode_values if v.generation_mode == GenerationMode.NEGATIVE]
    all_positive = [v for v in all_mode_values if v.generation_mode == GenerationMode.POSITIVE]

    # NEGATIVE mode should generate the same negative cases as ALL mode
    negative_only_count = len(negative_only_negative)
    all_negative_count = len(all_negative)

    # Both should have at least the minimum expected negative count
    assert negative_only_count >= min_expected_negative_count, (
        f"Expected at least {min_expected_negative_count} negative cases in negative mode, "
        f"but got {negative_only_count}"
    )
    assert all_negative_count >= min_expected_negative_count, (
        f"Expected at least {min_expected_negative_count} negative cases in all mode, but got {all_negative_count}"
    )

    # The number of negative cases should be equal (the main bug we're testing)
    assert negative_only_count == all_negative_count, (
        f"Negative mode generated {negative_only_count} negative cases, "
        f"but all mode generated {all_negative_count} negative cases. "
    )

    # ALL mode should have additional positive cases when expected
    if should_have_positive:
        assert len(all_positive) > 0, "All mode should generate positive cases for 'not' schemas"

    # NEGATIVE mode should not generate positive cases when only negative mode is requested
    assert len(negative_only_positive) == 0, (
        f"Negative mode should not generate positive cases, but got {len(negative_only_positive)}"
    )


@pytest.mark.parametrize(
    ("schema", "ty"),
    [
        (
            {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            "object",
        ),
        (
            {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "array",
        ),
        (
            {"properties": {"name": {"type": "string"}}, "required": ["name"]},
            None,
        ),
    ],
    ids=["object", "array", "implicit-object"],
)
def test_cover_positive_for_type_skips_template_generation_in_negative_mode(ctx_factory, schema, ty, monkeypatch):
    ctx = ctx_factory(generation_modes=[GenerationMode.NEGATIVE])
    calls = 0
    original = CoverageContext.generate_from_schema

    def wrapped(self, schema):
        nonlocal calls
        calls += 1
        return original(self, schema)

    monkeypatch.setattr(CoverageContext, "generate_from_schema", wrapped)

    assert list(_cover_positive_for_type(ctx, schema, ty)) == []
    assert calls == 0


def test_generate_from_schema_uses_cache_and_returns_fresh_copy(ctx_factory, monkeypatch):
    ctx = ctx_factory(generation_modes=[GenerationMode.NEGATIVE])
    calls = 0

    def wrapped(self, strategy):
        nonlocal calls
        calls += 1
        return {"cached": True}

    monkeypatch.setattr(CoverageContext, "generate_from", wrapped)

    schema_1 = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": {"type": "string"},
    }
    schema_2 = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": {"type": "string"},
    }

    first = ctx.generate_from_schema(schema_1)
    first["mutated"] = True
    second = ctx.generate_from_schema(schema_2)

    assert calls == 1
    assert second == {"cached": True}


def test_generate_from_schema_reflects_bundle_mutations(ctx_factory):
    # Without pattern rewriting the bundle is used as-is, so replacing a definition in it is visible to later draws.
    schema = {
        "oneOf": [{"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1"}],
        BUNDLE_STORAGE_KEY: {"schema1": {"type": "integer"}},
    }

    def make_context():
        return ctx_factory(root_schema=schema, generation_modes=[GenerationMode.POSITIVE], update_pattern=None)

    assert isinstance(make_context().generate_from_schema(schema), int)

    schema[BUNDLE_STORAGE_KEY]["schema1"] = {"type": "string"}

    assert isinstance(make_context().generate_from_schema(schema), str)


def test_generate_from_schema_caches_unsatisfiable_verdict(pctx):
    # JS-style `/.../`-wrapped pattern can never match; the second call must still raise
    # Unsatisfiable, served from the cached sentinel rather than re-running Hypothesis.
    schema = {"type": "string", "pattern": "/^x$/", "format": "date-time"}
    with pytest.raises(Unsatisfiable):
        pctx.generate_from_schema(schema)
    with pytest.raises(Unsatisfiable):
        pctx.generate_from_schema(schema)


def test_generate_from_schema_serves_cached_value(pctx):
    # Two calls on identical schema/context: second must equal the first, served from cache.
    assert pctx.generate_from_schema({"type": "string"}) == pctx.generate_from_schema({"type": "string"})


@pytest.mark.parametrize(
    "schema",
    [
        # maxLength shorter than the pattern's minimum match (30 chars)
        {
            "type": "string",
            "pattern": "arn:aws:kinesisvideo:[a-z0-9-]+:[0-9]+:[a-z]+/[a-zA-Z0-9_.-]+/[0-9]+",
            "minLength": 1,
            "maxLength": 5,
        },
        # minLength exceeds a fixed-length pattern's max (34 chars)
        {"type": "string", "pattern": "^AC[0-9a-fA-F]{32}$", "minLength": 50, "maxLength": 100},
        # maxLength below a fixed-length pattern's min
        {"type": "string", "pattern": "^AC[0-9a-fA-F]{32}$", "maxLength": 10},
    ],
)
def test_generate_from_schema_pattern_length_incompatible(pctx, schema):
    # Pattern bounds make the length constraint structurally impossible.
    with pytest.raises(Unsatisfiable):
        pctx.generate_from_schema(schema)


def test_positive_string_skips_infeasible_boundary_lengths(pctx):
    # Pattern minimum match is ~30 chars; minLength=1 boundary variant (exact len=1) is
    # structurally impossible and must be skipped instead of timing out in Hypothesis.
    schema = {
        "type": "string",
        "pattern": "arn:aws:kinesisvideo:[a-z0-9-]+:[0-9]+:[a-z]+/[a-zA-Z0-9_.-]+/[0-9]+",
        "minLength": 1,
        "maxLength": 1024,
    }
    covered = list(_positive_string(pctx, schema))
    for value in covered:
        assert isinstance(value.value, str)
    assert_conform(covered, schema)


@pytest.mark.parametrize(
    "schema",
    [
        # Boundary-length variants only generate quickly once the bound is baked into the quantifier.
        {"type": "string", "pattern": "[A-Za-z][A-Za-z0-9_.-]*", "minLength": 1, "maxLength": 255},
        {"type": "string", "pattern": "[a-z0-9_][a-z0-9_-]+[a-z0-9_]", "minLength": 3, "maxLength": 63},
        {"type": "string", "pattern": "[a-z][0-9]+", "minLength": 4, "maxLength": 12},
    ],
)
def test_unanchored_pattern_boundary_lengths_conform(pctx, schema):
    covered = list(_positive_string(pctx, schema))
    assert covered
    assert_conform(covered, schema)


def test_path_pattern_with_literal_slash_is_unsatisfiable(ctx_factory):
    # Pattern's literal / conflicts with the path-parameter transport constraint.
    path_ctx = ctx_factory(location=ParameterLocation.PATH, generation_modes=[GenerationMode.POSITIVE])
    schema = {
        "type": "string",
        "pattern": "arn:aws:kinesisvideo:[a-z0-9-]+:[0-9]+:[a-z]+/[a-zA-Z0-9_.-]+/[0-9]+",
        "minLength": 1,
        "maxLength": 1024,
    }
    with pytest.raises(Unsatisfiable):
        path_ctx.generate_from_schema(schema)


@pytest.mark.parametrize("location", [ParameterLocation.HEADER, ParameterLocation.COOKIE])
def test_header_pattern_requiring_non_alnum_skips_positive_string(ctx_factory, location):
    # Header/cookie values are alphanumeric-only; an ARN's literal `:` can't satisfy that, so nothing is emitted.
    ctx = ctx_factory(location=location, generation_modes=[GenerationMode.POSITIVE])
    schema = {
        "type": "string",
        "pattern": "arn:[a-z0-9-\\.]{1,63}:[a-z0-9-\\.]{0,63}:[a-z0-9-\\.]{0,63}:[a-z0-9-\\.]{0,63}",
        "minLength": 1,
        "maxLength": 1024,
    }
    assert list(_positive_string(ctx, schema)) == []


def test_header_alnum_pattern_still_generates(ctx_factory):
    # A purely alphanumeric pattern is compatible with the header restriction — values must still be produced.
    ctx = ctx_factory(location=ParameterLocation.HEADER, generation_modes=[GenerationMode.POSITIVE])
    schema = {"type": "string", "pattern": "[A-Za-z0-9]+", "minLength": 1, "maxLength": 16}
    covered = list(_positive_string(ctx, schema))
    assert covered
    compiled = re.compile(schema["pattern"])
    for value in covered:
        assert isinstance(value.value, str)
        assert compiled.fullmatch(value.value)


def test_query_pattern_requiring_non_alnum_not_skipped(ctx_factory):
    # Query parameters carry no alphanumeric-only restriction, so the ARN pattern is satisfiable there.
    ctx = ctx_factory(location=ParameterLocation.QUERY, generation_modes=[GenerationMode.POSITIVE])
    schema = {
        "type": "string",
        "pattern": "arn:[a-z0-9-\\.]{1,63}:[a-z0-9-\\.]{0,63}:[a-z0-9-\\.]{0,63}:[a-z0-9-\\.]{0,63}",
        "minLength": 1,
        "maxLength": 1024,
    }
    covered = list(_positive_string(ctx, schema))
    assert covered


def test_items_false_with_prefix_items(ctx_factory):
    # `prefixItems` arrived with 2020-12, which is the dialect the crash this guards was reported under.
    ctx = ctx_factory(generation_modes=[GenerationMode.POSITIVE], validator_cls=jsonschema_rs.Draft202012Validator)
    schema = {
        "type": "array",
        "items": False,
        "prefixItems": [{"type": "string"}, {"type": "string"}],
    }
    assert_covers(ctx, schema, [[]])


def test_negative_prefix_items(nctx):
    schema = {
        "type": "array",
        "items": [{"type": "integer"}, {"type": "boolean"}],
    }
    covered = cover_schema(nctx, schema)
    assert_unique(covered)
    assert_not_conform(covered, schema)
    # Should have negative cases for each position
    arrays = [v for v in covered if isinstance(v, list)]
    assert len(arrays) > 0
    # Each array should have exactly 2 items (matching prefixItems length)
    for arr in arrays:
        assert len(arr) == 2


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf"])
def test_anyof_oneof_with_items_as_list(nctx, keyword):
    schema = {
        "type": "object",
        "properties": {
            "data": {
                keyword: [
                    {"type": "array", "items": [{"type": "string"}]},
                    {"type": "null"},
                ]
            }
        },
    }
    covered = cover_schema(nctx, schema)
    assert_unique(covered)
    assert_not_conform(covered, schema)


def test_negative_binary_string_type_violation(ctx_factory):
    # Binary format strings should still generate non-string type violations
    ctx = ctx_factory(location=ParameterLocation.BODY, generation_modes=[GenerationMode.NEGATIVE])
    schema = {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "value": {"type": "string", "format": "binary"},
        },
        "required": ["key", "value"],
    }
    covered = cover_schema(ctx, schema)
    assert_unique(covered)
    # Check that we generate non-string values for the binary property
    non_string_values = [
        v for v in covered if isinstance(v, dict) and "value" in v and not isinstance(v["value"], (str | bytes))
    ]
    assert len(non_string_values) > 0, "Should generate non-string type violations for binary format"
    assert_not_conform(covered, schema)


def test_negative_oneof_with_binary_format_items(ctx_factory):
    ctx = ctx_factory(location=ParameterLocation.BODY, generation_modes=[GenerationMode.NEGATIVE])
    schema = {
        "oneOf": [
            {
                "type": "array",
                "items": {"type": "string", "format": "binary"},
                "maxItems": 10,
            },
            {"type": "string"},
        ]
    }
    assert_unique(cover_schema(ctx, schema))


def test_anyof_with_required_constraints(pctx):
    # See GH-3520
    schema = {
        "type": "object",
        "anyOf": [
            {"required": ["name"]},
            {"required": ["id"]},
        ],
        "properties": {
            "type": {"type": "string"},
            "id": {"type": "string"},
            "name": {"type": "string"},
        },
    }
    covered = cover_schema(pctx, schema)
    assert covered == [
        {"type": "", "id": "", "name": ""},
        {"id": "", "name": ""},
        {"type": "", "name": ""},
        {"name": ""},
        {"type": "", "id": "", "name": ""},
        {"id": "", "name": ""},
        {"type": "", "id": ""},
        {"id": ""},
    ]
    assert_conform(covered, schema)


def test_merge_with_parent_context_bool_subschema(pctx):
    schema = {
        "type": "object",
        "anyOf": [
            True,
            {"required": ["name"]},
        ],
        "properties": {
            "name": {"type": "string"},
        },
    }
    covered = cover_schema(pctx, schema)
    object_values = [v for v in covered if isinstance(v, dict)]
    assert len(object_values) > 0
    assert_conform(object_values, schema)


def test_merge_with_parent_context_merges_required_lists(pctx):
    # Parent has `required` AND sub has `required` - the two lists get merged
    schema = {
        "type": "object",
        "required": ["type"],
        "anyOf": [
            {"required": ["name"]},
        ],
        "properties": {
            "type": {"type": "string"},
            "name": {"type": "string"},
        },
    }
    covered = cover_schema(pctx, schema)
    assert_conform(covered, schema)
    # The merged sub inherits "type" from parent and adds "name" from sub
    assert all("type" in v and "name" in v for v in covered if isinstance(v, dict))


def test_inline_sub_with_own_properties_is_folded_with_parent(pctx):
    # A branch with its own `properties` still answers to the parent's; both sets appear together.
    schema = {
        "type": "object",
        "properties": {
            "parent_field": {"type": "string"},
        },
        "anyOf": [
            {
                "properties": {"id": {"type": "integer"}},
            },
        ],
    }
    covered = cover_schema(pctx, schema)
    assert_conform(covered, schema)
    assert covered == [{"parent_field": "", "id": 0}, {"id": 0}, {"parent_field": ""}, {}]


def test_with_effective_required_break_when_no_extra_fields(pctx):
    # break fires when the first dict sub-schema with `required` contributes no extra fields
    # because all its required fields are already in the parent's required list
    schema = {
        "type": "object",
        "required": ["name"],
        "anyOf": [
            {"required": ["name"]},  # "name" already required by parent -> extra=[] -> break
            {"required": ["id"]},  # never reached due to break above
        ],
        "properties": {
            "name": {"type": "string"},
            "id": {"type": "string"},
        },
    }
    covered = cover_schema(pctx, schema)
    assert_conform(covered, schema)
    # All positive values must include "name" (always required by parent)
    assert all("name" in v for v in covered if isinstance(v, dict))


def test_no_property_nesting_with_ref_oneof(ctx_factory):
    # See GH-3584
    # Generated values for a schema with oneOf $ref sub-schemas
    # must not produce nested objects like {config: {config: {...}}}.
    schema = {
        "type": "object",
        "properties": {
            "config": {
                "oneOf": [
                    {"$ref": "#/x-bundled/schema1"},
                    {"$ref": "#/x-bundled/schema2"},
                ],
            },
        },
        "required": ["config"],
        "x-bundled": {
            "schema1": {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
            "schema2": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    }
    ctx = ctx_factory(root_schema=schema, location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE])
    covered = cover_schema(ctx, schema)
    assert_conform(covered, schema)
    # Each oneOf branch generates its own type; no config key inside config values
    assert covered == [
        {"config": {"value": 0}},
        {"config": {"name": ""}},
    ]


def test_ref_with_sibling_keywords_does_not_inherit_parent_properties(ctx_factory):
    schema = {
        "type": "object",
        "properties": {
            "config": {
                "oneOf": [
                    {
                        "$ref": "#/x-bundled/schema1",
                        "description": "line config variant",
                    }
                ],
            },
            "extra": {"type": "string"},
        },
        "required": ["config"],
        "x-bundled": {
            "schema1": {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
        },
    }
    ctx = ctx_factory(root_schema=schema, location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE])
    covered = cover_schema(ctx, schema)
    assert_conform(covered, schema)
    # Sibling keywords on $ref (description) don't affect resolution — schema1 generated directly
    # Parent properties (extra) appear at the outer object level, not injected inside config
    assert covered == [
        {"config": {"value": 0}, "extra": ""},
        {"config": {"value": 0}},
    ]


def test_ref_to_additive_schema_inherits_parent_properties(ctx_factory):
    # A $ref sub-schema that resolves to a schema with NO properties of its own
    # (additive constraint only) SHOULD still inherit parent properties so the
    # generator knows the field definitions for required fields.
    # anyOf has two branches so parent-generated {"name": ""} satisfies the first branch.
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "id": {"type": "integer"},
        },
        "required": ["name"],
        "anyOf": [
            {"required": ["name"]},
            {"$ref": "#/x-bundled/extra_required"},
        ],
        "x-bundled": {
            "extra_required": {"required": ["id"]},
        },
    }
    ctx = ctx_factory(root_schema=schema, location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE])
    covered = cover_schema(ctx, schema)
    assert_conform(covered, schema)
    # Additive $ref (no properties) merges parent context — both name and id appear
    assert covered == [
        {"name": "", "id": 0},
        {"name": ""},
        {"name": "", "id": 0},
    ]


def test_negative_unique_items_on_scalar_param_emits_both_polarities(nctx):
    # Scalar params with `uniqueItems` need both duplicate and unique pairs to cover both polarities.
    schema = {"type": "integer", "uniqueItems": True}
    covered = [v for v in cover_schema(nctx, schema) if isinstance(v, list)]
    assert any(len(array) == 2 and array[0] == array[1] for array in covered), covered
    assert any(len(array) == 2 and array[0] != array[1] for array in covered), covered


def test_array_with_unique_items_enum_not_violated(pctx):
    schema = {
        "type": "array",
        "items": {"enum": ["A", "B", "C"]},
        "uniqueItems": True,
        "minItems": 3,
        "maxItems": 3,
    }
    covered = cover_schema(pctx, schema)
    # All generated arrays must be valid (no duplicate elements)
    assert_conform(covered, schema)
    # Each enum variant must appear as the first element in at least one array,
    # so every variant gets coverage
    first_elements = {arr[0] for arr in covered if arr}
    assert first_elements == {"A", "B", "C"}


def test_oneof_branch_honors_sibling_items(ctx_factory):
    pctx = ctx_factory(location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE])
    schema = {
        "type": "object",
        "required": ["entrypoint"],
        "properties": {
            "entrypoint": {
                "items": {"type": "string"},
                "oneOf": [{"type": "array", "items": {}}, {"type": "string"}],
            }
        },
    }
    covered = cover_schema(pctx, schema)
    assert_conform(covered, schema)
    assert covered == [{"entrypoint": ""}, {"entrypoint": [""]}, {"entrypoint": []}]


def test_anyof_branch_honors_sibling_additional_properties(ctx_factory):
    pctx = ctx_factory(location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE])
    schema = {
        "type": "object",
        "required": ["data"],
        "properties": {
            "data": {
                "additionalProperties": False,
                "anyOf": [
                    {"properties": {"last_name": {"type": "string"}}, "required": ["last_name"]},
                    {"properties": {"nickname": {"type": "string"}}, "required": ["nickname"]},
                ],
            }
        },
    }
    covered = cover_schema(pctx, schema)
    assert_conform(covered, schema)
    # Every object shape a branch proposes carries a key the parent forbids, leaving only `null`.
    assert covered == [{"data": None}]


IF_THEN_ELSE_SCHEMA = {
    "type": "object",
    "properties": {"kind": {"type": "string"}, "value": {}},
    "required": ["kind"],
    "if": {"properties": {"kind": {"const": "number"}}},
    "then": {"properties": {"value": {"type": "integer"}}, "required": ["value"]},
    "else": {"properties": {"value": {"type": "string"}}, "required": ["value"]},
}


def test_positive_if_then_else_emits_only_conforming_cases(ctx_factory):
    # Body context applies the parent-validator gate; query context skips it.
    pctx = ctx_factory(location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE])
    rewritten = transform(IF_THEN_ELSE_SCHEMA, to_json_schema, nullable_keyword="x-nullable")
    validator = jsonschema_rs.Draft202012Validator(IF_THEN_ELSE_SCHEMA)
    cases = cover_schema(pctx, rewritten)
    invalid = [c for c in cases if not validator.is_valid(c)]
    assert not invalid, f"positive cases violate if/then/else: {invalid}"
    assert any(isinstance(c, dict) and c.get("kind") == "number" for c in cases), "then-branch case missing"
    assert any(isinstance(c, dict) and c.get("kind") != "number" for c in cases), "else-branch case missing"


def test_negative_if_then_else_violates_branches(ctx_factory):
    nctx = ctx_factory(location=ParameterLocation.BODY, generation_modes=[GenerationMode.NEGATIVE])
    rewritten = transform(IF_THEN_ELSE_SCHEMA, to_json_schema, nullable_keyword="x-nullable")
    validator = jsonschema_rs.Draft202012Validator(IF_THEN_ELSE_SCHEMA)
    cases = cover_schema(nctx, rewritten)
    invalid = [c for c in cases if isinstance(c, dict) and not validator.is_valid(c)]
    assert invalid, "no negative cases violate the conditional"


def test_negative_allof_with_unmergeable_branches_terminates(nctx):
    # `contains` with conflicting item types prevents canonicalish from merging the `allOf`.
    schema = {
        "allOf": [
            {"type": "object", "properties": {"arr": {"type": "array", "contains": {"type": "string"}}}},
            {"type": "object", "properties": {"arr": {"type": "array", "contains": {"type": "integer"}}}},
        ],
    }
    list(cover_schema_iter(nctx, schema))


def test_minitems_one_yields_empty_array_negative_with_unresolvable_items(nctx):
    schema = {"type": "array", "minItems": 1, "items": {"$ref": "#/components/schemas/Missing"}}
    assert scenario_values(nctx, schema, CoverageScenario.ARRAY_BELOW_MIN_ITEMS) == [[]]


@pytest.mark.parametrize(
    ("schema", "expects_baseline"),
    [
        (
            {
                "type": "array",
                "items": {"type": "object"},
                "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                "required": ["key", "value"],
            },
            True,
        ),
        (
            {
                "type": ["array", "null"],
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
            True,
        ),
        (
            {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
            False,
        ),
        (
            {
                "type": ["object", "string"],
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
            False,
        ),
    ],
    ids=[
        "outer-array-excludes-object",
        "type-list-excludes-object",
        "type-object-includes-object",
        "type-list-includes-object",
    ],
)
def test_negative_properties_baseline_emission(ctx_factory, schema, expects_baseline):
    # Outer `type` excludes object -> emit a bare template alongside per-leaf negatives;
    # `type` includes object -> positive path already emits it, no double-counting.
    nctx = ctx_factory(location=ParameterLocation.BODY, generation_modes=[GenerationMode.NEGATIVE])
    inner_validator = jsonschema_rs.Draft7Validator(
        {
            "type": "object",
            "properties": schema["properties"],
            "required": schema["required"],
        }
    )
    cases = cover_schema(nctx, schema)
    baselines = [c for c in cases if isinstance(c, dict) and inner_validator.is_valid(c)]
    if expects_baseline:
        assert baselines, f"no baseline emitted: {cases}"
        leaf_negatives = [c for c in cases if isinstance(c, dict) and not inner_validator.is_valid(c) and c != {}]
        assert leaf_negatives, f"per-leaf negatives lost: {cases}"
    else:
        assert baselines == [], f"unexpected baseline: {baselines}"


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array", "patternProperties": {"^x_": {"type": "string"}}},
        {"type": "array", "propertyNames": {"pattern": "^x_"}},
    ],
    ids=["patternProperties", "propertyNames"],
)
def test_negative_object_keyword_baseline_emission(ctx_factory, schema):
    # `_negative_type` already emits `{}` for `type: array`; the new baseline path emits a
    # second `{}`. Counting both guards against a regression in the baseline branch.
    nctx = ctx_factory(
        location=ParameterLocation.BODY,
        generation_modes=[GenerationMode.NEGATIVE],
        validator_cls=jsonschema_rs.Draft202012Validator,
    )
    cases = cover_schema(nctx, schema)
    assert cases.count({}) >= 2, f"baseline `{{}}` emission missing: {cases}"


def test_get_properties_resolves_ref_to_implied_object(pctx):
    # Without ref resolution, the nested value would be generated as any JSON value -- often `null`.
    schema = {
        "$defs": {
            "Inner": {
                "properties": {
                    "key": {"type": "string", "example": "myKey"},
                    "value": {"type": "string", "example": "myValue"},
                }
            }
        },
        "type": "object",
        "required": ["nested"],
        "properties": {"nested": {"$ref": "#/$defs/Inner"}},
    }
    pctx.root_schema = schema
    cases = cover_schema(pctx, schema)
    populated = [c for c in cases if isinstance(c, dict) and isinstance(c.get("nested"), dict) and c["nested"]]
    assert populated, f"nested ref-to-object never materialized: {cases}"


def test_get_properties_preserves_required_outside_properties(pctx):
    # Required keys not declared in `properties` must still reach the generated template.
    schema = {
        "$defs": {
            "Inner": {
                "required": ["name"],
                "properties": {"value": {"type": "string"}},
            }
        },
        "type": "object",
        "required": ["nested"],
        "properties": {"nested": {"$ref": "#/$defs/Inner"}},
    }
    pctx.root_schema = schema
    cases = cover_schema(pctx, schema)
    nested_objects = [c["nested"] for c in cases if isinstance(c, dict) and isinstance(c.get("nested"), dict)]
    assert nested_objects, f"no nested object emitted: {cases}"
    assert all("name" in n for n in nested_objects), f"required key dropped: {nested_objects}"


def test_positive_number_boundary_respects_sibling_not(pctx):
    schema = {"type": "integer", "minimum": 1, "maximum": 65535, "not": {"minimum": 65534, "maximum": 65534}}
    covered = cover_schema(pctx, schema)
    assert_conform(covered, schema)
    assert 65535 in covered


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "number", "minimum": 0, "maximum": 10, "not": {"minimum": 10, "maximum": 10}},
        {"type": "boolean", "not": {"const": False}},
        {"type": ["null", "integer"], "not": {"type": "null"}},
        {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 3, "not": {"maxItems": 1}},
        {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            "required": ["a"],
            "not": {"required": ["a", "b"]},
        },
        {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            "not": {"maxProperties": 0},
        },
    ],
    ids=["number", "boolean", "multi-type", "array", "object-required", "object-min-properties"],
)
def test_positive_values_respect_sibling_not(pctx, schema):
    assert_conform(cover_schema(pctx, schema), schema)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "pattern": "[0-9]+", "anyOf": [{"minLength": 2}]},
        {"type": "integer", "minimum": 0, "maximum": 10, "anyOf": [{"multipleOf": 7, "minimum": 7}]},
        {"type": "array", "items": {"type": "integer"}, "anyOf": [{"minItems": 3}]},
        {"type": "boolean", "anyOf": [{"const": True}]},
        {"type": "string", "pattern": "[0-9]+", "oneOf": [{"minLength": 2}]},
        {"type": "array", "items": {"type": "integer"}, "oneOf": [{"minItems": 2}, {"maxItems": 0}]},
    ],
    ids=["string", "number", "array", "boolean", "one-of-string", "one-of-array"],
)
def test_positive_values_respect_sibling_combinators(pctx, schema):
    assert_conform(cover_schema(pctx, schema), schema)


def test_negative_pattern_with_min_length_above_max_length_skips_pattern_violation(nctx):
    schema = {"type": "string", "minLength": 1, "maxLength": 0, "pattern": "^[a-z]+$"}
    assert_not_conform(cover_schema(nctx, schema), schema)


# Past 2**53 a unit step vanishes in float arithmetic, leaving the "violating" value equal to the bound.
@pytest.mark.parametrize(
    "schema",
    [
        {"minimum": -5.151020255852562e16},
        {"type": "number", "maximum": 1e17},
        {"type": "integer", "maximum": 9996036847180748.0},
    ],
    ids=["untyped-minimum", "number-maximum", "integer-float-spelled-maximum"],
)
def test_negative_numeric_boundary_steps_past_large_float_bounds(nctx, schema):
    assert_not_conform(cover_schema(nctx, schema), schema)


def test_positive_integer_past_exclusive_float_bound_steps_in_integer_arithmetic(ctx_factory):
    schema = {"type": "integer", "exclusiveMinimum": 9996036847180748.0}
    ctx = ctx_factory(validator_cls=jsonschema_rs.Draft202012Validator, generation_modes=[GenerationMode.POSITIVE])
    assert_conform(cover_schema(ctx, schema), schema)


# Bundled names restart per operation, so the same `$ref` names different targets in different operations.
def test_positive_values_follow_each_schema_own_reference_target(ctx_factory):
    for target in ({"type": "integer"}, {"type": "null"}):
        schema = {
            "type": "object",
            "properties": {"a": {"type": "array", "items": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/schema1"}, "minItems": 1}},
            BUNDLE_STORAGE_KEY: {"schema1": target},
        }
        ctx = ctx_factory(
            root_schema=schema, location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE]
        )
        assert_conform(cover_schema(ctx, schema), schema)


# Integer multiples of `p/q` are exactly the multiples of `p`; a float step must not leak into the values.
@pytest.mark.parametrize(
    "schema",
    [{"type": "integer", "multipleOf": 0.5}, {"type": "integer", "multipleOf": 0.3, "minimum": 1, "maximum": 7}],
    ids=["unbounded", "bounded"],
)
def test_positive_integer_with_fractional_multiple_of_stays_on_the_integer_grid(pctx, schema):
    values = cover_schema(pctx, schema)
    assert values and all(isinstance(value, int) for value in values), values
    assert_conform(values, schema)


# A required name outside `properties` answers to a matching `patternProperties` entry, else to `additionalProperties`.
@pytest.mark.parametrize(
    "schema",
    [
        {
            "type": "object",
            "properties": {"a": {"type": "null"}},
            "required": ["a", "b"],
            "additionalProperties": {"type": "boolean"},
        },
        {
            "type": "object",
            "properties": {"x": {"type": "null"}},
            "required": ["ab"],
            "patternProperties": {"^a": {"type": "boolean"}},
            "additionalProperties": {"type": "string"},
        },
        {
            "allOf": [
                {"type": "object", "properties": {"b": {"type": "null"}}, "additionalProperties": {"type": "boolean"}},
                {"type": "object", "properties": {"b": {"type": "null"}}, "required": ["a"]},
            ]
        },
    ],
    ids=["additional-properties", "pattern-properties", "all-of"],
)
def test_positive_required_name_outside_properties_takes_its_governing_schema(pctx, schema):
    values = cover_schema(pctx, schema)
    assert values
    assert_conform(values, schema)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {"a": {"type": "null"}}, "maxProperties": 0},
        {"type": "object", "properties": {"b": {"type": "null"}}, "required": ["a"], "maxProperties": 1},
    ],
    ids=["no-room-at-all", "required-fills-the-window"],
)
def test_positive_object_keeps_property_sweep_within_max_properties(pctx, schema):
    values = cover_schema(pctx, schema)
    assert values
    assert_conform(values, schema)


# Both spellings of a bound apply; the resolved window is the tighter one, here empty.
def test_positive_number_with_both_bound_spellings_keeps_the_tighter_one(ctx_factory):
    schema = {
        "type": "integer",
        "minimum": 8,
        "maximum": -7,
        "exclusiveMinimum": -8,
        "exclusiveMaximum": 5,
        "multipleOf": 2,
    }
    ctx = ctx_factory(validator_cls=jsonschema_rs.Draft202012Validator, generation_modes=[GenerationMode.POSITIVE])
    assert_conform(cover_schema(ctx, schema), schema)


# The template may inflate `required`, never relax it: an unsatisfiable optional object must stay absent.
def test_positive_object_omits_optional_property_whose_schema_requires_an_undeclared_name(pctx):
    schema = {
        "type": "object",
        "properties": {
            "a": {
                "type": "object",
                "properties": {"b": {"type": "null"}},
                "additionalProperties": False,
                "required": ["a"],
            }
        },
    }
    assert cover_schema(pctx, schema) == [{}]


# A float32-parsing server narrows the value; the boundary negative must still fall outside after narrowing.
@pytest.mark.parametrize(
    ("keyword", "bound"),
    [("maximum", 1e10), ("minimum", -1e10), ("maximum", 10000000000)],
    ids=["maximum", "minimum", "integer-spelled-maximum"],
)
def test_negative_float_boundary_survives_float32_narrowing(nctx, keyword, bound):
    schema = {"type": "number", "format": "float", keyword: bound}
    scenario = CoverageScenario.VALUE_ABOVE_MAXIMUM if keyword == "maximum" else CoverageScenario.VALUE_BELOW_MINIMUM
    values = scenario_values(nctx, schema, scenario)
    assert values
    for value in values:
        narrowed = to_float32(float(value))
        assert narrowed > to_float32(bound) if keyword == "maximum" else narrowed < to_float32(bound), value


# `minProperties` only constrains objects; a negative built without pinning the type may be a valid non-object.
@pytest.mark.parametrize("schema", [{"minProperties": 2}, {"minProperties": 2, "properties": {"a": {"type": "null"}}}])
def test_negative_min_properties_without_type_stays_an_object(nctx, schema):
    values = scenario_values(nctx, schema, CoverageScenario.OBJECT_BELOW_MIN_PROPERTIES)
    assert values
    assert_not_conform(values, schema)


# Past the generation buffer there is no object worth building, let alone one key per unit of `maxProperties`.
def test_negative_max_properties_past_the_buffer_is_skipped(nctx):
    schema = {"type": "object", "maxProperties": 40_000}
    scenarios = {generated.scenario for generated in cover_schema_iter(nctx, schema)}
    assert CoverageScenario.OBJECT_ABOVE_MAX_PROPERTIES not in scenarios


# Draft 4 reads `1.0` as a number, not an integer, so an integer's boundary value must be spelled as one.
@pytest.mark.parametrize(
    "schema",
    [{"type": "integer", "minimum": -5.151020255852562e16}, {"type": "integer", "maximum": 7.0}],
    ids=["float-spelled-minimum", "float-spelled-maximum"],
)
def test_positive_integer_boundaries_are_integers(pctx, schema):
    values = cover_schema(pctx, schema)
    assert values and all(isinstance(value, int) for value in values), values


# A floor past the buffer rules its own type out, combinator or not; the other types stay (long strings are padded).
@pytest.mark.parametrize(
    ("schema", "expected_types"),
    [
        ({"minItems": 32769, "anyOf": [{"type": "string"}, {"type": "array"}]}, {str}),
        ({"minProperties": 32769, "anyOf": [{"type": "integer"}, {"type": "object"}]}, {int}),
        ({"minLength": 40000, "anyOf": [{"type": "integer"}, {"type": "string"}]}, {int, str}),
        ({"type": "array", "minItems": 32769, "anyOf": [{}]}, set()),
        (
            {
                "type": "object",
                "properties": {
                    "o": {
                        "type": "object",
                        "anyOf": [{}],
                        "properties": {"p": {"type": "array", "minItems": 32769}},
                        "required": ["p"],
                    }
                },
                "required": ["o"],
            },
            set(),
        ),
    ],
    ids=["items", "properties", "length", "typed-array", "built-whole"],
)
def test_positive_floor_past_the_buffer_beside_a_combinator(pctx, schema, expected_types):
    values = cover_schema(pctx, schema)
    assert {type(value) for value in values} == expected_types, values
    assert_conform(values, schema)


# A floor no ceiling admits rules the container out; filling to the floor anyway would breach the ceiling.
@pytest.mark.parametrize(
    "schema",
    [
        {"maxItems": 0, "minItems": 65},
        {"type": "array", "maxItems": 3, "minItems": 65},
        {"maxProperties": 0, "minProperties": 65},
        {"type": "object", "maxProperties": 3, "minProperties": 65},
        {"minProperties": 65, "maximum": 0},
    ],
    ids=["untyped-items", "typed-items", "untyped-properties", "typed-properties", "untyped-properties-only"],
)
def test_positive_large_floor_respects_the_ceiling(pctx, schema):
    assert_conform(cover_schema(pctx, schema), schema)


# The filler starts from an object even where the schema admits other types too.
def test_positive_large_floor_under_a_type_list_still_fills_an_object(pctx):
    schema = {
        "type": "object",
        "properties": {"p": {"type": ["null", "object"], "minProperties": 65}},
        "required": ["p"],
    }
    values = cover_schema(pctx, schema)
    assert any(isinstance(value["p"], dict) and len(value["p"]) >= 65 for value in values), values
    assert_conform(values, schema)


# One drawn element repeated fills a large floor whether or not `items` is declared; past the buffer nothing is built.
@pytest.mark.parametrize(
    ("schema", "lengths"),
    [
        ({"type": "array", "minItems": 9000}, {9000, 9001}),
        ({"minItems": 9000}, {9000, 9001}),
        ({"minItems": 33915}, set()),
        ({"type": "array", "items": {"type": "null"}, "minItems": 100000}, set()),
    ],
    ids=["undeclared-items", "untyped", "untyped-past-the-buffer", "declared-items-past-the-buffer"],
)
def test_positive_array_floor_is_tiled_within_the_buffer(pctx, schema, lengths):
    arrays = [value for value in cover_schema(pctx, schema) if isinstance(value, list)]
    assert {len(value) for value in arrays} == lengths
    assert_conform(arrays, schema)


# A value the operation's draft rejects must not slip through because a newer draft cannot read the schema at all.
def test_positive_enum_values_are_judged_by_the_operation_draft(pctx):
    schema = {"enum": [[]], "minItems": 1, "minimum": 0, "exclusiveMinimum": True}
    assert cover_schema(pctx, schema) == []


# Past the generation buffer there is no object worth building, in either direction.
@pytest.mark.parametrize("mode", list(GenerationMode))
def test_min_properties_past_the_buffer_is_skipped(ctx_factory, mode):
    schema = {"type": "object", "minProperties": 40_000}
    scenarios = {generated.scenario for generated in cover_schema_iter(ctx_factory(generation_modes=[mode]), schema)}
    assert not scenarios & {CoverageScenario.VALID_OBJECT, CoverageScenario.OBJECT_BELOW_MIN_PROPERTIES}


# One drawn value under synthesized names fills a large floor; declared names keep their own schemas.
@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "minProperties": 1000},
        {"type": "object", "minProperties": 1000, "additionalProperties": {"type": "integer"}},
        {
            "type": "object",
            "minProperties": 1000,
            "properties": {"x": {"type": "integer"}, "y": {"type": "string"}},
            "required": ["y"],
        },
    ],
    ids=["free", "typed-additional", "declared-names"],
)
def test_positive_object_floor_is_filled_within_the_buffer(pctx, schema):
    objects = [value for value in cover_schema(pctx, schema) if isinstance(value, dict)]
    assert objects and all(len(value) >= 1000 for value in objects), [len(value) for value in objects]
    assert_conform(objects, schema)


# A positive flipped out of `not` still answers to the outer keywords, spelled in the operation's draft.
def test_positive_flipped_from_not_is_judged_by_the_operation_draft(pctx):
    schema = {"type": "string", "maxLength": 2, "minimum": 0, "exclusiveMinimum": True, "not": {"enum": ["ab"]}}
    validator = jsonschema_rs.Draft4Validator(schema)
    for value in cover_schema(pctx, schema):
        validator.validate(value)


# Multiples past the decimal context's precision are still exact, and only a multiple a float spells is emitted.
@pytest.mark.parametrize(
    "schema",
    [
        {"type": "number", "maximum": 1e30, "multipleOf": 3.0},
        {"type": "number", "minimum": 1e30, "multipleOf": 3.0},
        {"type": "number", "exclusiveMinimum": 1e30, "multipleOf": 0.5},
        {"type": "number", "exclusiveMaximum": -1e30, "multipleOf": 0.3},
    ],
    ids=["maximum", "minimum", "exclusive-minimum-half-step", "exclusive-maximum-decimal-step"],
)
def test_positive_number_multiples_past_decimal_precision(ctx_factory, schema):
    ctx = ctx_factory(validator_cls=jsonschema_rs.Draft202012Validator, generation_modes=[GenerationMode.POSITIVE])
    validator = jsonschema_rs.Draft202012Validator(schema)
    values = cover_schema(ctx, schema)
    assert values
    for value in values:
        assert validator.is_valid(value), value


# The validator reads a float bound as the decimal its JSON text spells, so integer steps must be taken in decimal.
@pytest.mark.parametrize(
    ("schema", "mode"),
    [
        ({"type": "integer", "maximum": -5.151020255852562e16}, GenerationMode.POSITIVE),
        ({"type": "integer", "minimum": 5.151020255852562e16}, GenerationMode.POSITIVE),
        ({"type": "integer", "exclusiveMaximum": -5.151020255852562e16}, GenerationMode.POSITIVE),
        ({"type": "integer", "minimum": -5.151020255852562e16}, GenerationMode.NEGATIVE),
        (
            {"type": "integer", "minimum": -5.151020255852562e16, "exclusiveMinimum": -5.151020255852562e16},
            GenerationMode.POSITIVE,
        ),
        (
            {"type": "integer", "maximum": 5.151020255852562e16, "exclusiveMaximum": 5.151020255852562e16},
            GenerationMode.POSITIVE,
        ),
    ],
    ids=["maximum", "minimum", "exclusive-maximum", "negative-minimum", "both-minimums", "both-maximums"],
)
def test_integer_steps_past_float_bounds_follow_the_decimal_text(ctx_factory, schema, mode):
    ctx = ctx_factory(validator_cls=jsonschema_rs.Draft202012Validator, generation_modes=[mode])
    validator = jsonschema_rs.Draft202012Validator(schema)
    values = cover_schema(ctx, schema)
    assert values
    for value in values:
        assert validator.is_valid(value) == (mode == GenerationMode.POSITIVE), value


# A multiple sits inside a pinned float bound only when both are read as the decimal text spells them.
@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            {"type": "number", "minimum": -5.151020255852562e16, "maximum": -5.151020255852562e16, "multipleOf": 2},
            [-51510202558525620],
        ),
        (
            {"type": "number", "minimum": 5.151020255852562e16, "maximum": 5.151020255852562e16, "multipleOf": 2},
            [51510202558525620],
        ),
        (
            {"type": "number", "minimum": -5.151020255852562e16, "maximum": -5.151020255852562e16, "multipleOf": 100},
            [],
        ),
    ],
    ids=["negative", "positive", "no-multiple-fits"],
)
def test_positive_number_multiple_within_pinned_float_bounds(ctx_factory, schema, expected):
    ctx = ctx_factory(validator_cls=jsonschema_rs.Draft202012Validator, generation_modes=[GenerationMode.POSITIVE])
    assert cover_schema(ctx, schema) == expected


# A property's bound is read the same way whether the template or the boundary sweep builds the value.
def test_positive_object_template_steps_float_bounds_in_decimal(pctx):
    schema = {"type": "object", "properties": {"a": {"type": "integer", "maximum": -5.151020255852562e16}}}
    validator = jsonschema_rs.Draft4Validator(schema)
    for value in cover_schema(pctx, schema):
        validator.validate(value)


# A branch value still answers to the parent's keywords in the operation's draft, where `0.0` is no integer.
def test_positive_branch_values_respect_parent_type_in_the_operation_draft(ctx_factory):
    schema = {"type": "integer", "anyOf": [{"anyOf": [{"type": "number", "example": None, "default": None}]}]}
    validator = jsonschema_rs.Draft4Validator(schema)
    for value in cover_schema(
        ctx_factory(location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE]), schema
    ):
        validator.validate(value)


# A non-integer float only violates `integer` when `number` is not also allowed.
def test_negative_type_for_number_and_integer_union_emits_no_number(nctx):
    schema = {"type": ["number", "integer"], "minimum": 0.0, "maximum": 1.0}
    values = scenario_values(nctx, schema, CoverageScenario.INCORRECT_TYPE)
    assert values
    assert not any(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values), values


def test_negative_pattern_with_min_length_past_the_buffer_is_skipped(nctx):
    schema = {"type": "string", "minLength": 57341, "pattern": "^a"}
    assert_not_conform(cover_schema(nctx, schema), schema)


# Past 2**53 the next multiple may have no float spelling; as an integer it is exact.
def test_positive_number_multiples_past_float_precision_are_exact(pctx):
    schema = {"type": "number", "multipleOf": 3, "minimum": 9996036847180748.0}
    assert_conform(cover_schema(pctx, schema), schema)


def test_negative_boundary_past_the_largest_float_is_skipped(nctx):
    schema = {"maximum": 1.7976931348623157e308}
    assert inf not in cover_schema(nctx, schema)


def test_positive_string_with_min_length_above_max_length_emits_nothing(pctx):
    assert cover_schema(pctx, {"type": "string", "minLength": 1, "maxLength": 0}) == []


def test_positive_object_drops_optional_string_with_an_empty_length_window(pctx):
    schema = {"type": "object", "properties": {"a": {"type": "string", "minLength": 1, "maxLength": 0}}}
    assert cover_schema(pctx, schema) == [{}]


@pytest.mark.parametrize("location", [ParameterLocation.QUERY, ParameterLocation.PATH])
def test_negative_boolean_not_coercible_wire_value(ctx_factory, location):
    # Lenient parsers coerce 0/1/true/false to booleans, so those wire values are not type violations for a boolean parameter
    nctx = ctx_factory(location=location, generation_modes=[GenerationMode.NEGATIVE])
    schema = {"type": "boolean", "default": False}
    values = scenario_values(nctx, schema, CoverageScenario.INCORRECT_TYPE)

    coercible = {"0", "1", "true", "false"}
    rendered = {str(value).lower() for value in values}
    assert not (rendered & coercible), f"Boolean-coercible negatives generated: {values}"


@pytest.mark.parametrize(
    ("validator_cls", "should_generate"),
    [
        (jsonschema_rs.Draft4Validator, False),
        (jsonschema_rs.Draft202012Validator, True),
    ],
)
def test_hostname_negative_format_respects_validator_draft(ctx_factory, validator_cls, should_generate):
    # `XN--9krT00a` is valid in Draft 4 but invalid in Draft 2020-12; `const` pins it as the only draw.
    schema = {"type": "string", "format": "hostname", "const": "XN--9krT00a"}
    ctx = ctx_factory(root_schema=schema, generation_modes=[GenerationMode.NEGATIVE], validator_cls=validator_cls)

    generator = _negative_format(ctx, schema, "hostname")

    if should_generate:
        assert next(generator).value == "XN--9krT00a"
    else:
        with pytest.raises(Unsatisfiable):
            next(generator)


def test_negative_format_serves_cached_value(nctx):
    # Random strings almost never look like IPv4, so the violation filter accepts and the
    # strategy returns immediately. The second call must yield the same value, served from cache.
    schema = {"type": "string", "format": "ipv4"}
    assert next(_negative_format(nctx, schema, "ipv4")).value == next(_negative_format(nctx, schema, "ipv4")).value


def test_negative_format_serves_cached_unsatisfiable(nctx):
    # Lowercase-letter strings are valid single-label hostnames, so the violation filter
    # rejects every draw. The second call must raise from the cached sentinel.
    schema = {"type": "string", "format": "hostname", "pattern": "^[a-z]+$"}
    with pytest.raises(Unsatisfiable):
        next(_negative_format(nctx, schema, "hostname"))
    with pytest.raises(Unsatisfiable):
        next(_negative_format(nctx, schema, "hostname"))


@pytest.mark.parametrize(
    ("types", "expected_kind"),
    [(["string", "number", "null"], (int, float)), (["string", "integer", "null"], int)],
    ids=["number", "integer"],
)
def test_multi_type_union_yields_numeric_branch(ctx_factory, types, expected_kind):
    # Numeric branch of a multi-type union must produce a numeric value, not a string drawn from a sibling branch.
    ctx = ctx_factory(
        generation_modes=[GenerationMode.POSITIVE],
        is_required=False,
        validator_cls=jsonschema_rs.Draft202012Validator,
    )
    values = cover_schema(ctx, {"type": types})
    assert any(isinstance(v, expected_kind) and not isinstance(v, bool) for v in values), values


def test_anyof_null_branch_filtered_when_allof_sibling_requires_object(ctx_factory):
    # A nullable-derived anyOf branch must not yield null when an allOf sibling pins `type: object`,
    # even when the merged schema holds nested bundled refs the parent validator must resolve.
    root_schema = {
        "x-bundled": {
            "request": {
                "additionalProperties": True,
                "allOf": [{"$ref": "#/x-bundled/base"}, {"type": "object"}],
                "required": ["start_date"],
            },
            "base": {
                "anyOf": [
                    {
                        "additionalProperties": True,
                        "properties": {"start_date": {"$ref": "#/x-bundled/start"}},
                    },
                    {"type": "null"},
                ]
            },
            "start": {"type": "string"},
        }
    }
    ctx = ctx_factory(
        root_schema=root_schema, location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE]
    )
    values = cover_schema(ctx, {"$ref": "#/x-bundled/request"})
    assert None not in values, values


def test_not_schema_flipped_values_respect_outer_type_with_bundled_refs(ctx_factory):
    # Flipped `not`-violations must satisfy the outer `type`, also when nested bundled refs
    # make the outer schema unverifiable without the bundle.
    root_schema = {"x-bundled": {"name": {"type": "string"}}}
    ctx = ctx_factory(
        root_schema=root_schema, location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE]
    )
    schema = {
        "type": "object",
        "properties": {"name": {"$ref": "#/x-bundled/name"}},
        "not": {"type": "string"},
    }
    positives = [v.value for v in cover_schema_iter(ctx, schema) if v.generation_mode == GenerationMode.POSITIVE]
    non_objects = [v for v in positives if not isinstance(v, dict)]
    assert not non_objects, non_objects


def test_cover_schema_iter_does_not_mutate_root_schema(ctx_factory):
    # A self-recursive `allOf` $ref used to grow the shared root document until cloning hit its recursion limit.
    root_schema = {
        "components": {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "child": {
                            "allOf": [
                                {"$ref": "#/components/schemas/Node"},
                                {"description": "child node"},
                            ]
                        }
                    },
                }
            }
        }
    }
    snapshot = deepclone(root_schema)
    ctx = ctx_factory(
        root_schema=root_schema,
        generation_modes=[GenerationMode.POSITIVE],
        validator_cls=jsonschema_rs.Draft202012Validator,
    )
    for _ in cover_schema_iter(ctx, {"$ref": "#/components/schemas/Node"}):
        pass
    assert root_schema == snapshot


@pytest.mark.parametrize(
    ("keyword", "bound"),
    [
        ("exclusiveMinimum", 0),
        ("exclusiveMinimum", 1.0),
        ("exclusiveMaximum", 1.0),
        ("exclusiveMinimum", 0.1),
        ("exclusiveMaximum", 16777217),
    ],
    ids=["min-zero", "min-representable", "max-representable", "min-rounds-up", "max-rounds-down"],
)
def test_float_format_boundary_strictly_satisfies_bound(pctx, keyword, bound):
    # The emitted boundary value must still satisfy the exclusive bound after a server narrows it to float32.
    schema = {"type": "number", "format": "float", keyword: bound}
    values = cover_schema(pctx, schema)
    assert values, schema
    for value in values:
        narrowed = to_float32(float(value))
        if keyword == "exclusiveMinimum":
            assert narrowed > bound, (value, narrowed)
        else:
            assert narrowed < bound, (value, narrowed)


@pytest.mark.parametrize("bound", [1e39, 10**1000], ids=["float", "integer"])
def test_float_format_bound_outside_single_precision_range_does_not_crash(pctx, bound):
    schema = {"type": "number", "format": "float", "exclusiveMaximum": bound}
    values = cover_schema(pctx, schema)
    for value in values:
        assert to_float32(float(value)) < 1e39, value


def test_float_format_unsatisfiable_bound_emits_nothing(pctx):
    # No finite float32 exceeds 10**1000, so there is no representable positive value to emit.
    schema = {"type": "number", "format": "float", "exclusiveMinimum": 10**1000}
    assert cover_schema(pctx, schema) == []


@pytest.mark.parametrize("key", ["example", "examples", "default"])
def test_float_format_collapsing_example_not_emitted(pctx, key):
    # A user value valid as float64 but collapsing to 0 in float32 must not be emitted as positive.
    value = [5e-324] if key == "examples" else 5e-324
    schema = {"type": "number", "format": "float", "exclusiveMinimum": 0, key: value}
    values = cover_schema(pctx, schema)
    assert values and all(to_float32(float(v)) > 0 for v in values), values


def test_float_format_representable_example_still_emitted(pctx):
    schema = {"type": "number", "format": "float", "exclusiveMinimum": 0, "example": 1000}
    assert 1000 in cover_schema(pctx, schema)


@pytest.mark.parametrize(
    "schema",
    [
        {"allOf": [{"type": "string", "pattern": "^a"}, {"type": "string", "pattern": "b$"}]},
        {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
            "allOf": [{"properties": {"a": {"pattern": "^x"}}}, {"properties": {"a": {"pattern": "y$"}}}],
        },
    ],
    ids=["two-patterns", "outer-properties"],
)
def test_satisfiable_allof_without_a_flat_form_still_emits_positive_values(pctx, schema):
    # Two `pattern`s have no single spelling, which used to drop the whole schema from coverage.
    values = cover_schema(pctx, schema)
    assert values, schema
    validator = make_validator_for(schema)
    for value in values:
        assert validator.is_valid(value), value


@pytest.mark.parametrize(
    "conditional",
    [
        {"if": {"minLength": 3}, "then": {"pattern": "\\p{L}"}},
        {"if": {"minLength": 3}, "then": {"pattern": "\\p{L}"}, "else": {"maxLength": 9}},
    ],
    ids=["then", "then-else"],
)
def test_negative_format_around_a_conditional_the_engine_cannot_follow(ctx_factory, conditional):
    # `\p{L}` has no Python spelling, so the guarded branch cannot drive a draw; dropping the guard
    # leaves a wider schema that can, with the validator ruling out what it over-admits.
    schema = {"type": "string", "format": "date", "minLength": 1, **conditional}
    ctx = ctx_factory(generation_modes=[GenerationMode.NEGATIVE], validator_cls=jsonschema_rs.Draft202012Validator)
    scenarios = {value.scenario for value in cover_schema_iter(ctx, schema)}
    assert CoverageScenario.INVALID_FORMAT in scenarios, scenarios


@pytest.mark.parametrize("hint", ["example", "default"], ids=["example", "default"])
def test_property_hint_pinned_under_draft4(ctx_factory, hint):
    # Draft 4 has no `const`, so pinning a hint with one leaves the property free to draw anything.
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"default": {"type": "boolean", hint: True}, "other": {"type": "string"}},
    }
    ctx = ctx_factory(location=ParameterLocation.BODY, validator_cls=jsonschema_rs.Draft4Validator)
    validator = jsonschema_rs.Draft4Validator(schema)
    for value in cover_schema_iter(ctx, schema):
        if value.generation_mode == GenerationMode.POSITIVE:
            assert validator.is_valid(value.value), value.value


def test_all_of_keeps_the_tightest_upper_bound(pctx):
    schema = {"allOf": [{"type": "string", "maxLength": 5}, {"maxLength": 3}]}
    values = cover_schema(pctx, schema)
    assert values and all(len(value) <= 3 for value in values), values


@pytest.mark.parametrize(
    "schema",
    [
        {"allOf": [{"allOf": [{"type": "string", "pattern": "^a"}, {"pattern": "b$"}]}]},
        {"allOf": [{"allOf": [{"type": "string", "pattern": "^a"}, {"pattern": "b$"}]}, {"type": "string"}]},
    ],
    ids=["sole-branch", "beside-a-sibling"],
)
def test_nested_all_of_without_a_flat_form_emits_a_conforming_value(pctx, schema):
    values = cover_schema(pctx, schema)
    assert values and all(value.startswith("a") and value.endswith("b") for value in values), values


def test_all_of_with_a_boolean_branch_emits_a_conforming_value(pctx):
    values = cover_schema(pctx, {"allOf": [{"type": "string"}, True]})
    assert values and all(isinstance(value, str) for value in values), values


# `integer` and `number` overlap, so a branch pair naming them still admits every integer.
@pytest.mark.parametrize(
    "branches",
    [
        [{"type": "integer"}, {"type": "number"}],
        [{"type": "number"}, {"type": "integer"}],
        [{"type": "integer"}, {"type": ["number", "string"]}],
    ],
    ids=["integer-number", "number-integer", "integer-number-union"],
)
def test_all_of_narrows_number_to_integer(pctx, nctx, branches):
    schema = {"allOf": branches}
    positive = cover_schema(pctx, schema)
    assert positive
    assert_conform(positive, schema)
    negative = cover_schema(nctx, schema)
    assert negative
    assert_not_conform(negative, schema)


# Keywords beside a property's `$ref` constrain its value in the object template too.
@pytest.mark.parametrize(
    "property_schema",
    [
        {"$ref": f"#/{BUNDLE_STORAGE_KEY}/A", "anyOf": [{"type": "null"}]},
        {"allOf": [{"$ref": f"#/{BUNDLE_STORAGE_KEY}/A", "anyOf": [{"type": "null"}]}]},
    ],
    ids=["ref-sibling", "ref-sibling-in-all-of"],
)
def test_positive_object_template_keeps_property_ref_sibling_keywords(ctx_factory, property_schema):
    schema = {
        "type": "object",
        "properties": {"a": property_schema},
        BUNDLE_STORAGE_KEY: {"A": {"type": "boolean"}},
    }
    ctx = ctx_factory(
        root_schema=schema,
        location=ParameterLocation.BODY,
        generation_modes=[GenerationMode.POSITIVE],
        validator_cls=jsonschema_rs.Draft202012Validator,
    )
    validator = jsonschema_rs.Draft202012Validator(schema)
    for value in cover_schema(ctx, schema):
        assert validator.is_valid(value), value


# Under Draft 2020-12 keywords beside a branch `$ref` constrain it; dropping them emits values off the set.
def test_positive_all_of_ref_branch_keeps_sibling_keywords(ctx_factory):
    schema = {
        "allOf": [{"$ref": f"#/{BUNDLE_STORAGE_KEY}/A", "anyOf": [{"type": "null"}]}],
        BUNDLE_STORAGE_KEY: {"A": {"type": "boolean"}},
    }
    ctx = ctx_factory(
        root_schema=schema,
        location=ParameterLocation.BODY,
        generation_modes=[GenerationMode.POSITIVE],
        validator_cls=jsonschema_rs.Draft202012Validator,
    )
    validator = jsonschema_rs.Draft202012Validator(schema)
    for value in cover_schema(ctx, schema):
        assert validator.is_valid(value), value


# Draft 4 ignores keywords beside `$ref`, so that branch admits `null` too and `oneOf` rejects it.
def test_positive_one_of_ref_branch_sibling_keywords_judged_by_the_operation_draft(ctx_factory):
    schema = {
        "oneOf": [{"type": "null"}, {"$ref": f"#/{BUNDLE_STORAGE_KEY}/A", "anyOf": [{"type": "boolean"}]}],
        BUNDLE_STORAGE_KEY: {"A": {"type": "null"}},
    }
    ctx = ctx_factory(root_schema=schema, location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE])
    validator = jsonschema_rs.Draft4Validator(schema)
    for value in cover_schema(ctx, schema):
        assert validator.is_valid(value), value


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "object", "properties": {"a": {"type": "string"}}, "allOf": {"a": 1}}, []),
        ({"allOf": {"type": "string"}}, []),
        ({"allOf": "nope"}, []),
        ({"allOf": [{"enum": [1, 2]}, {"enum": 5}]}, [1, 2]),
    ],
    ids=["all-of-not-a-list", "all-of-a-single-key-dict", "all-of-a-string", "enum-not-a-list"],
)
def test_malformed_all_of_covers_only_what_parses(pctx, schema, expected):
    # Real documents carry these shapes; the walk keeps going instead of taking the run down.
    assert cover_schema(pctx, schema) == expected


@pytest.mark.parametrize(
    "extra",
    [{}, {"not": {"maxLength": 2}}],
    ids=["plain", "with-not"],
)
def test_negative_format_without_a_buildable_base(ctx_factory, extra):
    # Stripping `format` leaves a pattern with no Python spelling, so no violation can be drawn from
    # it - and widening past the `not` does not bring one back.
    schema = {"type": "string", "format": "date", "pattern": "\\p{Tibetan}", **extra}
    ctx = ctx_factory(generation_modes=[GenerationMode.NEGATIVE], validator_cls=jsonschema_rs.Draft202012Validator)
    scenarios = {value.scenario for value in cover_schema_iter(ctx, schema)}
    assert CoverageScenario.INVALID_FORMAT not in scenarios, scenarios


@pytest.mark.parametrize(
    "items_hint",
    [{"example": {"id": "X"}}, {"examples": [{"id": "X"}]}, {"default": {"id": "X"}}],
    ids=["example", "examples", "default"],
)
def test_array_items_spec_hint_seeds_generated_array(pctx, items_hint):
    # Array elements draw from `items`-level spec hints.
    items = {"type": "object", "properties": {"id": {"type": "string"}}, **items_hint}
    assert pctx.generate_from_schema({"type": "array", "items": items, "minItems": 1}) == [{"id": "X"}]


@pytest.mark.parametrize(
    "hint_extra",
    [
        {"example": {"id": "X", "ro": "v"}},
        {"examples": [{"id": "X", "ro": "v"}]},
        {"default": {"id": "X", "ro": "v"}},
    ],
    ids=["example", "examples", "default"],
)
def test_spec_hint_recovers_after_dropping_readonly_stripped_keys(pctx, hint_extra):
    # Examples carrying `readOnly` keys (forbidden in request schemas) must still seed generation after dropping them.
    schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "ro": {"not": {}}},
        **hint_extra,
    }
    assert pctx.generate_from_schema(schema) == {"id": "X"}


def test_closing_generator_after_module_globals_are_cleared(pctx, monkeypatch):
    # Interpreter finalization nulls module globals before suspended generators are closed.
    generator = cover_schema_iter(pctx, {"type": "string"})
    next(generator)
    monkeypatch.setattr(_schema, "jsonschema_rs", None)

    generator.close()


def test_closing_reference_generator_after_module_globals_are_cleared(ctx_factory, monkeypatch):
    # Interpreter finalization nulls module globals before suspended generators are closed.
    ctx = ctx_factory(
        generation_modes=[GenerationMode.POSITIVE],
        root_schema={"definitions": {"Item": {"type": "string"}}},
    )
    generator = cover_schema_iter(ctx, {"$ref": "#/definitions/Item"})
    next(generator)
    monkeypatch.setattr(_schema, "RefResolutionError", None)

    generator.close()


def test_boundary_length_string_at_the_drawable_limit(pctx):
    # An off-by-one in the guard silently drops the maximum-length case for the whole band.
    schema = {"type": "string", "pattern": "^[a-z]+$", "maxLength": MAX_GENERATED_PATTERN_LENGTH}

    assert MAX_GENERATED_PATTERN_LENGTH in {len(value.value) for value in cover_schema_iter(pctx, schema)}


def test_boundary_length_string_beyond_the_drawable_limit_kept_when_pattern_allows_any_character(pctx):
    # A permissive pattern keeps its maximum-length case even past the length matching can draw.
    length = MAX_GENERATED_PATTERN_LENGTH * 2
    schema = {"type": "string", "pattern": ".*", "maxLength": length}

    assert length in {len(value.value) for value in cover_schema_iter(pctx, schema)}


def test_maximum_items_array_of_costly_elements(pctx):
    # Drawing every element as a pattern match outruns the budget and comes back empty.
    size = MAX_DRAWN_ARRAY_ITEMS * 4
    schema = {"type": "array", "items": {"type": "string", "pattern": UUID_PATTERN}, "maxItems": size}

    assert size in {len(value.value) for value in cover_schema_iter(pctx, schema)}


def test_maximum_items_array_of_costly_elements_stays_valid(pctx):
    size = MAX_DRAWN_ARRAY_ITEMS * 4
    schema = {"type": "array", "items": {"type": "string", "pattern": UUID_PATTERN}, "maxItems": size}
    validator = make_validator_for(schema)

    for value in cover_schema_iter(pctx, schema):
        assert validator.is_valid(value.value), value.value[:3]


def test_maximum_items_array_at_the_drawn_limit(pctx):
    # An off-by-one in the threshold drops the maximum-items case for arrays right at it.
    schema = {"type": "array", "items": {"type": "integer"}, "maxItems": MAX_DRAWN_ARRAY_ITEMS}

    assert MAX_DRAWN_ARRAY_ITEMS in {len(value.value) for value in cover_schema_iter(pctx, schema)}


def test_repeated_object_elements_are_independent(pctx):
    # Each index has to hold its own object, or editing one request field edits every other.
    size = MAX_DRAWN_ARRAY_ITEMS * 4
    schema = {
        "type": "array",
        "items": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        "maxItems": size,
    }

    for value in cover_schema_iter(pctx, schema):
        if len(value.value) < 2:
            continue
        value.value[0]["name"] = "edited"
        assert value.value[1]["name"] != "edited", value.value[:2]


def test_contains_array_is_not_filled_by_repetition(pctx):
    # Repeating one element cannot make an array hold both a match and a non-match.
    size = MAX_DRAWN_ARRAY_ITEMS * 4
    schema = {
        "type": "array",
        "items": {"type": "integer"},
        "contains": {"type": "integer", "minimum": 10},
        "maxItems": size,
    }
    validator = make_validator_for(schema)

    for value in cover_schema_iter(pctx, schema):
        assert validator.is_valid(value.value), value.value[:3]


def test_unique_items_array_is_not_filled_by_repetition(pctx):
    # Repeating one element would duplicate it, which `uniqueItems` forbids.
    size = MAX_DRAWN_ARRAY_ITEMS * 4
    schema = {"type": "array", "items": {"type": "integer"}, "maxItems": size, "uniqueItems": True}

    for value in cover_schema_iter(pctx, schema):
        assert len(set(value.value)) == len(value.value), value.value[:3]


# Draft 4 reads `1.0` as a number; a spec-provided value still answers to the operation's draft.
@pytest.mark.parametrize(
    "schema",
    [
        {"type": "integer", "example": 1.0},
        {"type": "integer", "default": 2.0},
        {"type": "integer", "enum": [1.0, 2]},
        {"type": "object", "properties": {"a": {"type": "integer", "example": 1.0}}},
        {"type": "array", "items": {"type": "integer"}, "example": [1.0]},
    ],
    ids=["example", "default", "enum", "nested-example", "array-example"],
)
def test_positive_spec_values_are_judged_by_the_operation_draft(pctx, schema):
    validator = jsonschema_rs.Draft4Validator(schema)
    values = cover_schema(pctx, schema)
    assert values
    for value in values:
        validator.validate(value)


@pytest.mark.parametrize("name", ["plain", "then", "else", "not", "if", "const"])
def test_positive_object_keeps_properties_named_like_keywords(ctx_factory, name):
    inner = {"type": "object", "properties": {name: {"type": "string"}, "other": {"type": "string"}}}
    schema = {"type": "object", "properties": {"nested": inner}}
    context = ctx_factory(location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE])
    assert next(cover_schema_iter(context, schema)).value == {"nested": {name: "", "other": ""}}


# A branch is covered folded with the keywords and combinators beside it, so the node yields what its fold yields.
@pytest.mark.parametrize(
    ("schema", "folded"),
    [
        ({"oneOf": [{"type": "null"}, {"type": "boolean"}], "allOf": [{"type": "null"}]}, {"type": "null"}),
        ({"oneOf": [{"type": "boolean"}], "anyOf": [{"type": "null"}]}, {"not": {}}),
        (
            {"type": "integer", "minimum": 5, "anyOf": [{"type": "string"}, {"multipleOf": 3}]},
            {"type": "integer", "minimum": 5, "multipleOf": 3},
        ),
        ({"type": "string", "pattern": "^[a-z]+$", "anyOf": [{"type": "null"}]}, {"not": {}}),
    ],
    ids=["oneOf-beside-allOf", "oneOf-beside-anyOf", "type-beside-anyOf", "pattern-beside-anyOf"],
)
def test_positive_branch_values_meet_their_siblings(pctx, schema, folded):
    assert cover_schema(pctx, schema) == cover_schema(pctx, folded)


# Draft 4 reads 0.0 as a non-integer, so a `not integer` branch matches it too; exclusivity holds in every draft.
def test_positive_one_of_exclusivity_judged_by_the_operation_draft(pctx):
    schema = {"oneOf": [{"not": {"type": "integer"}}, {"type": "number", "example": None, "default": None}]}
    validator = jsonschema_rs.Draft4Validator(schema)
    values = cover_schema(pctx, schema)
    assert values
    for value in values:
        assert validator.is_valid(value), value


REF_WITH_SIBLINGS = {
    "$ref": "#/x-bundled/base",
    "properties": {"extra": {"type": "integer"}},
    "required": ["extra"],
}
REF_TARGET_BUNDLE = {"base": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({**REF_WITH_SIBLINGS, "x-bundled": REF_TARGET_BUNDLE}, [{"name": "", "extra": 0}]),
        (
            {"oneOf": [REF_WITH_SIBLINGS, {"type": "string"}], "x-bundled": REF_TARGET_BUNDLE},
            [{"name": "", "extra": 0}, ""],
        ),
    ],
    ids=["bare", "inside-branch"],
)
def test_ref_sibling_properties_and_required_merge_into_the_target(ctx_factory, schema, expected):
    ctx = ctx_factory(root_schema=schema, location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE])
    assert cover_schema(ctx, schema) == expected


def test_no_unexpected_property_when_every_candidate_name_matches_pattern_properties(nctx):
    # `patternProperties` validates the added key instead of `additionalProperties`, so it stays valid.
    schema = {"type": "object", "patternProperties": {"property": {"type": "string"}}, "additionalProperties": False}
    assert scenario_values(nctx, schema, CoverageScenario.OBJECT_UNEXPECTED_PROPERTIES) == []


def test_additional_property_key_skips_a_declared_name(ctx_factory):
    schema = {
        "type": "object",
        "properties": {"x-schemathesis-additional": {"type": "string"}},
        "additionalProperties": {"type": "integer"},
    }
    ctx = ctx_factory(location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE])
    added = [
        set(value) - {"x-schemathesis-additional"}
        for value in scenario_values(ctx, schema, CoverageScenario.OBJECT_ADDITIONAL_PROPERTY)
    ]
    assert added == [{"x-schemathesis-additional1"}]


# JSON tells `false` and `0` apart where Python does not, while `1` and `1.0` are the same number.
@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"const": False, "anyOf": [{"const": 0}]}, []),
        ({"enum": [0, 1, "x"], "anyOf": [{"enum": [False, True, "x"]}]}, ["x"]),
        ({"const": 1, "anyOf": [{"const": 1.0}]}, [1]),
    ],
    ids=["bool-vs-number", "enum-overlap", "int-vs-float"],
)
def test_positive_allowed_values_intersect_as_json(pctx, schema, expected):
    assert cover_schema(pctx, schema) == expected


# A `multipleOf` violation is a number; a `pattern` beside an open type must not turn it into a string.
def test_negative_multiple_of_stays_numeric_beside_pattern(ctx_factory):
    nctx = ctx_factory(
        location=ParameterLocation.BODY,
        generation_modes=[GenerationMode.NEGATIVE],
        validator_cls=jsonschema_rs.Draft202012Validator,
    )
    schema = {"type": ["number", "string"], "multipleOf": 1.0, "pattern": ""}
    validator = jsonschema_rs.Draft202012Validator(schema)
    values = scenario_values(nctx, schema, CoverageScenario.NOT_MULTIPLE_OF)
    assert values
    for value in values:
        assert not validator.is_valid(value), value


# A `patternProperties` entry matching a declared name constrains that property's values too.
@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {"a": {"type": "null"}}, "patternProperties": {"a": False}},
        {"type": "object", "properties": {"a": {"type": "string"}}, "patternProperties": {"^a$": {"minLength": 5}}},
    ],
    ids=["forbidding", "constraining"],
)
def test_positive_declared_property_meets_matching_pattern_properties(ctx_factory, schema):
    ctx = ctx_factory(
        root_schema=schema,
        location=ParameterLocation.BODY,
        generation_modes=[GenerationMode.POSITIVE],
        validator_cls=jsonschema_rs.Draft202012Validator,
    )
    validator = jsonschema_rs.Draft202012Validator(schema)
    for value in cover_schema(ctx, schema):
        assert validator.is_valid(value), value


# Under Draft 2020-12 the first positions belong to `prefixItems`; `items` values must not land there.
@pytest.mark.parametrize(
    "schema",
    [
        {"items": {}, "prefixItems": [False]},
        {"type": "array", "items": {"type": "integer"}, "prefixItems": [{"type": "string"}]},
    ],
    ids=["forbidden-first", "typed-first"],
)
def test_positive_array_items_covering_respects_prefix_items(ctx_factory, schema):
    ctx = ctx_factory(
        root_schema=schema,
        location=ParameterLocation.BODY,
        generation_modes=[GenerationMode.POSITIVE],
        validator_cls=jsonschema_rs.Draft202012Validator,
    )
    validator = jsonschema_rs.Draft202012Validator(schema)
    for value in cover_schema(ctx, schema):
        assert validator.is_valid(value), value


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array", "items": {"type": "null"}, "prefixItems": [{"type": "boolean"}]},
        {"type": "array", "items": {"type": "integer"}, "prefixItems": [{"type": "string"}], "minItems": 3},
    ],
    ids=["single-prefix", "padded"],
)
def test_negative_array_items_covering_respects_prefix_items(ctx_factory, schema):
    ctx = ctx_factory(
        root_schema=schema,
        location=ParameterLocation.BODY,
        generation_modes=[GenerationMode.NEGATIVE],
        validator_cls=jsonschema_rs.Draft202012Validator,
    )
    validator = jsonschema_rs.Draft202012Validator(schema)
    values = cover_schema(ctx, schema)
    assert any(isinstance(value, list) and value for value in values), values
    for value in values:
        assert not validator.is_valid(value), value


def test_negative_prefix_items_covered_for_raw_keyword(ctx_factory):
    schema = {"type": "array", "prefixItems": [{"type": "integer"}], "minItems": 1}
    ctx = ctx_factory(
        root_schema=schema,
        location=ParameterLocation.BODY,
        generation_modes=[GenerationMode.NEGATIVE],
        validator_cls=jsonschema_rs.Draft202012Validator,
    )
    validator = jsonschema_rs.Draft202012Validator(schema)
    values = cover_schema(ctx, schema)
    assert any(isinstance(value, list) and value for value in values), values
    for value in values:
        assert not validator.is_valid(value), value


# A branch's `$ref` siblings narrow what it admits; ignoring them lets a value matching exactly
# one branch ship as a `oneOf` violation.
def test_negative_one_of_judges_branches_with_ref_sibling_keywords(ctx_factory):
    schema = {
        "oneOf": [
            {"type": "null"},
            {"$ref": f"#/{BUNDLE_STORAGE_KEY}/A", "anyOf": [{"type": "null"}]},
            {"type": "array", "items": {"type": "null"}},
        ],
        BUNDLE_STORAGE_KEY: {"A": {}},
    }
    ctx = ctx_factory(
        root_schema=schema,
        location=ParameterLocation.BODY,
        generation_modes=[GenerationMode.NEGATIVE],
        validator_cls=jsonschema_rs.Draft202012Validator,
    )
    validator = jsonschema_rs.Draft202012Validator(schema)
    for value in cover_schema(ctx, schema):
        assert not validator.is_valid(value), value


# Merged property halves conflict and one half requires a name, so the property itself admits no object.
def test_positive_all_of_merged_property_keeps_nested_required(ctx_factory):
    schema = {
        "allOf": [
            {
                "type": "object",
                "properties": {"c": {"type": "object", "properties": {"a": {"type": "null"}}, "required": ["a"]}},
            },
            {"type": "object", "properties": {"c": {"type": "object", "properties": {"a": {"type": "boolean"}}}}},
        ]
    }
    ctx = ctx_factory(root_schema=schema, location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE])
    validator = jsonschema_rs.Draft4Validator(schema)
    for value in cover_schema(ctx, schema):
        assert validator.is_valid(value), value


# Registry churn past the pin bound frees old ids; values keyed with their tokens must not survive.
def test_generation_session_bounds_pins_and_drops_dependent_values():
    session = GenerationSession()
    registries = [object() for _ in range(MAX_PINNED_REGISTRIES + 1)]
    first_token = session.token_for(registries[0])
    session.values[("k", first_token)] = "cached"
    for registry in registries[1:]:
        session.token_for(registry)
    assert len(session._pinned) == MAX_PINNED_REGISTRIES
    assert session.values.get(("k", first_token)) is MISSING


# An array item must keep the names its schema requires, declared under `properties` or not.
def test_positive_array_items_covering_keeps_item_required(ctx_factory):
    schema = {
        "type": "array",
        "items": {"type": "object", "properties": {"a": {"type": "null"}}, "required": ["b"]},
        "minItems": 1,
    }
    ctx = ctx_factory(root_schema=schema, location=ParameterLocation.BODY, generation_modes=[GenerationMode.POSITIVE])
    validator = jsonschema_rs.Draft4Validator(schema)
    for value in cover_schema(ctx, schema):
        assert validator.is_valid(value), value


def test_negative_one_of_ref_with_siblings_under_draft4(ctx_factory):
    # Draft 4 ignores keywords beside `$ref`, so the second branch admits any boolean and a
    # boolean is not a valid negative for the whole schema.
    schema = {
        "oneOf": [{"type": "null"}, {"$ref": "#/x-bundled/A", "anyOf": [{"type": "null"}]}],
        "x-bundled": {"A": {"type": "boolean"}},
    }
    ctx = ctx_factory(
        location=ParameterLocation.BODY,
        generation_modes=[GenerationMode.NEGATIVE],
        validator_cls=jsonschema_rs.Draft4Validator,
        root_schema=schema,
    )
    validator = jsonschema_rs.Draft4Validator(schema)
    for value in cover_schema_iter(ctx, schema):
        assert not validator.is_valid(value.value), f"False negative-mode value: {value.value!r}"
