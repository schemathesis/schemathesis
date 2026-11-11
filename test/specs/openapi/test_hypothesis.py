import datetime
import re
import string
import subprocess
import sys
import textwrap
import uuid
import warnings
from base64 import b64decode
from datetime import date
from pathlib import Path

import jsonschema_rs
import pytest
from flask import jsonify, request
from hypothesis import HealthCheck, Phase, assume, find, given, seed, settings
from hypothesis import strategies as st
from hypothesis.database import InMemoryExampleDatabase
from hypothesis.errors import FailedHealthCheck, NoSuchExample, Unsatisfiable
from hypothesis.internal.observability import with_observability_callback
from jsonschema_rs import Draft4Validator

import schemathesis
from schemathesis.config import GenerationConfig, SchemathesisConfig
from schemathesis.core import NOT_SET
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.jsonschema import FANCY_REGEX_OPTIONS
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis import examples
from schemathesis.generation.hypothesis.builder import HypothesisTestConfig, HypothesisTestMode, create_test
from schemathesis.generation.hypothesis.examples import generate_many
from schemathesis.generation.meta import CaseMetadata, FuzzingPhaseData, GenerationInfo, PhaseInfo, TestPhase
from schemathesis.openapi.generation.filters import is_valid_header
from schemathesis.specs.openapi import _hypothesis, formats
from schemathesis.specs.openapi._hypothesis import make_positive_strategy
from schemathesis.specs.openapi.adapter import v2
from schemathesis.specs.openapi.adapter.parameters import (
    OpenApiParameter,
    OpenApiParameterSet,
)
from schemathesis.specs.openapi.diagnostics import find_slow_parameter, find_unsatisfiable_parameter
from schemathesis.transport.serialization import Binary, quote_all
from test.utils import assert_requests_call, find_first, run_given, to_float32


@pytest.fixture
def operation(ctx):
    return ctx.openapi.load_operation(
        "POST /users",
        {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"type": "string"}}},
            },
            "parameters": [
                {"in": "path", "name": "p1", "required": True, "schema": {"type": "string", "enum": ["FOO"]}},
                {"in": "header", "name": "h1", "required": True, "schema": {"type": "string", "enum": ["FOO"]}},
                {"in": "cookie", "name": "c1", "required": True, "schema": {"type": "string", "enum": ["FOO"]}},
                {"in": "query", "name": "q1", "required": True, "schema": {"type": "string", "enum": ["FOO"]}},
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"body": "TEST"}, {"body": "TEST"}),
        ({"path_parameters": {"p1": "TEST"}}, {"path_parameters": {"p1": "TEST"}}),
        ({"path_parameters": {}}, {"path_parameters": {"p1": "FOO"}}),
        ({"headers": {"h1": "TEST"}}, {"headers": {"h1": "TEST"}}),
        ({"headers": {}}, {"headers": {"h1": "FOO"}}),
        # Even if the explicit value does not match the schema, it should appear in the output
        ({"headers": {"invalid": "T"}}, {"headers": {"h1": "FOO", "invalid": "T"}}),
        ({"cookies": {"c1": "TEST"}}, {"cookies": {"c1": "TEST"}}),
        ({"cookies": {}}, {"cookies": {"c1": "FOO"}}),
        ({"query": {"q1": "TEST"}}, {"query": {"q1": "TEST"}}),
        ({"query": {}}, {"query": {"q1": "FOO"}}),
    ],
)
def test_explicit_attributes(operation, values, expected):
    # When some Case's attribute is passed explicitly to the case strategy
    strategy = operation.as_strategy(**values)

    @run_given(strategy, max_examples=1)
    def test(case):
        # Then it should appear in the final result
        for attr_name, expected_values in expected.items():
            value = getattr(case, attr_name)
            assert value == expected_values


