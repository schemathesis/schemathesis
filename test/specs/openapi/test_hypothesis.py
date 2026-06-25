import uuid
import warnings
from datetime import date
from pathlib import Path

import jsonschema_rs
import pytest
from hypothesis import HealthCheck, Phase, assume, given, settings
from hypothesis import strategies as st
from hypothesis.errors import FailedHealthCheck, Unsatisfiable
from jsonschema_rs import Draft4Validator

import schemathesis
from schemathesis.config import GenerationConfig
from schemathesis.core.jsonschema.resolver import load_file
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.openapi.generation import filters
from schemathesis.openapi.generation.filters import is_valid_header
from schemathesis.specs.openapi import _hypothesis, formats
from schemathesis.specs.openapi._hypothesis import make_positive_strategy
from test.utils import assert_requests_call, to_float32


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


def test_missed_ref(ctx, deeply_nested_schema):
    # See GH-1167
    # When not resolved references are present in the schema during constructing a strategy
    schema = ctx.openapi.load_schema(deeply_nested_schema["paths"], components=deeply_nested_schema["components"])

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


@pytest.mark.parametrize("spec_required", [True, False], ids=["spec-required", "spec-optional"])
def test_header_schema_dedupes_case_insensitive_duplicates(ctx, spec_required):
    # HTTP header names are case-insensitive; the merged headers schema must collapse
    # spec parameter and security-scheme entries that differ only by case, and the
    # canonical first-seen casing must end up `required` whenever any duplicate is required.
    schema = ctx.openapi.load_schema(
        {
            "/v2/": {
                "post": {
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
                }
            }
        },
        components={
            "securitySchemes": {"Bearer": {"type": "http", "scheme": "bearer"}},
        },
    )
    operation = schema["/v2/"]["POST"]
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

    original = jsonschema_rs.canonical.json.to_string(deeply_nested_schema)
    schema = schemathesis.openapi.from_dict(deeply_nested_schema)

    @given(schema["/data"]["GET"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        # Then the referenced schema should be accessible by `hypothesis-jsonschema` and the right value should be
        # generated
        assert check(case.query["key"])

    test()

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


def test_header_filtration_not_needed(ctx, mocker):
    # When schema contains a simple header
    mocked = mocker.spy(filters, "is_valid_header")
    schema = ctx.openapi.load_schema(make_header_param())

    @given(schema["/data"]["GET"].as_strategy())
    def test(case):
        assert is_valid_header(case.headers)

    test()

    # Then header filter should not be used
    mocked.assert_not_called()


def test_header_filtration_needed(ctx, mocker):
    # When schema contains a header with a custom format
    mocked = mocker.spy(filters, "is_valid_header")
    schema = ctx.openapi.load_schema(make_header_param(format="date"))

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
    schema = ctx.openapi.load_schema(
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

    @given(schema["/data"]["GET"].as_strategy())
    def test(case):
        assert is_valid_header(case.headers)

    test()

    # Then header filter should be used
    mocked.assert_called()


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

    @given(schema["/data"]["GET"].as_strategy())
    def test(case):
        assert is_valid_header(case.headers)

    test()


def test_filter_urlencoded(ctx):
    # When API schema allows for inputs that can't be serialized to `application/x-www-form-urlencoded`
    # Then such examples should be filtered out during generation
    schema = ctx.openapi.load_schema(
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


@pytest.mark.hypothesis_nested
def test_email_format_passes_jsonschema_rs_validation(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
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
                }
            }
        }
    )
    operation = schema["/users"]["POST"]
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
    schema = ctx.openapi.load_schema(
        {
            "/events": {
                "get": {
                    "parameters": [
                        {
                            "name": "start_date",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string", "format": "date"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = schema["/events"]["GET"]

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
    schema = ctx.openapi.load_schema(
        {
            "/blocks/{block}": {
                "get": {
                    "parameters": [
                        {
                            "name": "block",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": format_name},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = schema["/blocks/{block}"]["GET"]

    @given(case=operation.as_strategy())
    @settings(max_examples=1, deadline=None)
    def inner(case):
        assert case.path_parameters["block"] == "192.168.1.0%2F24"

    inner()


@pytest.mark.hypothesis_nested
def test_path_example_without_slash_does_not_allow_encoded_slash(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/blocks/{block}": {
                "get": {
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
                }
            }
        }
    )
    operation = schema["/blocks/{block}"]["GET"]
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
def test_path_example_with_slash_allows_encoded_slash(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/blocks/{block}": {
                "get": {
                    "parameters": [
                        {
                            "name": "block",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "pattern": r"^[0-9.]+/[0-9]+$"},
                            "example": "192.168.1.0/24",
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = schema["/blocks/{block}"]["GET"]
    strategy = _hypothesis.get_parameters_strategy(
        operation,
        GenerationMode.POSITIVE,
        ParameterLocation.PATH,
        GenerationConfig(),
        mix_examples=False,
    )

    @given(value=strategy)
    @settings(max_examples=10, deadline=None)
    def inner(value):
        assert "%2F" in value["block"]

    inner()


@pytest.mark.hypothesis_nested
def test_path_parameters_encoded_braces_are_filtered(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/blocks/{block}": {
                "get": {
                    "parameters": [
                        {
                            "name": "block",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "pattern": "^[{}]$"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = schema["/blocks/{block}"]["GET"]
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

    @given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE))
    @settings(max_examples=30, deadline=None, suppress_health_check=list(HealthCheck))
    def inner(case):
        value = case.path_parameters["productName"]
        upper = value.upper()
        assert "%5C" not in upper, value
        assert "%7F" not in upper, value
        assert not any(f"%{i:02X}" in upper for i in range(0x20)), value

    inner()


@pytest.mark.hypothesis_nested
def test_float_format_snapping_preserves_literal_const(ctx):
    # A literal value that happens to be shaped like a float schema must not be rewritten as if it were one.
    literal = {"format": "float", "exclusiveMinimum": 0}
    schema = ctx.openapi.load_schema(
        {
            "/route": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"const": literal}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )
    operation = schema["/route"]["POST"]

    @given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE))
    @settings(max_examples=5, deadline=None, suppress_health_check=list(HealthCheck))
    def inner(case):
        assert case.body == literal, case.body

    inner()


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize("bound", [1e39, 10**1000], ids=["float", "integer"])
def test_float_format_bound_outside_single_precision_range(ctx, bound):
    # An exclusive bound beyond the float32 range must clamp to a representable value, not crash strategy preparation.
    schema = ctx.openapi.load_schema(
        {
            "/route": {
                "get": {
                    "parameters": [
                        {
                            "name": "f",
                            "in": "query",
                            "schema": {"type": "number", "format": "float", "exclusiveMaximum": bound},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )
    operation = schema["/route"]["GET"]

    @given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE))
    @settings(max_examples=5, deadline=None, suppress_health_check=list(HealthCheck))
    def inner(case):
        value = case.query.get("f")
        if value is not None:
            assert to_float32(float(value)) < 1e39, value

    inner()


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
    operation = ctx.openapi.load_schema(
        {
            "/route": {
                "get": {
                    "parameters": [{"name": "f", "in": "query", "required": True, "schema": schema}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )["/route"]["GET"]
    produced = []

    @given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE))
    @settings(max_examples=15, deadline=None, suppress_health_check=list(HealthCheck))
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

    inner()
    assert produced


@pytest.mark.hypothesis_nested
def test_float_format_on_integer_type_keeps_bound(ctx):
    # `format: float` on an integer schema is contradictory; snapping must not erase its bound and let
    # generation drift below it.
    schema = ctx.openapi.load_schema(
        {
            "/route": {
                "get": {
                    "parameters": [
                        {
                            "name": "f",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "format": "float", "exclusiveMinimum": 1000},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )
    operation = schema["/route"]["GET"]

    @given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE))
    @settings(max_examples=10, deadline=None, suppress_health_check=list(HealthCheck))
    def inner(case):
        value = case.query.get("f")
        if value is not None:
            assert int(value) > 1000, value

    inner()


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize("key", ["example", "examples"])
def test_float_format_collapsing_example_not_mixed_into_strategy(ctx, key):
    # A spec example valid as float64 but collapsing to 0 in float32 must not be mixed into positive generation.
    value = [5e-324] if key == "examples" else 5e-324
    schema = ctx.openapi.load_schema(
        {
            "/route": {
                "get": {
                    "parameters": [
                        {
                            "name": "f",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "number", "format": "float", "exclusiveMinimum": 0, key: value},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )
    operation = schema["/route"]["GET"]

    @given(case=operation.as_strategy(generation_mode=GenerationMode.POSITIVE))
    @settings(max_examples=50, deadline=None, suppress_health_check=list(HealthCheck))
    def inner(case):
        value = case.query.get("f")
        if value is not None:
            assert to_float32(float(value)) > 0, value

    inner()


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
