import json

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

import schemathesis
from schemathesis.specs.openapi import _hypothesis
from schemathesis.specs.openapi._hypothesis import get_case_strategy, is_valid_header, make_positive_strategy
from schemathesis.specs.openapi.references import load_file


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
    return schemathesis.from_dict(schema)["/users"]["POST"]


@pytest.mark.parametrize(
    "values, expected",
    (
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
    ),
)
def test_explicit_attributes(operation, values, expected):
    # When some Case's attribute is passed explicitly to the case strategy
    strategy = get_case_strategy(operation=operation, **values)

    @given(strategy)
    @settings(max_examples=1)
    def test(case):
        # Then it should appear in the final result
        for attr_name, expected_values in expected.items():
            value = getattr(case, attr_name)
            assert value == expected_values

    test()


@pytest.fixture
def deeply_nested_schema(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
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
    }
    empty_open_api_3_schema["components"] = {
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
    }
    return empty_open_api_3_schema


def test_missed_ref(deeply_nested_schema):
    # See GH-1167
    # When not resolved references are present in the schema during constructing a strategy
    schema = schemathesis.from_dict(deeply_nested_schema)

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

    schema = schemathesis.from_dict(deeply_nested_schema)

    @given(schema["/data"]["GET"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        # Then the referenced schema should be properly transformed to the JSON Schema form
        assume(case.query["key"] == "null")

    test()


@pytest.mark.parametrize("keywords", ({}, {"pattern": r"\A[A-F0-9]{12}\Z"}))
@pytest.mark.hypothesis_nested
def test_valid_headers(keywords):
    # When headers are generated
    # And there is no other keywords than "type"
    strategy = make_positive_strategy(
        {
            "type": "object",
            "properties": {"X-Foo": {"type": "string", **keywords}},
            "required": ["X-Foo"],
            "additionalProperties": False,
        },
        "GET /users/",
        "header",
        None,
    )

    @given(strategy)
    def test(headers):
        # Then headers are always valid
        assert is_valid_header(headers)

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
        "header",
        None,
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
    "setup, check",
    (
        (_remote_schema, lambda v: isinstance(v, int)),
        (_nested_remote_schema, lambda v: isinstance(v, int)),
        (_deep_nested_remote_schema, lambda v: isinstance(v["a"], int)),
        (_colliding_remote_schema, lambda v: isinstance(v["a"], int) and isinstance(v["b"], str)),
        (_back_reference_remote_schema, lambda v: isinstance(v, int)),
        (_scoped_remote_schema, lambda v: isinstance(v, int)),
    ),
)
def test_inline_remote_refs(testdir, deeply_nested_schema, setup, check):
    # See GH-986
    setup(testdir)
    deeply_nested_schema["components"]["schemas"]["foo9"] = {"$ref": "bar.json#/bar"}

    original = json.dumps(deeply_nested_schema, sort_keys=True, ensure_ascii=True)
    schema = schemathesis.from_dict(deeply_nested_schema)

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


def test_header_filtration_not_needed(empty_open_api_3_schema, mocker):
    # When schema contains a simple header
    mocked = mocker.spy(_hypothesis, "is_valid_header")
    make_header_param(empty_open_api_3_schema)

    schema = schemathesis.from_dict(empty_open_api_3_schema)

    @given(schema["/data"]["GET"].as_strategy())
    def test(case):
        assert is_valid_header(case.headers)

    test()

    # Then header filter should not be used
    mocked.assert_not_called()


def test_header_filtration_needed(empty_open_api_3_schema, mocker):
    # When schema contains a header with a custom format
    mocked = mocker.spy(_hypothesis, "is_valid_header")
    make_header_param(empty_open_api_3_schema, format="date")

    schema = schemathesis.from_dict(empty_open_api_3_schema)

    @given(schema["/data"]["GET"].as_strategy())
    @settings(max_examples=1)
    def test(_):
        pass

    test()

    # Then header filter should be used
    mocked.assert_called()


def test_missing_header_filter(empty_open_api_3_schema, mocker):
    # Regression. See GH-1142
    mocked = mocker.spy(_hypothesis, "is_valid_header")
    # When some header parameters have the `format` keyword
    # And some don't
    empty_open_api_3_schema["paths"] = {
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

    schema = schemathesis.from_dict(empty_open_api_3_schema)

    @given(schema["/data"]["GET"].as_strategy())
    def test(case):
        assert is_valid_header(case.headers)

    test()

    # Then header filter should be used
    mocked.assert_called()


@pytest.mark.parametrize(
    "value, expected",
    (
        ("foo", True),
        ("тест", False),
        ("\n", False),
    ),
)
def test_is_valid_header(value, expected):
    assert is_valid_header({"foo": value}) is expected


def test_unregister_string_format_valid():
    name = "example"
    schemathesis.openapi.format(name, st.text())
    assert name in _hypothesis.STRING_FORMATS
    _hypothesis.unregister_string_format(name)
    assert name not in _hypothesis.STRING_FORMATS


def test_unregister_string_format_invalid():
    with pytest.raises(ValueError, match="Unknown Open API format: unknown"):
        _hypothesis.unregister_string_format("unknown")