@pytest.fixture
def deeply_nested_schema(ctx):
    return ctx.openapi.build_schema(
        {
            "/data": {
                "get": {
                    "parameters": [
                        {
                            "name": "key",
                            "in": "query",
                            "required": True,
                            "schema": {
                                # In the end it will be replaced with "#/components/schemas/bar"
                                "$ref": "#/components/schemas/foo1"
                            },
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "schemas": {
                "foo1": {"$ref": "#/components/schemas/foo2"},
                "foo2": {"$ref": "#/components/schemas/foo3"},
                "foo3": {"$ref": "#/components/schemas/foo4"},
                "foo4": {"$ref": "#/components/schemas/foo5"},
                "foo5": {"$ref": "#/components/schemas/foo6"},
                "foo6": {"$ref": "#/components/schemas/foo7"},
                "foo7": {"$ref": "#/components/schemas/foo8"},
                "foo8": {"$ref": "#/components/schemas/foo9"},
                "foo9": {"$ref": "#/components/schemas/bar"},
                "bar": {
                    "type": "string",
                },
            }
        },
    )


def test_missed_ref(ctx, deeply_nested_schema):
    # See GH-1167
    # When not resolved references are present in the schema during constructing a strategy
    schema = ctx.openapi.load_schema(deeply_nested_schema["paths"], components=deeply_nested_schema["components"])

    @run_given(schema["/data"]["GET"].as_strategy(), max_examples=10)
    def test(case):
        # Then the reference should be correctly resolved
        assert isinstance(case.query["key"], str)


@pytest.mark.parametrize("location", ["query", "header"])
def test_required_array_parameter_is_non_empty(ctx, location):
    # An empty array for a required parameter serializes to nothing, dropping the parameter
    # from the wire and violating `required: true`.
    operation = ctx.openapi.load_operation(
        "GET /data",
        {
            "parameters": [
                {
                    "in": location,
                    "name": "ids",
                    "required": True,
                    "schema": {"type": "array", "items": {"type": "integer"}},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    container = {"query": "query", "header": "headers"}[location]

    @run_given(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=15)
    def test(case):
        assert getattr(case, container).get("ids"), "Required array parameter generated as empty or absent"


def test_allow_empty_value_false_excludes_empty_string(ctx):
    # `allowEmptyValue: false` forbids sending `?name=`, so an empty string is not a positive value.
    operation = ctx.openapi.load_operation(
        "GET /data",
        {
            "parameters": [
                {
                    "in": "query",
                    "name": "name",
                    "required": True,
                    "allowEmptyValue": False,
                    "schema": {"type": "string"},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    @run_given(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=25)
    def test(case):
        assert case.query["name"] != "", "Empty value generated for `allowEmptyValue: false`"


@pytest.mark.parametrize(
    "parameter_schema",
    [{"type": "null"}, {"anyOf": [{"type": "integer"}, {"type": "null"}]}],
    ids=["null", "anyOf-null"],
)
def test_optional_null_query_parameter_is_omitted(ctx, parameter_schema):
    # No mainstream framework reads `?limit=null` as a JSON null; absence is how a query string says "no value".
    operation = ctx.openapi.load_operation(
        "GET /data",
        {
            "parameters": [{"in": "query", "name": "limit", "required": False, "schema": parameter_schema}],
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    @run_given(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=10)
    def test(case):
        assert case.query.get("limit") != "null"


def test_required_null_query_parameter_is_sent(ctx):
    operation = ctx.openapi.load_operation(
        "GET /data",
        {
            "parameters": [{"in": "query", "name": "limit", "required": True, "schema": {"type": "null"}}],
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    @run_given(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=5)
    def test(case):
        assert case.query == {"limit": "null"}


@pytest.mark.parametrize(
    ("parameter", "expected"),
    [
        ({"name": "flag", "schema": {"enum": [False]}}, {"flag": "false"}),
        (
            {"name": "ids", "schema": {"type": "array", "items": {"type": "null"}, "minItems": 1, "maxItems": 1}},
            {"ids": ["null"]},
        ),
    ],
    ids=["optional-false", "null-inside-array"],
)
def test_optional_query_parameter_is_sent(ctx, parameter, expected):
    operation = ctx.openapi.load_operation(
        "GET /data",
        {
            "parameters": [{"in": "query", "required": False, **parameter}],
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    seen = []

    @run_given(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=10)
    def test(case):
        if case.query not in seen:
            seen.append(case.query)

    assert sorted(seen, key=len) == [{}, expected]


def test_null_path_parameter_is_sent(ctx):
    operation = ctx.openapi.load_operation(
        "GET /data/{key}",
        {
            "parameters": [{"in": "path", "name": "key", "required": True, "schema": {"type": "null"}}],
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    @run_given(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=5)
    def test(case):
        assert case.path_parameters == {"key": "null"}


def test_inlined_definitions(deeply_nested_schema):
    # See GH-1162
    # When not resolved references are present in the schema during constructing a strategy
    # And the referenced schema contains Open API specific keywords
    deeply_nested_schema["components"]["schemas"]["bar"]["nullable"] = True

    schema = schemathesis.openapi.from_dict(deeply_nested_schema)

    @run_given(schema["/data"]["GET"].as_strategy(), max_examples=1)
    def test(case):
        # Then the referenced schema should be properly transformed to the JSON Schema form
        assume(case.query["key"] == "null")


def test_nullable_recursive_reference(ctx):
    # Wrapping a nullable reference in `anyOf` must keep the storage its `$ref` points at.
    operation = ctx.openapi.load_operation(
        "POST /nodes",
        {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Node", "nullable": True}}},
            },
            "responses": {"200": {"description": "OK"}},
        },
        components={
            "schemas": {"Node": {"type": "object", "properties": {"child": {"$ref": "#/components/schemas/Node"}}}}
        },
    )
    strategy = operation.as_strategy()

    assert find_first(strategy, lambda case: case.body is None, max_examples=2000).body is None
    assert find(strategy, lambda case: isinstance(case.body, dict)).body == {}


def _tags_body_schema(ctx, body):
    return ctx.openapi.load_schema(
        {
            "/tags": {
                "post": {
                    "requestBody": {"required": True, "content": {"application/json": {"schema": body}}},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )


@pytest.mark.hypothesis_nested
def test_contains_no_item_can_match_with_zero_min_contains(ctx):
    body = {
        "type": "array",
        "items": {"type": "integer"},
        "contains": {"type": "string"},
        "minContains": 0,
        "maxContains": 1,
    }
    schema = _tags_body_schema(ctx, body)
    validator = jsonschema_rs.validator_for(body)

    @run_given(case=schema["/tags"]["POST"].as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=10)
    def test(case):
        assert validator.is_valid(case.body), case.body
        assert all(isinstance(item, int) for item in case.body), case.body


@pytest.mark.hypothesis_nested
def test_contains_contradicting_items_leaves_only_other_branch(ctx):
    # Every item has at most one character, so no item matches a two-character pattern.
    body = {
        "anyOf": [
            {"type": "array", "items": {"type": "string", "maxLength": 1}, "contains": {"pattern": "^..$"}},
            {"type": "null"},
        ]
    }
    schema = _tags_body_schema(ctx, body)

    @run_given(case=schema["/tags"]["POST"].as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=10)
    def test(case):
        assert case.body is None, case.body


@pytest.mark.hypothesis_nested
def test_contains_over_recursive_reference_keeps_items_valid(ctx):
    body = {
        "type": "array",
        "contains": {"$ref": "#/components/schemas/L"},
        "minContains": 1,
        "maxContains": 2,
        "items": {"not": {"type": "null"}},
    }
    components = {"schemas": {"L": {"type": "array", "items": {"$ref": "#/components/schemas/L"}}}}
    operation = ctx.openapi.load_operation(
        "POST /tags",
        {
            "requestBody": {"required": True, "content": {"application/json": {"schema": body}}},
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
        components=components,
    )
    validator = jsonschema_rs.validator_for({**body, "components": components})

    @run_given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=10)
    def test(case):
        assert validator.is_valid(case.body), case.body


@pytest.mark.hypothesis_nested
def test_valid_headers():
    # When headers are generated
    # And there is no other keywords than "type"
    strategy = make_positive_strategy(
        {
            "type": "object",
            "properties": {"X-Foo": {"type": "string", "pattern": "^[A-F0-9]{12}$"}},
            "required": ["X-Foo"],
            "additionalProperties": False,
        },
        "GET /users/",
        ParameterLocation.HEADER,
        None,
        GenerationConfig(),
        Draft4Validator,
    )

    @run_given(strategy, max_examples=100)
    def test(headers):
        # Then headers are always valid
        assert is_valid_header(headers)


@pytest.mark.parametrize("spec_required", [True, False], ids=["spec-required", "spec-optional"])
def test_header_schema_dedupes_case_insensitive_duplicates(ctx, spec_required):
    # HTTP header names are case-insensitive; the merged headers schema must collapse
    # spec parameter and security-scheme entries that differ only by case, and the
    # canonical first-seen casing must end up `required` whenever any duplicate is required.
    operation = ctx.openapi.load_operation(
        "POST /v2/",
        {
            "parameters": [
                {
                    "name": "authorization",
                    "in": "header",
                    "required": spec_required,
                    "schema": {"type": "string"},
                }
            ],
            "security": [{"Bearer": []}],
            "responses": {"200": {"description": "OK"}},
        },
        components={
            "securitySchemes": {"Bearer": {"type": "http", "scheme": "bearer"}},
        },
    )
    properties = operation.headers.schema["properties"]
    required = operation.headers.schema.get("required", [])
    seen = {name.lower() for name in properties}
    assert len(seen) == len(properties), f"case-insensitive duplicates in headers schema: {sorted(properties)}"
    seen_required = {name.lower() for name in required}
    assert len(seen_required) == len(required), f"case-insensitive duplicates in required: {sorted(required)}"
    # Security scheme always demands the header — the canonical casing must be required either way.
    assert "authorization" in seen_required


def test_configure_headers():
    strategy = make_positive_strategy(
        {
            "type": "object",
            "properties": {
                "X-Foo": {
                    "type": "string",
                    # This is added a few layers above
                    "format": formats.HEADER_FORMAT,
                }
            },
            "required": ["X-Foo"],
            "additionalProperties": False,
        },
        "GET /users/",
        ParameterLocation.HEADER,
        None,
        GenerationConfig(exclude_header_characters="".join({chr(i) for i in range(256)} - {"A", "B", "C"})),
        Draft4Validator,
    )

    @run_given(strategy, max_examples=100)
    def test(headers):
        # Then headers are always valid
        assert is_valid_header(headers)
        assert set(headers["X-Foo"]) - {"A", "B", "C"} == set()


def test_format_values_follow_codec_changed_after_generation():
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string", "format": "json-pointer"}},
        "required": ["q"],
        "additionalProperties": False,
    }
    config = GenerationConfig()
    generate_many(make_positive_strategy(schema, "GET /x", ParameterLocation.QUERY, None, config, Draft4Validator), 1)
    config.update(codec="ascii")
    strategy = make_positive_strategy(schema, "GET /y", ParameterLocation.QUERY, None, config, Draft4Validator)
    assert [value["q"] for value in generate_many(strategy, 50) if not value["q"].isascii()] == []


def test_as_strategy_keeps_passed_containers_unchanged(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/x": {
                "get": {
                    "parameters": [{"name": "tenant", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        config=SchemathesisConfig.from_str('headers = { "X-Configured" = "global" }\n[parameters]\ntenant = "acme"'),
    )
    headers = {"X-Caller": "local"}
    query = {"tenant": "caller", "page": "1"}
    strategy = schema["/x"]["GET"].as_strategy(headers=headers, query=query)
    assert (headers, query) == ({"X-Caller": "local"}, {"tenant": "caller", "page": "1"})

    @run_given(strategy, max_examples=1)
    def test(case):
        assert (case.headers, case.query) == (
            {"X-Caller": "local", "X-Configured": "global"},
            {"tenant": "acme", "page": "1"},
        )


CONFIGURED_OVERRIDES = """
headers = { "X-Project" = "project" }

[parameters]
"path.id" = 7
"query.tenant" = "acme"
"header.X-Tenant" = "header-acme"
"cookie.session" = "fixed"

[[operations]]
include-name = "GET /items/{id}"
headers = { "X-Operation" = "operation" }
"""
OPTIONAL_OPERATION_HEADER = {
    "name": "X-Operation",
    "in": "header",
    "required": False,
    "schema": {"type": "string", "enum": ["generated"]},
}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            "/items/{id}",
            [
                (
                    {"id": 7},
                    {"tenant": "acme"},
                    {"X-Tenant": "header-acme", "X-Operation": "operation", "X-Project": "project"},
                    {"session": "fixed"},
                )
            ],
        ),
        (
            "/other",
            [
                ({}, {}, {"X-Project": "project"}, {}),
                ({}, {}, {"X-Operation": "generated", "X-Project": "project"}, {}),
            ],
        ),
    ],
    ids=["configured-operation", "other-operation"],
)
def test_as_strategy_applies_configured_parameters_and_operation_headers(ctx, path, expected):
    schema = ctx.openapi.load_schema(
        {
            "/items/{id}": {
                "get": {
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "integer", "enum": [1, 2]}},
                        {
                            "name": "tenant",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string", "enum": ["acme", "other"]},
                        },
                        {
                            "name": "X-Tenant",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "enum": ["a", "b"]},
                        },
                        {
                            "name": "session",
                            "in": "cookie",
                            "required": True,
                            "schema": {"type": "string", "enum": ["a", "b"]},
                        },
                        OPTIONAL_OPERATION_HEADER,
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/other": {"get": {"parameters": [OPTIONAL_OPERATION_HEADER], "responses": {"200": {"description": "OK"}}}},
        },
        config=SchemathesisConfig.from_str(CONFIGURED_OVERRIDES),
    )

    @run_given(schema[path]["GET"].as_strategy(), max_examples=10)
    def test(case):
        assert (case.path_parameters, case.query, case.headers, case.cookies) in expected


CONSTRAINED_HEADER_SCHEMAS = [
    {"type": "string", "minLength": 5},
    {"type": "string", "minLength": 20, "maxLength": 30},
    {"type": "string", "maxLength": 10},
    {"type": "string", "description": "annotated, but still a plain header"},
    {"type": "array", "items": {"type": "string"}, "minItems": 1},
    {"type": "object", "additionalProperties": {"type": "string"}, "minProperties": 1},
]


def _strings_in(value):
    # Arrays and objects reach the wire joined into one header value, so every string inside
    # one of them has to be carryable on its own.
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings_in(item)
    elif isinstance(value, dict):
        for name, item in value.items():
            yield name
            yield from _strings_in(item)


@pytest.mark.parametrize("subschema", CONSTRAINED_HEADER_SCHEMAS, ids=str)
def test_constrained_headers_are_valid(subschema):
    strategy = make_positive_strategy(
        {
            "type": "object",
            "properties": {"X-Foo": subschema},
            "required": ["X-Foo"],
            "additionalProperties": False,
        },
        "GET /users/",
        ParameterLocation.HEADER,
        None,
        GenerationConfig(),
        Draft4Validator,
    )

    @run_given(strategy, max_examples=25)
    def test(headers):
        for value in _strings_in(headers["X-Foo"]):
            assert is_valid_header({"X-Foo": value}), repr(value)


def test_header_length_floor_is_reachable():
    # Header values used to come from a generator that trimmed leading whitespace after the draw,
    # so a length floor starved on the values it shortened.
    strategy = make_positive_strategy(
        {
            "type": "object",
            "properties": {"X-Foo": {"type": "string", "format": formats.HEADER_FORMAT, "minLength": 40}},
            "required": ["X-Foo"],
            "additionalProperties": False,
        },
        "GET /users/",
        ParameterLocation.HEADER,
        None,
        GenerationConfig(),
        Draft4Validator,
    )

    @run_given(strategy, max_examples=25)
    def test(headers):
        assert len(headers["X-Foo"]) >= 40, headers
        assert is_valid_header(headers), headers


def test_header_values_never_start_with_whitespace():
    # `requests` refuses to send a value whose first character is whitespace.
    with pytest.raises(NoSuchExample):
        find_first(
            formats.header_values(exclude_characters=formats.INVALID_HEADER_CHARS),
            lambda value: value[:1].strip() != value[:1],
            # The excluded value is rare, so a small budget would pass without looking.
            max_examples=1000,
        )


@pytest.mark.parametrize(
    "schema",
    [{"type": "string"}, {"type": "string", "minLength": 3, "maxLength": 3}],
    ids=["plain", "length-bounds"],
)
@pytest.mark.hypothesis_nested
def test_positive_header_values_never_end_with_whitespace(ctx, schema):
    # Servers strip trailing whitespace from header values, so the received value breaks length bounds.
    operation = ctx.openapi.load_operation(
        "GET /h",
        {
            "parameters": [{"name": "X-A", "in": "header", "required": True, "schema": schema}],
            "responses": {"200": {"description": "OK"}},
        },
    )
    with pytest.raises(NoSuchExample):
        find_first(
            operation.as_strategy(generation_mode=GenerationMode.POSITIVE),
            lambda case: case.headers["X-A"][-1:].strip() != case.headers["X-A"][-1:],
            # The excluded value is rare, so a small budget would pass without looking.
            max_examples=1000,
        )


COOKIE_OCTETS = frozenset(chr(code) for code in range(0x21, 0x7F)) - frozenset('",;\\')


@pytest.mark.parametrize(
    "schema",
    [{"type": "string"}, {"type": "string", "minLength": 3}],
    ids=["plain", "min-length"],
)
@pytest.mark.hypothesis_nested
def test_cookie_values_use_cookie_octets(ctx, schema):
    operation = ctx.openapi.load_operation(
        "GET /c",
        {
            "parameters": [{"name": "c", "in": "cookie", "required": True, "schema": schema}],
            "responses": {"200": {"description": "OK"}},
        },
    )

    # Enough draws to reach separators, quotes and whitespace that servers strip or split on.
    @run_given(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=30)
    def test(case):
        assert set(case.cookies["c"]) <= COOKIE_OCTETS, repr(case.cookies["c"])


@pytest.mark.hypothesis_nested
def test_negative_cookie_values_use_cookie_octets(ctx):
    operation = ctx.openapi.load_operation(
        "GET /c",
        {
            "parameters": [
                {"name": "c", "in": "cookie", "required": True, "schema": {"type": "string", "maxLength": 2}}
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    # A `;` inside a value splits it on the wire, so the server may read a valid prefix.
    @run_given(operation.as_strategy(generation_mode=GenerationMode.NEGATIVE), max_examples=30)
    def test(case):
        for value in _strings_in(case.cookies or {}):
            assert set(value) <= COOKIE_OCTETS, repr(value)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [({"type": "string", "enum": ["a b"]}, "a b"), ({"type": "string", "const": 'a;"b"'}, 'a;"b"')],
    ids=["enum", "const"],
)
@pytest.mark.hypothesis_nested
def test_cookie_values_keep_declared_literals(ctx, schema, expected):
    operation = ctx.openapi.load_operation(
        "GET /c",
        {
            "parameters": [{"name": "c", "in": "cookie", "required": True, "schema": schema}],
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    @run_given(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=5)
    def test(case):
        assert case.cookies == {"c": expected}


@pytest.mark.hypothesis_nested
def test_no_much_filtering_in_headers():
    # When headers are generated
    # And there are keywords other than "type"
    strategy = make_positive_strategy(
        {
            "type": "object",
            "properties": {"X-Foo": {"type": "string", "minLength": 12, "maxLength": 12}},
            "required": ["X-Foo"],
            "additionalProperties": False,
        },
        "GET /users/",
        ParameterLocation.HEADER,
        None,
        GenerationConfig(),
        Draft4Validator,
    )

    @run_given(strategy, max_examples=100)
    def test(_):
        # Then there should be no failed health checks
        pass


def _remote_schema(testdir):
    testdir.makefile(".json", bar='{"bar": {"type": "integer"}}')


def _nested_remote_schema(testdir):
    # Remote reference contains a remote reference
    testdir.makefile(".json", bar='{"bar": {"$ref": "spam.json#/spam"}}')
    testdir.makefile(".json", spam='{"spam": {"type": "integer"}}')


def _deep_nested_remote_schema(testdir):
    # Remote reference contains a remote reference located inside a keyword
    testdir.makefile(
        ".json", bar='{"bar": {"properties": {"a": {"$ref": "spam.json#/spam"}}, "type": "object", "required": ["a"]}}'
    )
    testdir.makefile(".json", spam='{"spam": {"type": "integer"}}')


def _colliding_remote_schema(testdir):
    # References' keys could collide if created without separators
    testdir.makefile(
        ".json",
        bar='{"bar": {"properties": {"a": {"$ref": "b.json#/a"}, "b": {"$ref": "b.json#/ab"}}, '
        '"type": "object", "required": ["a", "b"]}}',
    )
    testdir.makefile(".json", b='{"a": {"$ref": "bc.json#/d"}, "ab": {"$ref": "c.json#/d"}}')
    testdir.makefile(".json", bc='{"d": {"type": "integer"}}')
    testdir.makefile(".json", c='{"d": {"type": "string"}}')


def _back_reference_remote_schema(testdir):
    # Remote reference (1) contains a remote reference (2) that points back to 1
    testdir.makefile(".json", bar='{"bar": {"$ref": "spam.json#/spam"}, "baz": {"type": "integer"}}')
    testdir.makefile(".json", spam='{"spam": {"$ref": "bar.json#/baz"}}')


def _scoped_remote_schema(testdir):
    # The same references might lead to difference files, depending on the resolution scope
    testdir.makefile(".json", bar='{"bar": {"$ref": "./sub/foo.json#/foo"}}')
    sub = testdir.mkdir("sub")
    # `$ref` value is the same, but it is pointing to a different file (as it is a relative path)
    (sub / "foo.json").write_text('{"foo": {"$ref": "./sub/foo.json#/foo"}}', "utf8")
    subsub = sub.mkdir("sub")
    (subsub / "foo.json").write_text('{"foo": {"type": "integer"}}', "utf8")


@pytest.mark.parametrize(
    ("setup", "check"),
    [
        (_remote_schema, lambda query: isinstance(query["key"], int)),
        (_nested_remote_schema, lambda query: isinstance(query["key"], int)),
        (_deep_nested_remote_schema, lambda query: isinstance(query["a"], int)),
        (_colliding_remote_schema, lambda query: isinstance(query["a"], int) and isinstance(query["b"], str)),
        (_back_reference_remote_schema, lambda query: isinstance(query["key"], int)),
        (_scoped_remote_schema, lambda query: isinstance(query["key"], int)),
    ],
)
def test_inline_remote_refs(testdir, deeply_nested_schema, setup, check):
    # See GH-986
    setup(testdir)

    deeply_nested_schema["components"]["schemas"]["foo9"] = {
        "$ref": Path(str(testdir.tmpdir / "bar.json")).as_uri() + "#/bar"
    }

    original = jsonschema_rs.canonical.json.to_string(deeply_nested_schema)
    schema = schemathesis.openapi.from_dict(deeply_nested_schema)

    @run_given(schema["/data"]["GET"].as_strategy(), max_examples=1)
    def test(case):
        # Then the referenced schema should be resolved and the right value generated
        assert check(case.query)

    # And the original schema is not mutated
    assert jsonschema_rs.canonical.json.to_string(deeply_nested_schema) == original


def make_header_param(**kwargs):
    return {
        "/data": {
            "get": {
                "parameters": [
                    {
                        "name": "key",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string", **kwargs},
                    }
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


@pytest.mark.parametrize("subschema", [True, False], ids=["true", "false"])
def test_boolean_header_schema(ctx, subschema):
    # Open API 3.1 lets a parameter be written as a boolean, which claims every value or none.
    operation = ctx.openapi.load_operation(
        "GET /data",
        {
            "parameters": [{"name": "key", "in": "header", "schema": subschema}],
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    @run_given(operation.as_strategy(), max_examples=5)
    def test(case):
        assert is_valid_header(case.headers)


def test_missing_header_filter(ctx):
    # Regression. See GH-1142
    # When some header parameters have the `format` keyword
    # And some don't
    operation = ctx.openapi.load_operation(
        "GET /data",
        {
            "parameters": [
                {
                    "name": "key1",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                },
                {
                    "name": "key2",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string"},
                },
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    @run_given(operation.as_strategy(), max_examples=100)
    def test(case):
        assert is_valid_header(case.headers)


def test_serializing_shared_header_parameters(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "get": {
                    "responses": {"default": {"description": "Ok"}},
                },
                "parameters": [
                    {"name": "key", "type": "boolean", "in": "header"},
                ],
            },
        },
        version="2.0",
    )

    @run_given(schema["/data"]["GET"].as_strategy(), max_examples=100)
    def test(case):
        assert is_valid_header(case.headers)


FORM_BODY = {
    "type": "object",
    "properties": {"a": {"enum": ["value"]}},
    "required": ["a"],
    "additionalProperties": False,
}


@pytest.mark.parametrize(
    "content",
    [
        {"application/json": {"schema": {"type": "object"}}, "text/plain": {"schema": {"type": "string"}}},
        {"application/x-www-form-urlencoded": {"schema": FORM_BODY}},
        {"multipart/form-data": {"schema": FORM_BODY}},
    ],
    ids=["json-and-text", "urlencoded", "multipart"],
)
def test_declared_content_type_header_matches_the_sent_body(ctx, app_runner, content):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "parameters": [
                        {"in": "header", "name": "Content-Type", "required": True, "schema": {"type": "string"}}
                    ],
                    "requestBody": {"required": True, "content": content},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/items", methods=["POST"])
    def items():
        return jsonify({"mimetype": request.mimetype, "form": request.form.to_dict()})

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))

    @run_given(case=schema["/items"]["POST"].as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=10)
    def test(case):
        form = case.body if case.media_type in ("application/x-www-form-urlencoded", "multipart/form-data") else {}
        assert case.call().json() == {"mimetype": case.media_type, "form": form}


@pytest.mark.parametrize(
    ("header_schema", "expected"),
    [
        ({"enum": ["application/xml"]}, {"application/xml"}),
        ({"type": "string", "pattern": "^text/"}, {"text/plain"}),
    ],
    ids=["media-type-excluded", "one-media-type-admitted"],
)
def test_declared_content_type_header_keeps_values_its_schema_admits(ctx, header_schema, expected):
    operation = ctx.openapi.load_operation(
        "POST /items",
        {
            "parameters": [{"in": "header", "name": "Content-Type", "required": True, "schema": header_schema}],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": {"type": "object"}},
                    "text/plain": {"schema": {"type": "string"}},
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )
    seen = set()

    @run_given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=10)
    def test(case):
        if case.media_type == "text/plain" or "application/xml" in expected:
            seen.add(case.headers["Content-Type"])

    assert seen == expected


def test_filter_urlencoded(ctx):
    # When API schema allows for inputs that can't be serialized to `application/x-www-form-urlencoded`
    # Then such examples should be filtered out during generation
    operation = ctx.openapi.load_operation(
        "POST /test",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/x-www-form-urlencoded": {
                        "schema": {
                            "type": "array",
                            "items": {
                                "properties": {
                                    "value": {
                                        "enum": ["A"],
                                    },
                                    "key": {
                                        "enum": ["B"],
                                    },
                                },
                                "required": ["key", "value"],
                                # Additional properties are allowed
                            },
                            "maxItems": 3,
                        }
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    @run_given(operation.as_strategy(), max_examples=15, phases=[Phase.generate])
    def test(case):
        assert_requests_call(case)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("foo", True),
        ("тест", False),
        ("\n", False),
    ],
)
def test_is_valid_header(value, expected):
    assert is_valid_header({"foo": value}) is expected


def test_unregister_string_format_valid():
    name = "example"
    schemathesis.openapi.format(name, st.text())
    assert name in _hypothesis.STRING_FORMATS
    formats.unregister_string_format(name)
    assert name not in _hypothesis.STRING_FORMATS


def test_unregister_string_format_invalid():
    with pytest.raises(ValueError, match="Unknown Open API format: unknown"):
        formats.unregister_string_format("unknown")


@pytest.mark.hypothesis_nested
def test_email_format_passes_jsonschema_rs_validation(ctx):
    operation = ctx.openapi.load_operation(
        "POST /users",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"email": {"type": "string", "format": "email"}},
                            "required": ["email"],
                        }
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )
    validator = Draft4Validator({"type": "string", "format": "email"})

    @given(case=operation.as_strategy())
    @settings(deadline=None, suppress_health_check=list(HealthCheck))
    def inner(case):
        assert validator.is_valid(case.body["email"])

    inner()


@pytest.mark.hypothesis_nested
def test_builtin_format_override(ctx):
    # See GH-3269
    # When a built-in format is overridden with a custom strategy
    today = date.today()
    schemathesis.openapi.format("date", st.dates(max_value=today).map(str))
    operation = ctx.openapi.load_operation(
        "GET /events",
        {
            "parameters": [
                {
                    "name": "start_date",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string", "format": "date"},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    @given(case=operation.as_strategy())
    @settings(max_examples=20, deadline=None)
    def inner(case):
        # Then all generated values respect the override
        assert date.fromisoformat(case.query["start_date"]) <= today

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        inner()


@pytest.mark.hypothesis_nested
def test_custom_format_path_value_with_slash_is_accepted_when_explicit(ctx):
    # See GH-3571
    format_name = "ip-network-explicit-gh3571"
    schemathesis.openapi.format(format_name, st.sampled_from(["192.168.1.0/24"]))
    operation = ctx.openapi.load_operation(
        "GET /blocks/{block}",
        {
            "parameters": [
                {
                    "name": "block",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "format": format_name},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    @run_given(case=operation.as_strategy(), max_examples=1)
    def inner(case):
        assert case.path_parameters["block"] == "192.168.1.0%2F24"


@pytest.mark.hypothesis_nested
def test_custom_format_path_value_that_cannot_be_url_encoded_is_rejected(ctx):
    format_name = "lone-surrogate-path"
    schemathesis.openapi.format(format_name, st.just("a\ud800"))
    operation = ctx.openapi.load_operation(
        "GET /blocks/{block}",
        {
            "parameters": [
                {
                    "name": "block",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "format": format_name},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    @given(case=operation.as_strategy())
    @settings(max_examples=1, deadline=None)
    def inner(case):
        pass

    with pytest.raises(Unsatisfiable):
        inner()


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize("version", ["3.0.2", "3.1.0"])
def test_referenced_parameter_example_mixed_into_strategy(ctx, version):
    operation = ctx.openapi.load_operation(
        "GET /route",
        {
            "parameters": [
                {
                    "name": "q",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string"},
                    "examples": {"first": {"$ref": "#/components/examples/Special"}},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
        version=version,
        components={"examples": {"Special": {"value": "special-ref"}}},
    )
    strategy = operation.as_strategy(generation_mode=GenerationMode.POSITIVE)
    assert find_first(strategy, lambda case: case.query["q"] == "special-ref", max_examples=100).query == {
        "q": "special-ref"
    }


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    "parameter",
    [
        {"schema": {"type": "string", "pattern": "^[0-9]+/[0-9]+$"}, "example": "foo"},
        {"schema": {"type": "string", "pattern": "^[{}]$"}},
    ],
    ids=["example-without-slash", "encoded-braces"],
)
def test_path_parameter_values_needing_encoded_characters_are_filtered(ctx, parameter):
    operation = ctx.openapi.load_operation(
        "GET /blocks/{block}",
        {
            "parameters": [{"name": "block", "in": "path", "required": True, **parameter}],
            "responses": {"200": {"description": "OK"}},
        },
    )
    strategy = _hypothesis.get_parameters_strategy(
        operation,
        GenerationMode.POSITIVE,
        ParameterLocation.PATH,
        GenerationConfig(),
        mix_examples=False,
    )

    @given(value=strategy)
    @settings(max_examples=1, deadline=None)
    def inner(value):
        pass

    with pytest.raises((FailedHealthCheck, Unsatisfiable)):
        inner()


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    ("version", "schema", "example"),
    [
        ("3.0.2", {"type": "string", "pattern": r"^[0-9.]+/[0-9]+$"}, {"example": "192.168.1.0/24"}),
        ("3.0.2", {"type": "string", "enum": ["192.168.1.0/24"]}, {}),
        ("3.1.0", {"type": "string", "const": "192.168.1.0/24"}, {}),
    ],
    ids=["example", "enum", "const"],
)
def test_path_example_with_slash_allows_encoded_slash(ctx, version, schema, example):
    operation = ctx.openapi.load_operation(
        "GET /blocks/{block}",
        {
            "parameters": [{"name": "block", "in": "path", "required": True, "schema": schema, **example}],
            "responses": {"200": {"description": "OK"}},
        },
        version=version,
    )
    strategy = _hypothesis.get_parameters_strategy(
        operation,
        GenerationMode.POSITIVE,
        ParameterLocation.PATH,
        GenerationConfig(),
        mix_examples=False,
    )

    @run_given(value=strategy, max_examples=10)
    def inner(value):
        assert "%2F" in value["block"]


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    "item_schema",
    [
        {"type": "integer", "minimum": -2147483648, "maximum": 2147483647},
        {"type": "integer", "exclusiveMinimum": -2147483649, "exclusiveMaximum": 2147483648},
    ],
    ids=["inclusive", "exclusive"],
)
def test_positive_bias_keeps_path_integer_within_bounds(ctx, item_schema):
    # Positive-ID bias flips negatives via abs(); abs(int32 min) = int32 max + 1 must not exceed the declared max.
    operation = ctx.openapi.load_operation(
        "GET /items/{item_id}",
        {
            "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": item_schema}],
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )
    # High count: the flip only overshoots when the exact int32 minimum is drawn, a rare boundary draw.
    values = [int(case.path_parameters["item_id"]) for case in generate_many(operation.as_strategy(), 1000)]
    out_of_range = [value for value in values if value > 2147483647 or value < -2147483648]
    assert not out_of_range, f"Out-of-range path integers: {sorted(set(out_of_range))}"


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    "item_schema",
    [
        {"type": "integer", "enum": [0, 5]},
        {"type": "integer", "multipleOf": 5},
        {"type": "integer", "maximum": 1, "exclusiveMaximum": True},
        {"type": "integer", "not": {"enum": [1]}},
    ],
    ids=["enum", "multipleOf", "exclusiveMaximum", "not"],
)
def test_positive_bias_keeps_path_integer_schema_valid(ctx, item_schema):
    operation = ctx.openapi.load_operation(
        "GET /items/{item_id}",
        {
            "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": item_schema}],
            "responses": {"200": {"description": "OK"}},
        },
    )
    validator = Draft4Validator(item_schema)
    # Enough draws for the probabilistic positive-ID bias to fire on a non-positive value at least once.
    values = [
        case.path_parameters["item_id"]
        for case in generate_many(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), 20)
    ]
    assert [value for value in values if not validator.is_valid(value)] == []


def test_positive_bias_turns_non_positive_path_integers_positive(ctx):
    operation = ctx.openapi.load_operation(
        "GET /items/{item_id}",
        {
            "parameters": [
                {"name": "item_id", "in": "path", "required": True, "schema": {"type": "integer", "enum": [-1, 0, 1]}}
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    values = [
        case.path_parameters["item_id"]
        for case in generate_many(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), 20)
    ]
    assert values.count(1) > values.count(-1) + values.count(0)


def test_positive_bias_keeps_non_integer_and_out_of_schema_path_values(ctx):
    schemas = {
        "id": {"type": "integer", "enum": [-3, 3]},
        "zero": {"type": "integer", "enum": [0, 1]},
        "flag": {"type": "boolean"},
        "bounded": {"type": "integer", "enum": [-4, 5]},
        "amount": {"type": "number", "maximum": -1},
    }
    operation = ctx.openapi.load_operation(
        "GET /items/{id}/{zero}/{flag}/{bounded}/{amount}",
        {
            "parameters": [
                {"name": name, "in": "path", "required": True, "schema": schema} for name, schema in schemas.items()
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    cases = generate_many(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), 20)
    assert {(name, value) for case in cases for name, value in case.path_parameters.items() if name != "amount"} <= {
        ("id", -3),
        ("id", 3),
        ("zero", 0),
        ("zero", 1),
        ("flag", "false"),
        ("flag", "true"),
        ("bounded", -4),
        ("bounded", 5),
    }


def test_custom_format_with_bytes(testdir):
    # See GH-3289: custom formats returning bytes should work
    testdir.make_test(
        """
import schemathesis
from hypothesis import strategies as st

# Register a custom format that returns bytes
pdf_strategy = st.sampled_from([
    b"%PDF-1.4\\n1 0 obj\\n",
    b"%PDF-1.5\\n%\\xe2\\xe3",
])
schemathesis.openapi.format("custom-pdf", pdf_strategy)

schema = schemathesis.openapi.from_dict({
    "openapi": "3.0.0",
    "info": {"title": "Test", "version": "1.0.0"},
    "paths": {
        "/upload": {
            "put": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/octet-stream": {
                            "schema": {
                                "type": "string",
                                "format": "custom-pdf"
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "OK"}}
            }
        }
    }
})

@schema.parametrize()
def test_api(case):
    # Should not crash
    pass
        """,
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)


@given(st.data())
@settings(max_examples=50)
def test_uuid_format_is_rfc4122(data):
    value = data.draw(formats.get_default_format_strategies()["uuid"])
    assert uuid.UUID(value).variant == uuid.RFC_4122


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"name": "a%5Cb"}, {"name": "a_b"}),
        ({"name": "%5C%5c%01%1F%7F%7f"}, {"name": "______"}),
        ({"name": "ok-value-1"}, {"name": "ok-value-1"}),
        ({"name": "literal-%25"}, {"name": "literal-%25"}),
        ({"id": 42}, {"id": 42}),
    ],
    ids=["backslash", "control-and-del", "safe-string", "literal-percent", "non-string"],
)
def test_strip_path_decoder_unsafe(raw, expected):
    assert _hypothesis._strip_path_decoder_unsafe(raw) == expected


@pytest.mark.hypothesis_nested
def test_path_string_sanitized_when_decoder_strict(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/products/{productName}": {
                "get": {
                    "parameters": [
                        {"name": "productName", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema.adapt_to_path_decoder_rejection()
    operation = schema["/products/{productName}"]["GET"]

    @run_given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=30)
    def inner(case):
        value = case.path_parameters["productName"]
        upper = value.upper()
        assert "%5C" not in upper, value
        assert "%7F" not in upper, value
        assert not any(f"%{i:02X}" in upper for i in range(0x20)), value


@pytest.mark.hypothesis_nested
def test_float_format_snapping_preserves_literal_const(ctx):
    # A literal value that happens to be shaped like a float schema must not be rewritten as if it were one.
    literal = {"format": "float", "exclusiveMinimum": 0}
    operation = ctx.openapi.load_operation(
        "POST /route",
        {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"const": literal}}},
            },
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    @run_given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=5)
    def inner(case):
        assert case.body == literal, case.body


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize("bound", [1e39, 10**1000], ids=["float", "integer"])
def test_float_format_bound_outside_single_precision_range(ctx, bound):
    # An exclusive bound beyond the float32 range must clamp to a representable value, not crash strategy preparation.
    operation = ctx.openapi.load_operation(
        "GET /route",
        {
            "parameters": [
                {
                    "name": "f",
                    "in": "query",
                    "schema": {"type": "number", "format": "float", "exclusiveMaximum": bound},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    @run_given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=5)
    def inner(case):
        value = case.query.get("f")
        if value is not None:
            assert to_float32(float(value)) < 1e39, value


def test_float_format_snapping_skips_negation():
    # Tightening a `not` subschema weakens the negation, so the walker must leave it untouched.
    schema = {"not": {"type": "number", "format": "float", "exclusiveMinimum": 0}}
    _hypothesis.snap_float32_bounds(schema)
    assert schema == {"not": {"type": "number", "format": "float", "exclusiveMinimum": 0}}


def test_float_format_snapping_number_integer_union():
    # The number branch of a union still needs float32 bounds; only integer-only schemas are skipped.
    schema = {"type": ["number", "integer"], "format": "float", "exclusiveMinimum": 0}
    _hypothesis.snap_float32_bounds(schema)
    assert "exclusiveMinimum" not in schema
    assert schema["minimum"] > 0


FLOAT32_MAX = 3.4028234663852886e38


@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        ({"exclusiveMinimum": 0}, {"minimum": 1.401298464324817e-45}),
        ({"exclusiveMaximum": 0}, {"maximum": -1.401298464324817e-45}),
        ({"exclusiveMinimum": -0.5}, {"minimum": -0.4999999701976776}),
        ({"exclusiveMaximum": 0.5}, {"maximum": 0.4999999701976776}),
        ({"exclusiveMaximum": 0.7}, {"maximum": 0.699999988079071}),
        ({"exclusiveMaximum": FLOAT32_MAX}, {"maximum": 3.4028232635611926e38}),
        ({"exclusiveMinimum": -FLOAT32_MAX}, {"minimum": -3.4028232635611926e38}),
        ({"exclusiveMinimum": -1e39}, {"minimum": -FLOAT32_MAX}),
        ({"exclusiveMaximum": 1e39}, {"maximum": FLOAT32_MAX}),
    ],
    ids=[
        "above-zero",
        "below-zero",
        "above-negative",
        "below-positive",
        "below-rounds-down",
        "below-largest",
        "above-lowest",
        "above-out-of-range",
        "below-out-of-range",
    ],
)
def test_float_format_snapping_steps_to_the_adjacent_single(bounds, expected):
    schema = {"type": "number", "format": "float", **bounds}
    _hypothesis.snap_float32_bounds(schema)
    assert schema == {"type": "number", "format": "float", **expected}


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    ("schema", "sign"),
    [
        ({"type": "number", "format": "float", "minimum": 0, "exclusiveMinimum": True}, 1),
        ({"type": "number", "format": "float", "maximum": 0, "exclusiveMaximum": True}, -1),
        ({"type": "number", "format": "float", "exclusiveMinimum": True}, 0),
        ({"type": "number", "format": "float", "exclusiveMaximum": True}, 0),
    ],
    ids=["min", "max", "min-without-companion-bound", "max-without-companion-bound"],
)
def test_float_format_openapi_30_boolean_exclusive_bounds(ctx, schema, sign):
    # OpenAPI 3.0 spells exclusive bounds as a boolean modifier: a `true` on a `format: float` bound must
    # hold after float32 narrowing, and a `true` with no companion `minimum`/`maximum` must still generate.
    operation = ctx.openapi.load_operation(
        "GET /route",
        {
            "parameters": [{"name": "f", "in": "query", "required": True, "schema": schema}],
            "responses": {"200": {"description": "OK"}},
        },
    )
    produced = []

    @run_given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=15)
    def inner(case):
        value = case.query.get("f")
        if value is None:
            return
        produced.append(value)
        narrowed = to_float32(float(value))
        if sign > 0:
            assert narrowed > 0, value
        elif sign < 0:
            assert narrowed < 0, value

    assert produced


@pytest.mark.hypothesis_nested
def test_float_format_on_integer_type_keeps_bound(ctx):
    # `format: float` on an integer schema is contradictory; snapping must not erase its bound and let
    # generation drift below it.
    operation = ctx.openapi.load_operation(
        "GET /route",
        {
            "parameters": [
                {
                    "name": "f",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "integer", "format": "float", "exclusiveMinimum": 1000},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    @run_given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=10)
    def inner(case):
        value = case.query.get("f")
        if value is not None:
            assert int(value) > 1000, value


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize("key", ["example", "examples"])
def test_float_format_collapsing_example_not_mixed_into_strategy(ctx, key):
    # A spec example valid as float64 but collapsing to 0 in float32 must not be mixed into positive generation.
    value = [5e-324] if key == "examples" else 5e-324
    operation = ctx.openapi.load_operation(
        "GET /route",
        {
            "parameters": [
                {
                    "name": "f",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "number", "format": "float", "exclusiveMinimum": 0, key: value},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    @run_given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=50)
    def inner(case):
        value = case.query.get("f")
        if value is not None:
            assert to_float32(float(value)) > 0, value


@pytest.mark.parametrize("name", ["const", "enum", "default", "example", "examples", "if", "not"])
def test_float_format_snapping_property_named_like_keyword(name):
    # Under `properties` the keys are names, not keywords; a property named like a keyword still needs snapping.
    schema = {"type": "object", "properties": {name: {"type": "number", "format": "float", "exclusiveMinimum": 0}}}
    _hypothesis.snap_float32_bounds(schema)
    prop = schema["properties"][name]
    assert "exclusiveMinimum" not in prop, prop
    assert prop["minimum"] > 0


def test_float_format_snapping_skips_conditional_but_not_branches():
    # `if` desugars to `not if` in the else-branch, so snapping it weakens that negation; `then`/`else` are safe.
    schema = {
        "if": {"type": "number", "format": "float", "exclusiveMinimum": 0},
        "then": {"type": "number", "format": "float", "exclusiveMinimum": 0},
    }
    _hypothesis.snap_float32_bounds(schema)
    assert schema["if"] == {"type": "number", "format": "float", "exclusiveMinimum": 0}
    assert "exclusiveMinimum" not in schema["then"]
    assert schema["then"]["minimum"] > 0


@pytest.mark.parametrize(
    "bound",
    [{"exclusiveMinimum": 10**1000}, {"exclusiveMaximum": -(10**1000)}, {"exclusiveMaximum": -1e39}],
    ids=["minimum", "maximum", "maximum-float"],
)
def test_float_format_snapping_unsatisfiable_bound(bound):
    # No finite float32 lies past the bound, so the node must become unsatisfiable, not gain an infinite bound.
    schema = {"type": "number", "format": "float", **bound}
    _hypothesis.snap_float32_bounds(schema)
    assert schema == {"not": {}}


@pytest.mark.parametrize(
    ("siblings", "valid", "invalid"),
    [
        ({"type": ["number", "null"]}, [None], [5.0]),
        ({}, ["text"], [5.0]),
        ({"enum": ["ok"]}, ["ok"], [None, 5.0]),
    ],
    ids=["union", "typeless", "typeless-enum"],
)
def test_float_format_unsatisfiable_bound_keeps_non_numeric(siblings, valid, invalid):
    # Forbidding the empty numeric branch keeps non-numeric values and sibling constraints (e.g. `enum`) valid.
    schema = {"format": "float", "exclusiveMinimum": 10**1000, **siblings}
    _hypothesis.snap_float32_bounds(schema)
    for value in valid:
        assert jsonschema_rs.is_valid(schema, value), value
    for value in invalid:
        assert not jsonschema_rs.is_valid(schema, value), value


def test_float_format_invalid_exclusive_bound_left_for_validation():
    # A non-bool/non-numeric exclusive bound is an invalid schema; don't silently drop it into a valid one.
    schema = {"type": "number", "format": "float", "exclusiveMinimum": "bad"}
    _hypothesis.snap_float32_bounds(schema)
    assert schema["exclusiveMinimum"] == "bad"


def test_float_format_invalid_exclusive_bound_kept_when_other_bound_unsatisfiable():
    # An unsatisfiable resolvable bound must not erase a second invalid bound via the empty-branch rewrite.
    schema = {"type": "number", "format": "float", "exclusiveMinimum": 10**1000, "exclusiveMaximum": "bad"}
    _hypothesis.snap_float32_bounds(schema)
    assert schema["exclusiveMaximum"] == "bad"


def test_float_format_snapping_dependency_named_like_keyword():
    # `dependencies` maps property names to subschemas; a dependency named like a keyword still needs snapping.
    schema = {"type": "object", "dependencies": {"not": {"type": "number", "format": "float", "exclusiveMinimum": 0}}}
    _hypothesis.snap_float32_bounds(schema)
    dependency = schema["dependencies"]["not"]
    assert "exclusiveMinimum" not in dependency
    assert dependency["minimum"] > 0


def test_unsatisfiable_parameter_is_the_one_the_engine_cannot_draw(ctx):
    operation = ctx.openapi.load_operation(
        "GET /data",
        {
            "parameters": [
                {
                    "in": "query",
                    "name": "choice",
                    "required": True,
                    "schema": {
                        "oneOf": [
                            {"type": "object", "properties": {"a": {"type": "string"}}},
                            {"type": "object", "properties": {"b": {"type": "string"}}},
                        ]
                    },
                },
                {
                    "in": "query",
                    "name": "impossible",
                    "required": True,
                    "schema": {"type": "string", "minLength": 5, "maxLength": 2},
                },
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    found = find_unsatisfiable_parameter(operation)

    assert found is not None
    assert found.name == "impossible"


@pytest.mark.parametrize(
    "probe",
    [lambda operation: find_slow_parameter(operation, HealthCheck.too_slow), find_unsatisfiable_parameter],
    ids=["slow-parameter", "unsatisfiable"],
)
def test_probe_survives_a_parameter_that_cannot_build(ctx, probe):
    # A strategy failing at build time must not escape the error-reporting probe
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "contains": {"const": 7},
                            "minContains": 5000000,
                        }
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    found = probe(operation)

    assert found is not None
    assert found.name == "application/json"


def test_fuzzing_phase_uses_its_generation_config(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "integer"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema.config.generation.update(modes=[GenerationMode.POSITIVE])
    schema.config.phases.fuzzing.generation.update(modes=[GenerationMode.NEGATIVE])
    operation = schema["/data"]["POST"]
    generated = []

    def collect(case):
        generated.append(case)

    test = create_test(
        operation=operation,
        test_func=collect,
        config=HypothesisTestConfig(
            modes=[HypothesisTestMode.FUZZING],
            project=schema.config,
            settings=settings(max_examples=1, database=InMemoryExampleDatabase()),
        ),
    )

    test()

    assert [case.meta.generation.mode for case in generated] == [GenerationMode.NEGATIVE]


@pytest.mark.parametrize(
    ("generation", "mode", "offending"),
    [
        (
            {"codec": "ascii"},
            GenerationMode.POSITIVE,
            lambda case: [value for value in (case.query["q"], case.body) if not value.isascii()],
        ),
        (
            {"allow-x00": False},
            GenerationMode.POSITIVE,
            lambda case: [value for value in (case.query["q"], case.body) if "\x00" in value],
        ),
        (
            {"exclude-header-characters": string.ascii_letters},
            GenerationMode.POSITIVE,
            lambda case: [case.headers["X-Token"]] if set(case.headers["X-Token"]) & set(string.ascii_letters) else [],
        ),
        (
            {"dictionaries": {"string": {"dictionary": "edge", "probability": 1.0}}},
            GenerationMode.POSITIVE,
            lambda case: [case.query["q"]] if case.query["q"] != "dictionary-value" else [],
        ),
        (
            {"allow-extra-parameters": False},
            GenerationMode.NEGATIVE,
            lambda case: sorted(set(case.query or {}) - {"q"}),
        ),
    ],
    ids=["codec", "allow-x00", "exclude-header-characters", "dictionaries", "allow-extra-parameters"],
)
def test_phase_generation_config_applies_after_another_phase_generated(ctx, generation, mode, offending):
    config = SchemathesisConfig.from_dict(
        {
            "dictionaries": {"edge": {"values": ["dictionary-value"]}},
            "phases": {"stateful": {"generation": generation}},
        }
    )
    operation = ctx.openapi.load_operation(
        "POST /items",
        {
            "parameters": [
                {"name": "q", "in": "query", "required": True, "schema": {"type": "string", "minLength": 10}},
                {"name": "X-Token", "in": "header", "required": True, "schema": {"type": "string", "minLength": 10}},
            ],
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"type": "string", "minLength": 10}}},
            },
            "responses": {"200": {"description": "OK"}},
        },
        config=config,
    )
    generate_many(operation.as_strategy(generation_mode=mode, phase=TestPhase.FUZZING), 30)

    cases = generate_many(operation.as_strategy(generation_mode=mode, phase=TestPhase.STATEFUL), 30)

    assert [value for case in cases for value in offending(case)] == []


def test_negative_case_can_negate_the_body_alone(ctx):
    # A server may check the path before the body, so negating every location at once hides body handling.
    operation = ctx.openapi.load_operation(
        "POST /items/{kind}",
        {
            "parameters": [
                {
                    "name": "kind",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "enum": ["a", "b"]},
                }
            ],
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "string"}}}},
            "responses": {"200": {"description": "OK"}},
        },
    )
    strategy = operation.as_strategy(generation_mode=GenerationMode.NEGATIVE)

    find_first(
        strategy,
        lambda case: case.path_parameters["kind"] in ("a", "b") and not isinstance(case.body, str),
        max_examples=100,
    )


@pytest.mark.parametrize(
    ("parameters", "location", "explicit"),
    [
        (
            [{"name": "limit", "in": "query", "required": True, "schema": {"type": "integer"}}],
            ParameterLocation.QUERY,
            {"query": {"LIMIT": "5"}},
        ),
        (
            [
                {"name": "sid", "in": "cookie", "required": True, "schema": {"type": "integer"}},
                {"name": "SID", "in": "cookie", "required": True, "schema": {"type": "integer"}},
            ],
            ParameterLocation.COOKIE,
            {"cookies": {"sid": "5"}},
        ),
    ],
    ids=["query", "cookie"],
)
def test_explicit_value_differing_in_case_leaves_location_negatable(ctx, parameters, location, explicit):
    # Only header names are case-insensitive, so `LIMIT` does not cover `limit`.
    header = {"name": "X-Id", "in": "header", "required": True, "schema": {"type": "integer"}}
    operation = ctx.openapi.load_operation(
        "GET /items", {"parameters": [*parameters, header], "responses": {"200": {"description": "OK"}}}
    )
    strategy = operation.as_strategy(generation_mode=GenerationMode.NEGATIVE, **explicit)

    find_first(
        strategy,
        lambda case: location in case.meta.components and case.meta.components[location].mode.is_negative,
        max_examples=100,
    )


@pytest.mark.parametrize("version", ["3.0.2", "2.0"], ids=["openapi-3.0", "swagger-2"])
def test_positive_bodies_ignore_keywords_next_to_root_ref(ctx, version):
    prefix = "#/definitions" if version == "2.0" else "#/components/schemas"
    base = {"type": "object", "properties": {"id": {"type": "integer"}}}
    container = {"definitions": {"Base": base}} if version == "2.0" else {"components": {"schemas": {"Base": base}}}
    body = {"$ref": f"{prefix}/Base", "required": ["extra"]}
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "parameters": [{"in": "body", "name": "body", "required": True, "schema": body}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        if version == "2.0"
        else {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version=version,
        **container,
    )
    find_first(
        schema["/data"]["POST"].as_strategy(),
        lambda case: "extra" not in case.body,
        max_examples=10,
    )


def test_ref_with_sibling_anyof_against_anyof_target(ctx):
    body = {
        "type": "object",
        "properties": {
            "loadStrategyClass": {
                "$ref": "#/components/schemas/Strategy",
                "anyOf": [
                    {"const": "ai.starlake.IngestionNameStrategy"},
                    {"const": "ai.starlake.IngestionTimeStrategy"},
                ],
            }
        },
    }
    components = {
        "schemas": {
            "Strategy": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "boolean"},
                    {"type": "number"},
                    {"type": "integer"},
                    {"type": "null"},
                ],
            }
        }
    }
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": body}},
            },
            "responses": {"200": {"description": "OK"}},
        },
        components=components,
    )
    validator = jsonschema_rs.validator_for({**body, "components": components})

    @given(operation.as_strategy())
    @settings(max_examples=1, deadline=None, database=InMemoryExampleDatabase())
    def test(case):
        validator.validate(case.body)

    test()


def test_draft4_typed_integer_enum_stays_in_enum(ctx):
    # OpenAPI 3.0 is Draft 4, where an integer enum canonicalizes to a typed group; body must stay in the enum.
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"type": "integer", "enum": [1, 2]}}},
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    @given(operation.as_strategy())
    @settings(max_examples=10, deadline=None, database=InMemoryExampleDatabase())
    def test(case):
        assert case.body in (1, 2), case.body

    test()


def test_empty_enum_body_is_omitted(ctx):
    # An empty enum canonicalizes to `false`; an optional body carrying it generates no value.
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "required": False,
                "content": {"application/json": {"schema": {"enum": []}}},
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    @given(operation.as_strategy())
    @settings(max_examples=5, deadline=None, database=InMemoryExampleDatabase())
    def test(case):
        assert case.body is NOT_SET

    test()


def test_multitype_null_boolean_body(ctx):
    # A 3.1 `type: [null, boolean]` body lifts to a multi-type union; values stay null or boolean.
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"type": ["null", "boolean"]}}},
            },
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    @given(operation.as_strategy())
    @settings(max_examples=10, deadline=None, database=InMemoryExampleDatabase())
    def test(case):
        assert case.body is None or isinstance(case.body, bool), case.body

    test()


@pytest.mark.parametrize(
    "body_schema",
    [{"type": "string"}, {"type": "string", "pattern": ".+"}],
    ids=["plain", "pattern"],
)
@pytest.mark.parametrize("codec", ["utf-8", "ascii", None])
@pytest.mark.parametrize("allow_x00", [True, False])
def test_string_body_respects_alphabet(ctx, codec, allow_x00, body_schema):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body_schema}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema.config.generation.allow_x00 = allow_x00
    schema.config.generation.codec = codec

    @given(schema["/data"]["POST"].as_strategy())
    @settings(max_examples=10, deadline=None, database=InMemoryExampleDatabase())
    def test(case):
        assert isinstance(case.body, str)
        if not allow_x00:
            assert "\x00" not in case.body
        if codec is not None:
            case.body.encode(codec)

    test()


@pytest.mark.parametrize(
    "body_schema",
    [{"type": "string"}, {"type": "string", "pattern": ".+"}],
    ids=["plain", "pattern"],
)
def test_string_body_without_codec_has_no_lone_surrogates(ctx, body_schema):
    # Lone surrogates cannot be encoded as UTF-8, so checking a drawn value against a pattern crashed.
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body_schema}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema.config.generation.codec = None
    bodies = []

    # Seed 43 reaches a lone surrogate within 10 draws for both schemas.
    @run_given(schema["/data"]["POST"].as_strategy(), max_examples=10, seed=43)
    def collect(case):
        bodies.append(case.body)

    assert [body for body in bodies if any("\ud800" <= char <= "\udfff" for char in body)] == []


def test_anyof_disjoint_branches_body(ctx):
    # A 3.1 `anyOf` of branches that don't merge into one type lifts to a union; values stay in one branch.
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}},
            },
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    @given(operation.as_strategy())
    @settings(max_examples=10, deadline=None, database=InMemoryExampleDatabase())
    def test(case):
        assert case.body is None or isinstance(case.body, int), case.body

    test()


@pytest.mark.parametrize("location", sorted(set(ParameterLocation) - {ParameterLocation.UNKNOWN}))
@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_get_examples(ctx, location):
    if location == ParameterLocation.BODY:
        # In Open API 2.0, the `body` parameter has a name, which is ignored
        # But we'd like to use this object as a payload; therefore, we put one extra level of nesting
        example = expected = {"name": "John"}
        media_type = "application/json"
        definition = {
            "in": location,
            "name": "name",
            "required": True,
            "schema": {"type": "object", "properties": {"name": {"type": "string"}}},
            "x-example": example,
        }
    else:
        example = "John"
        expected = {"name": example}
        media_type = None  # there is no payload
        definition = {
            "in": location,
            "name": "name",
            "required": True,
            "type": "string",
            "x-example": example,
        }
    container = location.container_name
    path = "/users/{name}" if location == ParameterLocation.PATH else "/users"
    operation = ctx.openapi.load_operation(
        f"POST {path}", {"parameters": [definition], "responses": {"200": {"description": "OK"}}}, version="2.0"
    )
    strategies = operation.get_strategies_from_examples()
    assert len(strategies) == 1
    assert strategies[0].example() == operation.Case(
        media_type=media_type,
        _meta=CaseMetadata(
            generation=GenerationInfo(time=0.0, mode=GenerationMode.POSITIVE),
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
        ),
        **{container: expected},
    )


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_no_body_in_get(ctx):
    operation = ctx.openapi.load_operation(
        "GET /api/success",
        {
            "parameters": [{"required": True, "in": "query", "type": "string", "name": "key", "x-example": "John"}],
            "responses": {"200": {"description": "OK"}},
        },
        version="2.0",
    )
    strategies = operation.get_strategies_from_examples()
    assert len(strategies) == 1
    assert strategies[0].example().body is NOT_SET


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_custom_strategies(ctx):
    schemathesis.openapi.format("even_4_digits", st.from_regex(r"\A[0-9]{4}\Z").filter(lambda x: int(x) % 2 == 0))
    operation = ctx.openapi.load_operation(
        "POST /users",
        {
            "parameters": [
                {"name": "id", "in": "query", "required": True, "type": "string", "format": "even_4_digits"}
            ],
            "responses": {"200": {"description": "OK"}},
        },
        version="2.0",
    )
    result = operation.as_strategy().example()
    assert len(result.query["id"]) == 4
    assert int(result.query["id"]) % 2 == 0


def test_default_strategies_binary(ctx):
    operation = ctx.openapi.load_operation(
        "POST /users",
        {
            "consumes": ["multipart/form-data"],
            "parameters": [{"name": "upfile", "in": "formData", "type": "file", "required": True}],
            "responses": {"200": {"description": "OK"}},
        },
        version="2.0",
    )
    case = examples.generate_one(operation.as_strategy())
    assert isinstance(case.body["upfile"], Binary)
    kwargs = case.as_transport_kwargs(base_url="http://127.0.0.1")
    assert kwargs["files"] == [("upfile", ("upfile.png", case.body["upfile"].data))]


def test_merge_length_into_pattern(ctx):
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "string",
                            # Unlikely to generate a string of this length from a pattern
                            "minLength": 460,
                            "maxLength": 465,
                            "pattern": "^[a-z]+$",
                        },
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    @run_given(operation.as_strategy(), max_examples=1)
    def test(case):
        pass


def test_end_anchored_patterns_do_not_exhaust_generation(ctx):
    identifier = {
        "type": "string",
        "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$",
        "maxLength": 36,
    }
    operation = ctx.openapi.load_operation(
        "POST /d",
        {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Bulk"}}},
            },
            "responses": {"200": {"description": "OK"}},
        },
        components={
            "schemas": {
                "Id": identifier,
                "Item": {
                    "type": "object",
                    "required": ["a", "b", "c", "d"],
                    "additionalProperties": False,
                    "properties": {name: {"$ref": "#/components/schemas/Id"} for name in "abcd"},
                },
                "Bulk": {
                    "type": "object",
                    "required": ["data"],
                    "additionalProperties": False,
                    "properties": {
                        "data": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"$ref": "#/components/schemas/Item"},
                        }
                    },
                },
            }
        },
    )

    # Four end-anchored fields per item: any draw wasted on a trailing newline exhausts the filter budget.
    @seed(14)
    @settings(max_examples=15, deadline=None, database=None, phases=[Phase.generate])
    @given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE))
    def test(case):
        pass

    test()


@pytest.mark.parametrize(
    "version",
    ["4", "7"],
    ids=["v4", "v7"],
)
def test_pattern_stricter_than_format_does_not_exhaust_generation(ctx, version):
    identifier = {
        "type": "string",
        "format": "uuid",
        "pattern": f"^([0-9a-fA-F]{{8}}-[0-9a-fA-F]{{4}}-{version}[0-9a-fA-F]{{3}}-[89abAB][0-9a-fA-F]{{3}}-[0-9a-fA-F]{{12}})$",
    }
    reference = {"$ref": "#/components/schemas/Id"}
    operation = ctx.openapi.load_operation(
        "POST /d",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["a", "b", "items", "ids"],
                            "additionalProperties": False,
                            "properties": {
                                "a": reference,
                                "b": reference,
                                "items": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {
                                        "type": "object",
                                        "required": ["c", "d"],
                                        "additionalProperties": False,
                                        "properties": {"c": reference, "d": reference},
                                    },
                                },
                                "ids": {"type": "array", "minItems": 1, "items": reference},
                            },
                        }
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
        components={"schemas": {"Id": identifier}},
    )

    # The format generator draws few values this pattern admits, so the pattern has to drive generation too.
    @seed(0)
    @settings(max_examples=15, deadline=None, database=None, phases=[Phase.generate])
    @given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE))
    def test(case):
        pass

    test()


def test_required_without_properties(ctx):
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "additionalProperties": False,
                            "type": "object",
                            "required": ["A"],
                        },
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    @run_given(operation.as_strategy(), max_examples=1)
    def test(case):
        pass


