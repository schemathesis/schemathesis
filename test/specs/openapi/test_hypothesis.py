import json
from pathlib import Path

import pytest
from hypothesis import Phase, assume, given, settings
from hypothesis import strategies as st
from jsonschema import Draft4Validator

import schemathesis
from schemathesis.config import GenerationConfig
from schemathesis.core.parameters import ParameterLocation
from schemathesis.openapi.generation import filters
from schemathesis.openapi.generation.filters import is_valid_header
from schemathesis.specs.openapi import _hypothesis, formats
from schemathesis.specs.openapi._hypothesis import make_positive_strategy
from schemathesis.specs.openapi.references import load_file
from test.utils import assert_requests_call


@pytest.fixture
def operation(make_openapi_3_schema):
    schema = make_openapi_3_schema(
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
    return schemathesis.openapi.from_dict(schema)["/users"]["POST"]


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

    @given(strategy)
    @settings(max_examples=1)
    def test(case):
        # Then it should appear in the final result
        for attr_name, expected_values in expected.items():
            value = getattr(case, attr_name)
            assert value == expected_values

    test()


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


def test_missed_ref(deeply_nested_schema):
    # See GH-1167
    # When not resolved references are present in the schema during constructing a strategy
    schema = schemathesis.openapi.from_dict(deeply_nested_schema)

    @given(schema["/data"]["GET"].as_strategy())
    @settings(max_examples=10)
    def test(case):
        # Then the reference should be correctly resolved
        assert isinstance(case.query["key"], str)

    test()


def test_inlined_definitions(deeply_nested_schema):
    # See GH-1162
    # When not resolved references are present in the schema during constructing a strategy
    # And the referenced schema contains Open API specific keywords
    deeply_nested_schema["components"]["schemas"]["bar"]["nullable"] = True

    schema = schemathesis.openapi.from_dict(deeply_nested_schema)

    @given(schema["/data"]["GET"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        # Then the referenced schema should be properly transformed to the JSON Schema form
        assume(case.query["key"] == "null")

    test()


@pytest.mark.hypothesis_nested
def test_valid_headers():
    # When headers are generated
    # And there is no other keywords than "type"
    strategy = make_positive_strategy(
        {
            "type": "object",
            "properties": {"X-Foo": {"type": "string", "pattern": r"\A[A-F0-9]{12}\Z"}},
            "required": ["X-Foo"],
            "additionalProperties": False,
        },
        "GET /users/",
        ParameterLocation.HEADER,
        None,
        GenerationConfig(),
        Draft4Validator,
    )

    @given(strategy)
    def test(headers):
        # Then headers are always valid
        assert is_valid_header(headers)

    test()


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

    @given(strategy)
    def test(headers):
        # Then headers are always valid
        assert is_valid_header(headers)
        assert set(headers["X-Foo"]) - {"A", "B", "C"} == set()

    test()


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

    @given(strategy)
    def test(_):
        # Then there should be no failed health checks
        pass

    test()


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
        (_remote_schema, lambda v: isinstance(v, int)),
        (_nested_remote_schema, lambda v: isinstance(v, int)),
        (_deep_nested_remote_schema, lambda v: isinstance(v["a"], int)),
        (_colliding_remote_schema, lambda v: isinstance(v["a"], int) and isinstance(v["b"], str)),
        (_back_reference_remote_schema, lambda v: isinstance(v, int)),
        (_scoped_remote_schema, lambda v: isinstance(v, int)),
    ],
)
def test_inline_remote_refs(testdir, deeply_nested_schema, setup, check):
    # See GH-986
    setup(testdir)

    deeply_nested_schema["components"]["schemas"]["foo9"] = {
        "$ref": Path(str(testdir.tmpdir / "bar.json")).as_uri() + "#/bar"
    }

    original = json.dumps(deeply_nested_schema, sort_keys=True, ensure_ascii=True)
    schema = schemathesis.openapi.from_dict(deeply_nested_schema)

    @given(schema["/data"]["GET"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        # Then the referenced schema should be accessible by `hypothesis-jsonschema` and the right value should be
        # generated
        assert check(case.query["key"])

    test()

    # And the original schema is not mutated
    assert json.dumps(deeply_nested_schema, sort_keys=True, ensure_ascii=True) == original


def make_header_param(schema, **kwargs):
    schema["paths"] = {
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


def test_header_filtration_not_needed(ctx, mocker):
    # When schema contains a simple header
    mocked = mocker.spy(filters, "is_valid_header")
    schema = ctx.openapi.build_schema({})
    make_header_param(schema)

    schema = schemathesis.openapi.from_dict(schema)

    @given(schema["/data"]["GET"].as_strategy())
    def test(case):
        assert is_valid_header(case.headers)

    test()

    # Then header filter should not be used
    mocked.assert_not_called()


def test_header_filtration_needed(ctx, mocker):
    # When schema contains a header with a custom format
    mocked = mocker.spy(filters, "is_valid_header")
    schema = ctx.openapi.build_schema({})
    make_header_param(schema, format="date")

    schema = schemathesis.openapi.from_dict(schema)

    @given(schema["/data"]["GET"].as_strategy())
    @settings(max_examples=1)
    def test(_):
        pass

    test()

    # Then header filter should be used
    mocked.assert_called()


def test_missing_header_filter(ctx, mocker):
    # Regression. See GH-1142
    mocked = mocker.spy(filters, "is_valid_header")
    # When some header parameters have the `format` keyword
    # And some don't
    schema = ctx.openapi.build_schema(
        {
            "/data": {
                "get": {
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
                }
            }
        }
    )

    schema = schemathesis.openapi.from_dict(schema)

    @given(schema["/data"]["GET"].as_strategy())
    def test(case):
        assert is_valid_header(case.headers)

    test()

    # Then header filter should be used
    mocked.assert_called()


def test_serializing_shared_header_parameters():
    raw_schema = {
        "swagger": "2.0",
        "info": {"version": "1.0.0", "title": "Example API"},
        "paths": {
            "/data": {
                "get": {
                    "responses": {"default": {"description": "Ok"}},
                },
                "parameters": [
                    {"name": "key", "type": "boolean", "in": "header"},
                ],
            },
        },
    }

    schema = schemathesis.openapi.from_dict(raw_schema)

    @given(schema["/data"]["GET"].as_strategy())
    def test(case):
        assert is_valid_header(case.headers)

    test()


def test_filter_urlencoded(ctx):
    # When API schema allows for inputs that can't be serialized to `application/x-www-form-urlencoded`
    # Then such examples should be filtered out during generation
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
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
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema)

    @given(schema["/test"]["POST"].as_strategy())
    @settings(phases=[Phase.generate], max_examples=15, deadline=None)
    def test(case):
        assert_requests_call(case)

    test()


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
