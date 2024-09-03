import json
import re
from unittest.mock import ANY

import jsonschema
import pytest

from schemathesis.generation import DataGenerationMethod
from schemathesis.generation.coverage import (
    CoverageContext,
    GeneratedValue,
    _positive_number,
    _positive_string,
    cover_schema_iter,
)

PATTERN = "^\\d+$"


def cover_schema(ctx: CoverageContext, schema: dict) -> list:
    return [value.value for value in cover_schema_iter(ctx, schema)]


def assert_unique(values: list):
    seen = set()
    for value in values:
        if isinstance(value, GeneratedValue):
            value = value.value
        if isinstance(value, (dict, list)):
            serialized = json.dumps(value, sort_keys=True)
            key = (type(value), serialized)
        else:
            key = value
        assert key not in seen
        seen.add(key)


def assert_conform(values: list, schema: dict):
    for value in values:
        if isinstance(value, GeneratedValue):
            value = value.value
        jsonschema.validate(
            value,
            schema,
            cls=jsonschema.Draft7Validator,
            format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
        )


def assert_not_conform(values: list, schema: dict):
    for entry in values:
        if schema.get("format") == "unknown":
            # Can't validate the format
            continue
        try:
            jsonschema.validate(
                entry,
                schema,
                cls=jsonschema.Draft7Validator,
                format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
            )
            raise ValueError(f"Value {entry} conforms to {schema}")
        except (jsonschema.ValidationError, ValueError):
            pass


def is_string(value):
    return isinstance(value, str)


def is_not_string(value):
    return not isinstance(value, str)


def is_type(value, ty):
    if ty == "integer":
        return not isinstance(value, bool) and isinstance(value, int)
    elif ty == "number":
        return not isinstance(value, bool) and isinstance(value, (int, float))
    elif ty == "boolean":
        return isinstance(value, bool)
    elif ty == "string":
        return isinstance(value, str)
    elif ty == "null":
        return value is None
    elif ty == "array":
        return isinstance(value, list)
    elif ty == "object":
        return isinstance(value, dict)
    raise ValueError(f"Unknown type: {ty}")


def is_not_type(value, ty):
    return not is_type(value, ty)


def matches_pattern(value, pattern):
    return re.match(pattern, value) is not None


def does_not_match_pattern(value, pattern):
    return not matches_pattern(value, pattern)


@pytest.fixture
def ctx():
    return CoverageContext()


@pytest.fixture
def pctx():
    return CoverageContext(data_generation_methods=[DataGenerationMethod.positive])


@pytest.fixture
def nctx():
    return CoverageContext(data_generation_methods=[DataGenerationMethod.negative])


@pytest.mark.parametrize(
    "schema, expected",
    (
        ({"type": "null"}, [None]),
        ({"type": "boolean"}, [True, False]),
        ({"type": ["boolean", "null"]}, [True, False, None]),
        ({"enum": [1, 2]}, [1, 2]),
        ({"const": 42}, [42]),
    ),
)
def test_positive_primitive_schemas(pctx, schema, expected):
    covered = cover_schema(pctx, schema)
    assert covered == expected
    assert_unique(covered)
    assert_conform(covered, schema)


class AnyString:
    def __eq__(self, value: object, /) -> bool:
        return isinstance(value, str)


class AnyNumber:
    def __eq__(self, value: object, /) -> bool:
        return not isinstance(value, bool) and isinstance(value, (int, float))


class NotNumber:
    def __eq__(self, value: object, /) -> bool:
        return not (not isinstance(value, bool) and isinstance(value, (int, float)))


@pytest.mark.parametrize(
    "schema, expected",
    (
        ({"type": "null"}, 0),
        ({"type": "boolean"}, 0),
        ({"type": ["boolean", "null"]}, 0),
        ({"enum": [1, 2]}, None),
        ({"enum": [1, 2, {}]}, None),
        ({"const": 42}, None),
        ({"multipleOf": 2}, lambda x: x % 2 != 0),
        ({"format": "date-time"}, AnyString()),
        ({"format": "hostname"}, AnyString()),
        ({"format": "unknown"}, AnyString()),
        ({"uniqueItems": True}, [None, None]),
        ({"maximum": 5}, 6),
        ({"minimum": 5}, 4),
        ({"exclusiveMinimum": 5}, 5),
        ({"exclusiveMaximum": 5}, 5),
        ({"required": ["a"]}, {}),
    ),
)
def test_negative_primitive_schemas(nctx, schema, expected):
    covered = cover_schema(nctx, schema)
    if callable(expected):
        assert len(covered) == 1
        assert expected(covered[0])
    else:
        assert covered == [expected]
    assert_unique(covered)
    assert_not_conform(covered, schema)


@pytest.mark.parametrize(
    "schema, lengths",
    (
        ({"type": "string"}, {0}),
        ({"type": "string", "example": "test"}, {4}),
        ({"type": "string", "examples": ["A", "BB"]}, {1, 2}),
        ({"type": "string", "minLength": 5}, {5, 6}),
        ({"type": "string", "maxLength": 10}, {9, 10}),
        ({"type": "string", "minLength": 5, "maxLength": 10}, {5, 6, 9, 10}),
        ({"type": "string", "minLength": 5, "maxLength": 6}, {5, 6}),
        ({"type": "string", "minLength": 5, "maxLength": 5}, {5}),
    ),
)
def test_positive_string(ctx, schema, lengths):
    covered = list(_positive_string(ctx, schema))
    assert_unique(covered)
    for length in lengths:
        assert len([x for x in covered if len(x.value) == length]) == 1
    assert_conform(covered, schema)