def test_non_schema_property_value(ctx):
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "maxItems": 0,
                            },
                        },
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    @run_given(operation.as_strategy(), max_examples=1)
    def test(case):
        pass


def test_as_strategy_example_resolves_bundled_refs(tmp_path):
    # The public Python API path must work without prior CLI/pytest imports.
    script = tmp_path / "probe.py"
    script.write_text(
        textwrap.dedent("""
        import schemathesis

        schema = {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1.0.0"},
            "paths": {
                "/probe": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "outer": {
                                                "$ref": "#/components/schemas/Outer",
                                                "properties": {
                                                    "inner": {"$ref": "#/components/schemas/Inner"}
                                                },
                                            }
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
            "components": {
                "schemas": {
                    "Outer": {
                        "type": "object",
                        "properties": {"enabled": {"type": "boolean"}},
                    },
                    "Inner": {"type": "object", "properties": {"flag": {"type": "boolean"}}},
                }
            },
        }
        api = schemathesis.openapi.from_dict(schema)
        case = api["/probe"]["POST"].as_strategy().example()
        assert case is not None
    """)
    )
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_malformed_annotation_behind_a_reference_still_generates(ctx):
    # An annotation cannot change which values the schema admits, and canonicalization drops it.
    operation = ctx.openapi.load_operation(
        "POST /probe",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"field": {"$ref": "#/components/schemas/Annotated"}},
                            "required": ["field"],
                        },
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
        components={"schemas": {"Annotated": {"type": "string", "description": None}}},
    )

    case = examples.generate_one(operation.as_strategy())

    assert isinstance(case.body["field"], str)


