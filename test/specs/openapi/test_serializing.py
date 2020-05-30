import json
from urllib.parse import quote, unquote

import pytest
from hypothesis import given

import schemathesis
from schemathesis.specs.openapi.serialization import conversion

PRIMITIVE_SCHEMA = {"type": "integer", "enum": [1]}
ARRAY_SCHEMA = {"type": "array", "enum": [["blue", "black", "brown"]]}
OBJECT_SCHEMA = {
    "additionalProperties": False,
    "type": "object",
    "properties": {
        "r": {"type": "integer", "enum": [100]},  # "const" is not supported by Open API
        "g": {"type": "integer", "enum": [200]},
        "b": {"type": "integer", "enum": [150]},
    },
    "required": ["r", "g", "b"],
}


def chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i : i + n]


# Helpers to avoid dictionary ordering issues


class Prefixed:
    def __init__(self, instance, prefix=""):
        self.instance = quote(instance)
        self.prefix = quote(prefix)

    def prepare(self, value):
        raise NotImplementedError

    def __eq__(self, other):
        if self.prefix:
            if not other.startswith(self.prefix):
                return False
            prefix_length = len(self.prefix)
            instance = self.instance[prefix_length:]
            other = other[prefix_length:]
        else:
            instance = self.instance
        return self.prepare(instance) == self.prepare(other)

    def __str__(self):
        return self.instance

    def __repr__(self):
        return f"'{self.instance}'"


class CommaDelimitedObject(Prefixed):
    def prepare(self, value):
        items = unquote(value).split(",")
        return dict(chunks(items, 2))


class DelimitedObject(Prefixed):
    def __init__(self, *args, delimiter=",", **kwargs):
        super().__init__(*args, **kwargs)
        self.delimiter = delimiter

    def prepare(self, value):
        items = unquote(value).split(self.delimiter)
        return dict(item.split("=") for item in items)


def make_openapi_schema(*parameters):
    return {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/teapot": {
                "get": {"summary": "Test", "parameters": list(parameters), "responses": {"200": {"description": "OK"}},}
            }
        },
    }


