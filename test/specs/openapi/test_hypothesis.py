import uuid
import warnings
from datetime import date
from pathlib import Path

import jsonschema_rs
import pytest
from flask import jsonify, request
from hypothesis import HealthCheck, Phase, assume, find, given, settings
from hypothesis import strategies as st
from hypothesis.errors import FailedHealthCheck, NoSuchExample, Unsatisfiable
from jsonschema_rs import Draft4Validator

import schemathesis
from schemathesis.config import GenerationConfig
from schemathesis.core.jsonschema.resolver import load_file
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.examples import generate_many
from schemathesis.openapi.generation.filters import is_valid_header
from schemathesis.specs.openapi import _hypothesis, formats
from schemathesis.specs.openapi._hypothesis import make_positive_strategy
from schemathesis.specs.openapi.diagnostics import find_slow_parameter, find_unsatisfiable_parameter
from test.utils import assert_requests_call, run_given, to_float32


@pytest.fixture
def operation(ctx, make_openapi_3_schema):
    raw_schema = make_openapi_3_schema(
        body={
            "required": True,
            "content": {"application/json": {"schema": {"type": "string"}}},
        },
        parameters=[
            {"in": "path", "name": "p1", "required": True, "schema": {"type": "string", "enum": ["FOO"]}},
            {"in": "header", "name": "h1", "required": True, "schema": {"type": "string", "enum": ["FOO"]}},
            {"in": "cookie", "name": "c1", "required": True, "schema": {"type": "string", "enum": ["FOO"]}},
            {"in": "query", "name": "q1", "required": True, "schema": {"type": "string", "enum": ["FOO"]}},
        ],
    )
    schema = ctx.openapi.load_schema(raw_schema["paths"])
    return schema["/users"]["POST"]


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


def test_optional_false_query_parameter_is_sent(ctx):
    operation = ctx.openapi.load_operation(
        "GET /data",
        {
            "parameters": [{"in": "query", "name": "flag", "required": False, "schema": {"enum": [False]}}],
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    seen = []

    @run_given(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=10)
    def test(case):
        if case.query not in seen:
            seen.append(case.query)

    assert sorted(seen, key=len) == [{}, {"flag": "false"}]


def test_null_inside_query_array_is_sent(ctx):
    operation = ctx.openapi.load_operation(
        "GET /data",
        {
            "parameters": [
                {
                    "in": "query",
                    "name": "ids",
                    "required": False,
                    "schema": {"type": "array", "items": {"type": "null"}, "minItems": 1, "maxItems": 1},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    seen = []

    @run_given(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=10)
    def test(case):
        if case.query not in seen:
            seen.append(case.query)

    assert sorted(seen, key=len) == [{}, {"ids": ["null"]}]


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

    assert find(strategy, lambda case: case.body is None).body is None
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


def test_as_strategy_keeps_passed_headers_unchanged(ctx):
    schema = ctx.openapi.load_schema({"/x": {"get": {"responses": {"200": {"description": "OK"}}}}})
    schema.config.update(headers={"X-Configured": "global"})
    headers = {"X-Caller": "local"}
    strategy = schema["/x"]["GET"].as_strategy(headers=headers)
    assert headers == {"X-Caller": "local"}

    @run_given(strategy, max_examples=1)
    def test(case):
        assert case.headers == {"X-Caller": "local", "X-Configured": "global"}


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
        find(
            formats.header_values(exclude_characters=formats.INVALID_HEADER_CHARS),
            lambda value: value[:1].strip() != value[:1],
            # The excluded value is rare, so a small budget would pass without looking.
            settings=settings(max_examples=1000, database=None),
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
        find(
            operation.as_strategy(generation_mode=GenerationMode.POSITIVE),
            lambda case: case.headers["X-A"][-1:].strip() != case.headers["X-A"][-1:],
            # The excluded value is rare, so a small budget would pass without looking.
            settings=settings(max_examples=1000, database=None),
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


@pytest.fixture
def clear_caches():
    yield
    load_file.cache_clear()


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


@pytest.mark.usefixtures("clear_caches")
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
    assert find(strategy, lambda case: case.query["q"] == "special-ref", settings=settings(database=None)).query == {
        "q": "special-ref"
    }


@pytest.mark.hypothesis_nested
def test_path_example_without_slash_does_not_allow_encoded_slash(ctx):
    operation = ctx.openapi.load_operation(
        "GET /blocks/{block}",
        {
            "parameters": [
                {
                    "name": "block",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "pattern": "^[0-9]+/[0-9]+$"},
                    "example": "foo",
                }
            ],
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
def test_path_parameters_encoded_braces_are_filtered(ctx):
    operation = ctx.openapi.load_operation(
        "GET /blocks/{block}",
        {
            "parameters": [
                {
                    "name": "block",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "pattern": "^[{}]$"},
                }
            ],
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
    "schema",
    [
        {"type": "number", "format": "float", "exclusiveMinimum": 10**1000},
        {"type": "number", "format": "float", "exclusiveMaximum": -(10**1000)},
    ],
    ids=["minimum", "maximum"],
)
def test_float_format_snapping_unsatisfiable_bound(schema):
    # No finite float32 lies past the bound, so the node must become unsatisfiable, not gain an infinite bound.
    _hypothesis.snap_float32_bounds(schema)
    assert schema == {"not": {}}


@pytest.mark.parametrize(
    ("schema", "valid", "invalid"),
    [
        ({"type": ["number", "null"], "format": "float", "exclusiveMinimum": 10**1000}, [None], [5.0]),
        ({"format": "float", "exclusiveMinimum": 10**1000}, ["text"], [5.0]),
        ({"format": "float", "exclusiveMinimum": 10**1000, "enum": ["ok"]}, ["ok"], [None, 5.0]),
    ],
    ids=["union", "typeless", "typeless-enum"],
)
def test_float_format_unsatisfiable_bound_keeps_non_numeric(schema, valid, invalid):
    # Forbidding the empty numeric branch keeps non-numeric values and sibling constraints (e.g. `enum`) valid.
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


def test_slow_parameter_probe_survives_a_parameter_that_cannot_build(ctx):
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

    found = find_slow_parameter(operation, HealthCheck.too_slow)

    assert found is not None
    assert found.name == "application/json"


def test_unsatisfiable_probe_survives_a_parameter_that_cannot_build(ctx):
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

    found = find_unsatisfiable_parameter(operation)

    assert found is not None
    assert found.name == "application/json"