@pytest.mark.parametrize("version", ["3.0.0", "3.1.0"])
def test_array_with_allof_of_multiple_contains(ctx, version):
    # Filtering for several `contains` demands exhausts before both consts land, so each is placed:
    # by the `contains` strategy under 3.1, by the converter's positional prefix under Draft 4.
    operation = ctx.openapi.load_operation(
        "POST /probe",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["type"],
                            "properties": {
                                "type": {
                                    "type": "array",
                                    "minItems": 1,
                                    "uniqueItems": True,
                                    "items": {"type": "string"},
                                    "allOf": [
                                        {"contains": {"const": "VerifiablePresentation"}},
                                        {"contains": {"const": "KyaManifest"}},
                                    ],
                                }
                            },
                        }
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
        version=version,
    )

    case = examples.generate_one(operation.as_strategy())

    assert "VerifiablePresentation" in case.body["type"]
    assert "KyaManifest" in case.body["type"]


def test_string_with_allof_of_formats_and_patterns(ctx):
    # Every generated value has to satisfy both halves of each conjunction at once.
    operation = ctx.openapi.load_operation(
        "POST /probe",
        {
            "parameters": [
                {
                    "in": "query",
                    "name": "host",
                    "required": True,
                    "schema": {
                        "allOf": [
                            {"type": "string", "format": "hostname"},
                            {"type": "string", "format": "ipv4"},
                        ]
                    },
                }
            ],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"type": "string", "allOf": [{"pattern": "^[a-z]+$"}, {"pattern": "a"}]}
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    case = examples.generate_one(operation.as_strategy())

    assert jsonschema_rs.Draft202012Validator({"type": "string", "format": "ipv4"}, validate_formats=True).is_valid(
        case.query["host"]
    )
    assert re.fullmatch(r"[a-z]+", case.body) and "a" in case.body


@pytest.mark.parametrize("media_type", ["text/yaml"])
def test_binary_is_serializable(ctx, media_type):
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "required": True,
                "content": {media_type: {"schema": {"type": "string", "format": "binary"}}},
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    @run_given(operation.as_strategy(), max_examples=1)
    def test(case):
        assert_requests_call(case)
        assert case.as_transport_kwargs()["data"] == case.body.data


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_default_strategies_bytes(ctx):
    operation = ctx.openapi.load_operation(
        "POST /users",
        {
            "consumes": ["text/plain"],
            "parameters": [
                {"in": "body", "name": "byte", "required": True, "schema": {"type": "string", "format": "byte"}}
            ],
            "responses": {"200": {"description": "OK"}},
        },
        version="2.0",
    )
    result = operation.as_strategy().example()
    assert isinstance(result.body, str)
    b64decode(result.body)


@pytest.mark.parametrize(
    ("values", "error"),
    [
        (("valid", "invalid"), f"strategy must be of type {st.SearchStrategy}, not {str}"),
        ((123, st.from_regex(r"\d")), f"name must be of type {str}, not {int}"),
    ],
)
def test_invalid_custom_strategy(values, error):
    with pytest.raises(TypeError) as exc:
        schemathesis.openapi.format(*values)
    assert error in str(exc.value)


def make_swagger(*parameters):
    return {
        "swagger": "2.0",
        "info": {"title": "Sample API", "description": "API description in Markdown.", "version": "1.0.0"},
        "host": "api.example.com",
        "basePath": "/v1",
        "schemes": ["https"],
        "paths": {
            "/form": {
                "post": {
                    "parameters": list(parameters),
                    "summary": "Returns a list of users.",
                    "description": "Optional extended description in Markdown.",
                    "consumes": ["multipart/form-data"],
                    "produces": ["application/json"],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


@pytest.mark.parametrize(
    "raw_schema",
    [
        make_swagger(
            {"name": "a", "in": "formData", "required": True, "type": "number"},
            {"name": "b", "in": "formData", "required": True, "type": "boolean"},
            {"name": "c", "in": "formData", "required": True, "type": "array"},
        ),
        make_swagger({"name": "c", "in": "formData", "required": True, "type": "array"}),
        {
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "servers": [{"url": "http://127.0.0.1:8081/{basePath}", "variables": {"basePath": {"default": "api"}}}],
            "paths": {
                "/form": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "multipart/form-data": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "a": {"type": "number"},
                                            "b": {"type": "boolean"},
                                            "c": {"type": "array"},
                                        },
                                        "required": ["a", "b", "c"],
                                    },
                                }
                            }
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
    ],
)
@pytest.mark.hypothesis_nested
def test_valid_form_data(ctx, raw_schema):
    api = ctx.openapi.apps.success()
    # When the request definition contains a schema, matching values of which cannot be encoded to multipart
    # straightforwardly
    schema = ctx.openapi.from_full_schema(raw_schema)
    schema.config.update(base_url=f"{api.base_url}/api")

    @run_given(case=schema["/form"]["POST"].as_strategy(), max_examples=10)
    def inner(case):
        case.call()

    # Then these values should be cast to bytes and handled successfully


@pytest.mark.hypothesis_nested
def test_optional_form_data(ctx):
    api = ctx.openapi.apps.success()
    schema = ctx.openapi.load_schema(
        {
            "/form": {
                "post": {
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "string",
                                },
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    # When the multipart form is optional
    # Note, this test is similar to the one above, but has a simplified schema & conditions
    # It is done mostly due to performance reasons
    schema.config.update(base_url=f"{api.base_url}/api")

    @run_given(case=schema["/form"]["POST"].as_strategy(), max_examples=1)
    def inner(case):
        assume(case.body is NOT_SET)
        case.call()

    # Then payload can be absent


def test_path_parameter_space_encoded_in_url(ctx):
    # GH-4252: a space in a path segment must reach the wire as "%20", never "+"
    schema = ctx.openapi.load_schema(
        {"/forecast/{level}": {"get": {"responses": {"200": {"description": "OK"}}}}},
        version="3.1.0",
    )
    operation = schema["/forecast/{level}"]["GET"]
    case = operation.Case(path_parameters=quote_all({"level": "2 m above gnd"}))
    assert schema.build_request_url(case, "http://127.0.0.1") == "http://127.0.0.1/forecast/2%20m%20above%20gnd"


def test_percent_encoded_base_url_reserved_char_preserved(ctx):
    # A base URL with a percent-encoded reserved character in its path (e.g. an Azure-style
    # basePath that embeds a literal "?" as "%3F") must reach the wire with that encoding
    # intact; decoding it back to "?" turns a static path segment into a query delimiter.
    schema = ctx.openapi.load_schema(
        {"/current-Movie-Data.csv&imdb_id={IMDBid}": {"get": {"responses": {"200": {"description": "OK"}}}}},
        version="2.0",
    )
    operation = schema["/current-Movie-Data.csv&imdb_id={IMDBid}"]["GET"]
    case = operation.Case(path_parameters={"IMDBid": "0"})
    url = schema.build_request_url(case, "http://localhost/api-v2/%3Fsource=http:/hydramovies.com/api-v2")
    assert url == "http://localhost/api-v2/%3Fsource=http:/hydramovies.com/api-v2/current-Movie-Data.csv&imdb_id=0"


@pytest.mark.parametrize("expected", ["null", "true", "false"])
def test_parameters_jsonified(ctx, expected):
    # See GH-1166
    # When `None` or `True` / `False` are generated in path or query
    operation = ctx.openapi.load_operation(
        "GET /foo/{param_path}",
        {
            "parameters": [
                {
                    "name": f"param_{location}",
                    "in": location,
                    "required": True,
                    "schema": {"type": "boolean", "nullable": True},
                }
                for location in ("path", "query")
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    strategy = operation.as_strategy()

    @run_given(case=strategy, max_examples=1)
    def test(case):
        # Then they should be converted to their JSON equivalents
        assume(case.path_parameters["param_path"] == expected)
        assume(case.query["param_query"] == expected)


@pytest.mark.parametrize("version", ["2.0", "3.0.2"])
def test_optional_payload(ctx, version):
    # When body are not required
    paths = {"/users": {"post": {"responses": {"200": {"description": "OK"}}}}}
    if version == "2.0":
        paths["/users"]["post"]["parameters"] = [{"in": "body", "name": "body", "schema": {"type": "string"}}]
    else:
        paths["/users"]["post"]["requestBody"] = {"content": {"application/json": {"schema": {"type": "string"}}}}
    schema = ctx.openapi.load_schema(paths, version=version)
    strategy = schema["/users"]["post"].as_strategy()
    # Then `None` could be generated by Schemathesis
    assert find_first(strategy, lambda x: x.body is NOT_SET, max_examples=2000).body is NOT_SET


@given(data=st.data())
@settings(deadline=None)
def test_date_format(data):
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/data": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "format": "date",
                                    "type": "string",
                                },
                            }
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    schema = schemathesis.openapi.from_dict(raw_schema)
    strategy = schema["/data"]["POST"].as_strategy()
    case = data.draw(strategy)
    datetime.datetime.strptime(case.body, "%Y-%m-%d")


# Inside the generation buffer the floor is reached by repeating one element; past the buffer no example
# exists at all, which is a schema error.
@pytest.mark.parametrize("min_items", [20000, 100000], ids=["drawable", "schema-error"])
def test_large_minimum_array_size(ctx, cli, snapshot_cli, min_items):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "array", "items": {"type": "integer"}, "minItems": min_items}
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )
    assert (
        cli.run(
            str(schema_path), "--max-examples=1", f"--url={api.base_url}/api", "--phases=fuzzing", "--mode=positive"
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    ("discriminator", "valid_values"),
    [
        ({"propertyName": "petType"}, {"Cat", "Dog"}),
        (
            {
                "propertyName": "petType",
                "mapping": {"feline": "#/components/schemas/Cat", "canine": "#/components/schemas/Dog"},
            },
            {"feline", "canine", "Cat", "Dog"},
        ),
    ],
    ids=["implicit", "explicit"],
)
def test_discriminator_property_pinned_in_generation(ctx, discriminator, valid_values):
    operation = ctx.openapi.load_operation(
        "POST /pets",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/Cat"},
                                {"$ref": "#/components/schemas/Dog"},
                            ],
                            "discriminator": discriminator,
                        }
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
        components={
            "schemas": {
                "Cat": {"type": "object", "properties": {"petType": {"type": "string"}}, "required": ["petType"]},
                "Dog": {"type": "object", "properties": {"petType": {"type": "string"}}, "required": ["petType"]},
            }
        },
    )

    @run_given(case=operation.as_strategy(), max_examples=10, phases=[Phase.generate])
    def inner(case):
        assert case.body["petType"] in valid_values


def test_hypothesis_observability_serialization(ctx):
    # Hypothesis observability serializes all dataclass fields on generated values
    operation = ctx.openapi.load_operation("GET /test", {"responses": {"200": {"description": "OK"}}})

    @given(case=operation.as_strategy())
    @settings(max_examples=1, database=None, phases=[Phase.generate])
    def test(case):
        pass

    with with_observability_callback(lambda _: None):
        test()


@pytest.mark.parametrize("location", [ParameterLocation.QUERY, ParameterLocation.HEADER])
def test_apply_exclusions_drops_empty_required_when_all_filtered(location):
    # Empty `required` violates the OpenAPI meta-schema and crashes Hypothesis draws.
    pset = OpenApiParameterSet(location, items=[], adapter=v2)
    pset._schema = {
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
        "additionalProperties": False,
    }
    out = pset.get_schema_with_exclusions(exclude=["key"])
    assert "required" not in out


def test_apply_exclusions_reachable_via_auth_supplied_required_header():
    pset = OpenApiParameterSet(
        ParameterLocation.HEADER,
        [
            OpenApiParameter.from_definition(
                definition={
                    "in": "header",
                    "name": "Authorization",
                    "required": True,
                    "type": "string",
                },
                name_to_uri={},
                adapter=v2,
            )
        ],
        adapter=v2,
    )
    out = pset.get_schema_with_exclusions(exclude=["Authorization"])
    assert "required" not in out
    assert out["properties"] == {}


# HTTP header names are case-insensitive; other locations match names exactly.
def test_exclusions_ignore_case_only_for_headers(ctx):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {"name": "X-Token", "in": "header", "required": True, "schema": {"type": "string"}},
                {"name": "Token", "in": "query", "required": True, "schema": {"type": "string"}},
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    assert [
        (schema.get("required"), list(schema["properties"]))
        for schema in (
            operation.headers.get_schema_with_exclusions(["x-token"]),
            operation.query.get_schema_with_exclusions(["token"]),
        )
    ] == [(None, []), (["Token"], ["Token"])]


@pytest.mark.parametrize(
    ("format", "minimum", "maximum"),
    [("int32", -(2**31), 2**31 - 1), ("int64", -(2**63), 2**63 - 1)],
)
def test_positive_integers_stay_within_format_range(ctx, format, minimum, maximum):
    # `format` alone declares the range; without it generation draws arbitrary-precision ints
    # that a fixed-width-int server rejects, turning a positive case into a false failure.
    operation = ctx.openapi.load_operation(
        "POST /x",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"value": {"type": "integer", "format": format}},
                            "required": ["value"],
                        }
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    # The violating draw is rare, so fewer examples would let a regression pass.
    @run_given(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=250)
    def test(case):
        assert minimum <= case.body["value"] <= maximum, f"Out of {format} range: {case.body['value']}"


def test_integer_multiple_of_body(ctx):
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"type": "integer", "multipleOf": 3, "minimum": 1}}},
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    @run_given(operation.as_strategy(), max_examples=10)
    def test(case):
        assert case.body >= 1 and case.body % 3 == 0, case.body