def assert_generates(raw_schema, expected, parameter):
    schema = schemathesis.from_dict(raw_schema)

    @given(case=schema["/teapot"]["GET"].as_strategy())
    def test(case):
        assert getattr(case, parameter) == expected

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    "schema, explode, style, expected",
    (
        # Based on examples from https://swagger.io/docs/specification/serialization/
        (OBJECT_SCHEMA, True, "deepObject", {"color[r]": 100, "color[g]": 200, "color[b]": 150}),
        (OBJECT_SCHEMA, True, "form", {"r": 100, "g": 200, "b": 150}),
        (OBJECT_SCHEMA, False, "form", {"color": CommaDelimitedObject("r,100,g,200,b,150")}),
        (ARRAY_SCHEMA, False, "pipeDelimited", {"color": "blue|black|brown"}),
        (ARRAY_SCHEMA, True, "pipeDelimited", {"color": ["blue", "black", "brown"]}),
        (ARRAY_SCHEMA, False, "spaceDelimited", {"color": "blue black brown"}),
        (ARRAY_SCHEMA, True, "spaceDelimited", {"color": ["blue", "black", "brown"]}),
        (ARRAY_SCHEMA, False, "form", {"color": "blue,black,brown"}),
        (ARRAY_SCHEMA, True, "form", {"color": ["blue", "black", "brown"]}),
    ),
)
def test_query_serialization_styles_openapi3(schema, explode, style, expected):
    raw_schema = make_openapi_schema(
        {"name": "color", "in": "query", "required": True, "schema": schema, "explode": explode, "style": style}
    )
    assert_generates(raw_schema, expected, "query")


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    "schema, explode, expected",
    (
        (ARRAY_SCHEMA, True, {"X-Api-Key": "blue,black,brown"}),
        (ARRAY_SCHEMA, False, {"X-Api-Key": "blue,black,brown"}),
        (OBJECT_SCHEMA, True, {"X-Api-Key": DelimitedObject("r=100,g=200,b=150")}),
        (OBJECT_SCHEMA, False, {"X-Api-Key": CommaDelimitedObject("r,100,g,200,b,150")}),
    ),
)
def test_header_serialization_styles_openapi3(schema, explode, expected):
    raw_schema = make_openapi_schema(
        {"name": "X-Api-Key", "in": "header", "required": True, "schema": schema, "explode": explode}
    )
    assert_generates(raw_schema, expected, "headers")


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    "schema, explode, expected",
    (
        (ARRAY_SCHEMA, True, {}),
        (ARRAY_SCHEMA, False, {"SessionID": "blue,black,brown"}),
        (OBJECT_SCHEMA, True, {}),
        (OBJECT_SCHEMA, False, {"SessionID": CommaDelimitedObject("r,100,g,200,b,150")}),
    ),
)
def test_cookie_serialization_styles_openapi3(schema, explode, expected):
    raw_schema = make_openapi_schema(
        {"name": "SessionID", "in": "cookie", "required": True, "schema": schema, "explode": explode}
    )
    assert_generates(raw_schema, expected, "cookies")


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    "schema, style, explode, expected",
    (
        (ARRAY_SCHEMA, "simple", False, {"color": quote("blue,black,brown")}),
        (ARRAY_SCHEMA, "simple", True, {"color": quote("blue,black,brown")}),
        (OBJECT_SCHEMA, "simple", False, {"color": CommaDelimitedObject("r,100,g,200,b,150")}),
        (OBJECT_SCHEMA, "simple", True, {"color": DelimitedObject("r=100,g=200,b=150")}),
        (PRIMITIVE_SCHEMA, "label", False, {"color": quote(".1")}),
        (PRIMITIVE_SCHEMA, "label", True, {"color": quote(".1")}),
        (ARRAY_SCHEMA, "label", False, {"color": quote(".blue,black,brown")}),
        (ARRAY_SCHEMA, "label", True, {"color": quote(".blue.black.brown")}),
        (OBJECT_SCHEMA, "label", False, {"color": CommaDelimitedObject(".r,100,g,200,b,150", prefix=".")}),
        (OBJECT_SCHEMA, "label", True, {"color": DelimitedObject(".r=100.g=200.b=150", prefix=".", delimiter=".")}),
        (PRIMITIVE_SCHEMA, "matrix", False, {"color": quote(";color=1")}),
        (PRIMITIVE_SCHEMA, "matrix", True, {"color": quote(";color=1")}),
        (ARRAY_SCHEMA, "matrix", False, {"color": quote(";blue,black,brown")}),
        (ARRAY_SCHEMA, "matrix", True, {"color": quote(";color=blue;color=black;color=brown")}),
        (OBJECT_SCHEMA, "matrix", False, {"color": CommaDelimitedObject(";r,100,g,200,b,150", prefix=";")}),
        (OBJECT_SCHEMA, "matrix", True, {"color": DelimitedObject(";r=100;g=200;b=150", prefix=";", delimiter=";")}),
    ),
)
def test_path_serialization_styles_openapi3(schema, style, explode, expected):
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/teapot/{color}": {
                "get": {
                    "summary": "Test",
                    "parameters": [
                        {
                            "name": "color",
                            "in": "path",
                            "required": True,
                            "schema": schema,
                            "style": style,
                            "explode": explode,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    schema = schemathesis.from_dict(raw_schema)

    @given(case=schema["/teapot/{color}"]["GET"].as_strategy())
    def test(case):
        assert case.path_parameters == expected

    test()


@pytest.mark.hypothesis_nested
def test_query_serialization_styles_openapi_multiple_params():
    raw_schema = make_openapi_schema(
        {
            "name": "color1",
            "in": "query",
            "required": True,
            "schema": ARRAY_SCHEMA,
            "explode": False,
            "style": "pipeDelimited",
        },
        {
            "name": "color2",
            "in": "query",
            "required": True,
            "schema": ARRAY_SCHEMA,
            "explode": False,
            "style": "spaceDelimited",
        },
    )
    assert_generates(raw_schema, {"color1": "blue|black|brown", "color2": "blue black brown"}, "query")


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    "collection_format, expected",
    (
        ("csv", {"color": "blue,black,brown"}),
        ("ssv", {"color": "blue black brown"}),
        ("tsv", {"color": "blue\tblack\tbrown"}),
        ("pipes", {"color": "blue|black|brown"}),
        ("multi", {"color": ["blue", "black", "brown"]}),
    ),
)
def test_query_serialization_styles_swagger2(collection_format, expected):
    raw_schema = {
        "swagger": "2.0",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "host": "127.0.0.1:8888",
        "basePath": "/",
        "schemes": ["http"],
        "produces": ["application/json"],
        "paths": {
            "/teapot": {
                "get": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "color",
                            "required": True,
                            "type": "array",
                            "items": {"type": "string"},
                            "collectionFormat": collection_format,
                            "enum": [["blue", "black", "brown"]],
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    assert_generates(raw_schema, expected, "query")


@pytest.mark.parametrize("item, expected", (({}, {}), ({"key": 1}, {"key": "TEST"})))
def test_item_is_missing(item, expected):
    # When there is no key in the data

    @conversion
    def foo(data, name):
        data[name] = "TEST"

    foo("key")(item)

    # Then the data should not be affected
    # And should be otherwise
    assert item == expected


class JSONString(Prefixed):
    def prepare(self, value):
        return json.loads(unquote(value))


def test_content_serialization():
    raw_schema = make_openapi_schema(
        {"in": "query", "name": "filter", "required": True, "content": {"application/json": {"schema": OBJECT_SCHEMA}}}
    )
    assert_generates(raw_schema, {"filter": JSONString('{"r":100, "g": 200, "b": 150}')}, "query")