@pytest.mark.parametrize(
    "schema, expected",
    (
        ({"type": "string"}, [0]),
        ({"type": "string", "minLength": 5}, [0, "0000"]),
        ({"type": "string", "maxLength": 10}, [0, "00000000000"]),
        ({"type": "string", "minLength": 5, "maxLength": 10}, [0, "0000", "00000000000"]),
        ({"type": "string", "pattern": "^[0-9]", "minLength": 1}, [0, ""]),
        ({"type": "string", "pattern": "^[0-9]"}, [0, ""]),
        ({"type": "string", "format": "date-time"}, [0, ""]),
    ),
)
def test_negative_string(nctx, schema, expected):
    covered = cover_schema(nctx, schema)
    assert covered == expected
    assert_unique(covered)
    assert_not_conform(covered, schema)


@pytest.mark.parametrize("multiple_of", (None, 2))
@pytest.mark.parametrize(
    "schema, values, with_multiple_of",
    (
        ({"type": "integer"}, [0], [0]),
        ({"type": "integer", "example": 2}, [2], [2]),
        ({"type": "integer", "examples": [42, 44]}, [42, 44], [42, 44]),
        ({"type": "number"}, [0], [0]),
        ({"type": "integer", "minimum": 5}, [5, 6], [6, 8]),
        ({"type": "number", "minimum": 5.5}, [5.5, 6.5], [6, 8]),
        ({"type": "integer", "maximum": 10}, [10, 9], [10, 8]),
        ({"type": "number", "maximum": 11.5}, [11.5, 10.5], [10, 8]),
        ({"type": "integer", "minimum": 5, "maximum": 10}, [5, 6, 10, 9], [6, 8, 10]),
        ({"type": "integer", "minimum": 5, "maximum": 6}, [5, 6], [6]),
        ({"type": "integer", "minimum": 5, "maximum": 5}, [5], None),
        ({"type": "integer", "exclusiveMinimum": 5}, [6, 7], [6, 8]),
        ({"type": "integer", "exclusiveMaximum": 10}, [9, 8], [8, 6]),
        ({"type": "integer", "exclusiveMinimum": 5, "exclusiveMaximum": 10}, [6, 7, 9, 8], [6, 8]),
    ),
)
def test_positive_number(ctx, schema, multiple_of, values, with_multiple_of):
    if with_multiple_of is None and multiple_of is not None:
        pytest.skip("This test is not applicable for multiple_of=None")
    if multiple_of is not None:
        schema["multipleOf"] = multiple_of
        values = with_multiple_of
    covered = [value.value for value in _positive_number(ctx, schema)]
    assert_unique(covered)
    assert covered == values
    assert_conform(covered, schema)


@pytest.mark.parametrize(
    "schema, expected",
    (
        ({"type": "object"}, [{}]),
        ({"type": "object", "example": {"A": 42}}, [{"A": 42}]),
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
                                            "pattern": "^[-._\\p{L}\\p{N}]+$",
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
            [],
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
        (
            {"type": "array", "items": {"type": "integer"}, "maxItems": 5},
            [
                [],
                [0, 0, 0, 0, 0],
                [0, 0, 0, 0],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "example": [1, 2, 3]},
            [
                [1, 2, 3],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "examples": [[1, 2, 3], [4, 5, 6]]},
            [
                [1, 2, 3],
                [4, 5, 6],
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
        # Unsatisfiable allOf
        (
            {
                "allOf": [
                    {"type": "string", "pattern": "^\\p{Alnum}$"},
                    {"maxLength": 160},
                ]
            },
            [],
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
    ),
)
def test_positive_other(pctx, schema, expected):
    covered = cover_schema(pctx, schema)
    assert covered == expected
    assert_unique(covered)
    assert_conform(covered, schema)


@pytest.mark.parametrize(
    "schema, expected",
    (
        (
            {
                "properties": {
                    "foo": {"type": "string"},
                    "bar": {"type": "string"},
                },
                "required": ["foo", "bar"],
            },
            [
                {
                    "foo": 0,
                    "bar": "",
                },
                {
                    "foo": "",
                    "bar": 0,
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
                    "foo": {"type": "string"},
                    "bar": {"type": "string"},
                },
            },
            [
                {
                    "foo": 0,
                    "bar": "",
                },
                {
                    "foo": "",
                    "bar": 0,
                },
            ],
        ),
        (
            {
                "properties": {
                    "foo": {"type": "string"},
                },
                "additionalProperties": False,
            },
            [
                {
                    "foo": 0,
                },
                {
                    "foo": "",
                    "x-schemathesis-unknown-property": 42,
                },
            ],
        ),
    ),
)
def test_negative_objects(nctx, schema, expected):
    covered = cover_schema(nctx, schema)
    assert covered == expected
    assert_unique(covered)
    assert_not_conform(covered, schema)


@pytest.mark.parametrize(
    "schema, expected",
    (
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
            [4, NotNumber()],
        ),
        (
            {
                "anyOf": [
                    {"minimum": 5},
                    {"type": "string"},
                ],
            },
            [4, 0],
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
                            {
                                "maxLength": 10,
                            },
                            {"type": "null"},
                        ]
                    },
                ]
            },
            [ANY, None, 0],
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
    ),
)
def test_negative_combinators(nctx, schema, expected):
    covered = cover_schema(nctx, schema)
    assert covered == expected
    assert_unique(covered)
    assert_not_conform(covered, schema)