@pytest.mark.parametrize("version", ["3.0.2", "3.1.0"])
@pytest.mark.parametrize(
    ("body_schema", "extract"),
    [
        ({"type": "string", "format": "uuid"}, lambda body: body),
        (
            {"type": "object", "properties": {"id": {"type": "string", "format": "uuid"}}, "required": ["id"]},
            lambda body: body["id"],
        ),
    ],
    ids=["bare", "property"],
)
def test_format_constrained_string_body(ctx, version, body_schema, extract):
    # Draft 2020-12 reads `format` as an annotation, which must not turn `uuid` into arbitrary text.
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": body_schema}},
            },
            "responses": {"200": {"description": "OK"}},
        },
        version=version,
    )

    @run_given(operation.as_strategy(), max_examples=10)
    def test(case):
        uuid.UUID(extract(case.body))


@pytest.mark.parametrize(
    "value",
    [datetime.date(2020, 1, 1), datetime.time(1, 2, 3), datetime.datetime(2020, 1, 1, 2, 3, 4)],
    ids=["date", "time", "datetime"],
)
@pytest.mark.parametrize("count", [{"type": "number"}, {"type": "number", "format": "float"}], ids=["plain", "float"])
def test_schema_with_a_non_json_value_is_reported(ctx, value, count):
    # Python dicts can hold values the Rust engine cannot serialize into a JSON document.
    operation = ctx.openapi.load_operation(
        "POST /items",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "count": count,
                                "when": {"type": "string", "default": value},
                            },
                        }
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )
    with pytest.raises(InvalidSchema, match="Unsupported type"):
        generate_many(operation.as_strategy(), 1)


def test_recursion_next_to_a_schema_that_declines_canonicalization(ctx):
    # `oneOf` routes the body to the other engine, which cannot follow the pointer back to itself.
    operation = ctx.openapi.load_operation(
        "POST /orders",
        {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Order"}}},
            },
            "responses": {"200": {"description": "OK"}},
        },
        components={
            "schemas": {
                "Order": {
                    "type": "object",
                    "required": ["Items", "Restaurant"],
                    "properties": {
                        "Restaurant": {"$ref": "#/components/schemas/Restaurant"},
                        "Items": {"type": "array", "items": {"$ref": "#/components/schemas/Item"}},
                    },
                },
                "Restaurant": {
                    "oneOf": [
                        {"type": "object", "required": ["Id"], "properties": {"Id": {"type": "integer"}}},
                        {"type": "object", "required": ["Reference"], "properties": {"Reference": {"type": "string"}}},
                    ]
                },
                "Item": {
                    "type": "object",
                    "properties": {
                        "Name": {"type": "string"},
                        "Items": {"type": "array", "items": {"$ref": "#/components/schemas/Item"}},
                    },
                },
            }
        },
    )
    is_valid = jsonschema_rs.Draft202012Validator(operation.body[0].validation_schema).is_valid

    @run_given(operation.as_strategy(), max_examples=10)
    def test(case):
        assert is_valid(case.body), case.body


def test_header_pattern_needing_only_excluded_characters_is_unsatisfiable(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "get": {
                    "parameters": [
                        {
                            "name": "X-Token",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "pattern": "^\\w$"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema.config.generation.update(
        codec="latin-1",
        exclude_header_characters="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_",
    )
    strategy = schema["/data"]["GET"].as_strategy(generation_mode=GenerationMode.POSITIVE)

    with pytest.raises(Unsatisfiable):
        find_first(strategy, lambda _: True, max_examples=10)


@pytest.mark.parametrize(
    "body_schema",
    [
        {
            "type": "number",
            "multipleOf": 0.1,
            "exclusiveMinimum": 1e20,
            "maximum": 10**20 + 100_000,
        },
        {
            "anyOf": [
                {
                    "type": "number",
                    "multipleOf": 0.1,
                    "exclusiveMinimum": 1e20,
                    "maximum": 10**20 + 100_000,
                },
                {"const": "valid"},
            ]
        },
        # The bound folds onto a grid point no float holds, which the schema reads as its neighbour.
        {
            "type": "number",
            "multipleOf": 1e-20,
            "exclusiveMinimum": 10**25,
            "maximum": 1e308,
        },
    ],
    ids=["number", "number-branch", "number-bound-off-the-float-grid"],
)
def test_canonical_number_generation_respects_original_schema(ctx, body_schema):
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": body_schema,
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )
    is_valid = jsonschema_rs.Draft202012Validator(body_schema).is_valid

    @run_given(operation.as_strategy(), max_examples=10)
    def test(case):
        assert is_valid(case.body)


@pytest.mark.parametrize("version", ["3.0.2", "3.1.0"])
def test_positive_generation_respects_not_with_unicode_property_pattern(ctx, version):
    body_schema = {"type": "string", "not": {"pattern": "[\\p{L}]"}}
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": body_schema}},
            },
            "responses": {"200": {"description": "OK"}},
        },
        version=version,
    )
    is_valid = jsonschema_rs.validator_for(body_schema).is_valid

    @run_given(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=10)
    def test(case):
        assert is_valid(case.body), case.body


def test_cookie_value_barred_from_every_printable_character_is_empty(ctx):
    operation = ctx.openapi.load_operation(
        "GET /data",
        {
            "parameters": [
                {
                    "name": "session",
                    "in": "cookie",
                    "required": True,
                    "schema": {"type": "string", "not": {"pattern": "[ -~]"}},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    @run_given(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=10)
    def test(case):
        assert case.cookies == {"session": ""}


@pytest.mark.parametrize("version", ["3.0.2", "3.1.0"])
def test_canonical_object_property_formats(ctx, version):
    body_schema = {
        "type": "object",
        "properties": {"id": {"type": "string", "format": "uuid"}, "seen": {"type": "string", "format": "date-time"}},
        "required": ["id", "seen"],
    }
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {"required": True, "content": {"application/json": {"schema": body_schema}}},
            "responses": {"200": {"description": "OK"}},
        },
        version=version,
    )
    is_valid = jsonschema_rs.Draft202012Validator(body_schema, validate_formats=True).is_valid

    @run_given(operation.as_strategy(), max_examples=10)
    def test(case):
        assert is_valid(case.body), case.body


def test_canonical_contains_demand_behind_a_branch_pointer(ctx):
    # A demand one branch reaches through a pointer still has to draw from what the pointer names.
    big = {"type": "integer", "minimum": 5}
    operation = ctx.openapi.load_operation(
        "POST /data",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "array",
                            "items": {"type": ["integer", "string"]},
                            "contains": {"anyOf": [{"$ref": "#/components/schemas/Big"}, {"type": "string"}]},
                            "minItems": 1,
                        }
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
        components={"schemas": {"Big": big}},
    )
    is_valid = jsonschema_rs.Draft202012Validator(
        {
            "type": "array",
            "items": {"type": ["integer", "string"]},
            "contains": {"anyOf": [big, {"type": "string"}]},
            "minItems": 1,
        }
    ).is_valid

    @run_given(operation.as_strategy(), max_examples=10)
    def test(case):
        assert is_valid(case.body), case.body


BOUNDED_INTEGER = {"type": "integer", "minimum": 1, "maximum": 100}


def _positive_values(schema, location, media_type, validator_cls, count=25):
    # Both spellings need enough draws to show up.
    return generate_many(
        make_positive_strategy(schema, "test", location, media_type, GenerationConfig(), validator_cls), count
    )


def test_integer_json_body_drawn_as_whole_float():
    # From draft 6 on a zero fractional part is an integer, so a server decoding `2.0` as a float
    # alone rejects a value its own schema admits.
    values = _positive_values(
        BOUNDED_INTEGER, ParameterLocation.BODY, "application/json", jsonschema_rs.Draft202012Validator
    )

    assert any(isinstance(value, float) for value in values)
    is_valid = jsonschema_rs.Draft202012Validator(BOUNDED_INTEGER).is_valid
    assert all(is_valid(value) for value in values), values


def test_integer_json_body_stays_whole_on_draft_4():
    # Draft 4 reads a fractional spelling as a number, not an integer.
    values = _positive_values(
        BOUNDED_INTEGER, ParameterLocation.BODY, "application/json", jsonschema_rs.Draft4Validator
    )

    assert all(isinstance(value, int) for value in values), values


@pytest.mark.parametrize(
    ("location", "media_type"),
    [
        (ParameterLocation.QUERY, None),
        (ParameterLocation.PATH, None),
        (ParameterLocation.HEADER, None),
        (ParameterLocation.BODY, "application/xml"),
        (ParameterLocation.BODY, "application/x-www-form-urlencoded"),
        (ParameterLocation.BODY, "application.json"),
    ],
    ids=["query", "path", "header", "xml-body", "form-body", "malformed-media-type"],
)
def test_integer_stays_whole_outside_a_json_body(location, media_type):
    # Everywhere else the value goes out as text, where `2.0` is a different string than `2`.
    values = _positive_values(BOUNDED_INTEGER, location, media_type, jsonschema_rs.Draft202012Validator)

    assert all(isinstance(value, int) for value in values), values


def test_whole_float_never_misrepresents_a_large_integer():
    # Past 2**53 no float spells the integer back, and the shifted value can leave the bounds.
    schema = {"type": "integer", "minimum": 2**53 + 1, "maximum": 2**53 + 1000}
    is_valid = jsonschema_rs.Draft202012Validator(schema).is_valid

    values = _positive_values(schema, ParameterLocation.BODY, "application/json", jsonschema_rs.Draft202012Validator)

    assert all(is_valid(value) for value in values), values


def _body_operation(ctx, body_schema):
    operation = ctx.openapi.load_operation(
        "POST /items",
        {
            "requestBody": {"required": True, "content": {"application/json": {"schema": body_schema}}},
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )
    return operation


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    "body_schema",
    [
        {"type": "object", "patternProperties": {"(?<!a+)b": {"type": "integer"}}},
        {
            "type": "object",
            "additionalProperties": {"type": "integer"},
            "not": {"additionalProperties": {"type": "integer", "minimum": 0}},
        },
        {"type": "number", "format": "float", "minimum": 1},
    ],
    ids=["pattern-property-python-cannot-read", "barred-additional-properties", "float-format-inclusive-bound"],
)
def test_body_draws_satisfy_schema(ctx, body_schema):
    operation = _body_operation(ctx, body_schema)
    validator = jsonschema_rs.validator_for(body_schema, pattern_options=FANCY_REGEX_OPTIONS)

    @run_given(case=operation.as_strategy(), max_examples=10)
    def check(case):
        assert validator.is_valid(case.body), case.body


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    "body_schema",
    [
        {"type": "object", "minProperties": 100000},
        {
            "type": "number",
            "exclusiveMinimum": 9007199254740992,
            "exclusiveMaximum": 9007199254740994,
            "not": {"multipleOf": 0.5},
        },
    ],
    ids=["object-wider-than-a-draw", "only-integer-in-range-is-barred"],
)
def test_body_nothing_can_be_drawn_for(ctx, body_schema):
    operation = _body_operation(ctx, body_schema)

    @given(case=operation.as_strategy())
    @settings(max_examples=10, database=None, suppress_health_check=list(HealthCheck))
    def check(case):
        pass

    with pytest.raises(Unsatisfiable):
        check()


def _query_operation(ctx, parameter_schema, config):
    return ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [{"name": "q", "in": "query", "required": True, "schema": parameter_schema}],
            "responses": {"200": {"description": "OK"}},
        },
        config=SchemathesisConfig.from_dict(config),
    )


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    ("parameter_schema", "config", "expected"),
    [
        (
            {"type": "string"},
            {
                "dictionaries": {"tokens": {"values": ["DICT"]}},
                "operations": [{"include-name": "GET /items", "parameters": {"query.q": {"dictionary": "tokens"}}}],
            },
            {"DICT"},
        ),
        (
            {"enum": ["a", "b"]},
            {
                "dictionaries": {"tokens": {"values": ["DICT"]}},
                "generation": {"dictionaries": {"string": {"dictionary": "tokens", "probability": 1.0}}},
            },
            {"a", "b"},
        ),
    ],
    ids=["operation-scoped-binding", "type-wide-binding-skips-untyped-parameter"],
)
def test_query_dictionary_binding(ctx, parameter_schema, config, expected):
    operation = _query_operation(ctx, parameter_schema, config)
    values = {case.query["q"] for case in generate_many(operation.as_strategy(), 10)}
    assert values == expected


@pytest.mark.hypothesis_nested
def test_body_dictionary_binding_through_recursive_reference(ctx):
    node = {
        "type": "object",
        "properties": {"token": {"type": "string"}, "child": {"$ref": "#/components/schemas/A"}},
        "required": ["token"],
    }
    config = SchemathesisConfig.from_dict(
        {"dictionaries": {"tokens": {"values": ["DICT"]}}, "parameters": {"body.child.token": {"dictionary": "tokens"}}}
    )
    operation = ctx.openapi.load_operation(
        "POST /items",
        {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/A"}}},
            },
            "responses": {"200": {"description": "OK"}},
        },
        components={"schemas": {"A": node}},
        config=config,
    )
    tokens = []

    @run_given(case=operation.as_strategy(), max_examples=10, derandomize=True)
    def collect(case):
        child = case.body.get("child") if isinstance(case.body, dict) else None
        if isinstance(child, dict) and "token" in child:
            tokens.append(child["token"])

    assert tokens
    assert set(tokens) == {"DICT"}


@pytest.mark.hypothesis_nested
def test_body_dictionary_binding_on_unknown_item_field_is_ignored(ctx):
    body_schema = {
        "type": "array",
        "items": {"type": "object", "properties": {"x": {"type": "string"}}, "additionalProperties": False},
    }
    config = SchemathesisConfig.from_dict(
        {"dictionaries": {"tokens": {"values": ["DICT"]}}, "parameters": {"body.[*].missing": {"dictionary": "tokens"}}}
    )
    operation = ctx.openapi.load_operation(
        "POST /items",
        {
            "requestBody": {"required": True, "content": {"application/json": {"schema": body_schema}}},
            "responses": {"200": {"description": "OK"}},
        },
        config=config,
    )
    validator = jsonschema_rs.validator_for(body_schema)

    @run_given(case=operation.as_strategy(), max_examples=10)
    def check(case):
        assert validator.is_valid(case.body), case.body
