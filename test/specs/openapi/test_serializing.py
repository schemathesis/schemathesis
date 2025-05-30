import json
from email.message import EmailMessage
from urllib.parse import quote, unquote

import pytest
from hypothesis import given, settings

import schemathesis
from schemathesis.core import SCHEMATHESIS_TEST_CASE_HEADER
from schemathesis.generation.modes import GenerationMode
from schemathesis.specs.openapi.serialization import (
    comma_delimited_object,
    conversion,
    deep_object,
    delimited,
    delimited_object,
    extracted_object,
    label_array,
    label_object,
    label_primitive,
    matrix_array,
    matrix_object,
    matrix_primitive,
)
from schemathesis.transport.prepare import get_default_headers
from test.utils import assert_requests_call

PRIMITIVE_SCHEMA = {"type": "integer", "enum": [1]}
NULLABLE_PRIMITIVE_SCHEMA = {"type": "integer", "enum": [1], "nullable": True}
ARRAY_SCHEMA = {"type": "array", "enum": [["blue", "black", "brown"]], "example": ["blue", "black", "brown"]}
NULLABLE_ARRAY_SCHEMA = {"type": "array", "enum": [["blue", "black", "brown"]], "nullable": True}
OBJECT_SCHEMA = {
    "additionalProperties": False,
    "type": "object",
    "properties": {
        "r": {"type": "string", "enum": ["100"], "example": "100"},  # "const" is not supported by Open API
        "g": {"type": "string", "enum": ["200"], "example": "200"},
        "b": {"type": "string", "enum": ["150"], "example": "150"},
    },
    "required": ["r", "g", "b"],
}
NULLABLE_OBJECT_SCHEMA = {
    "additionalProperties": False,
    "type": "object",
    "properties": {
        "r": {"type": "string", "enum": ["100"]},  # "const" is not supported by Open API
        "g": {"type": "string", "enum": ["200"]},
        "b": {"type": "string", "enum": ["150"]},
    },
    "required": ["r", "g", "b"],
    "nullable": True,
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
        return f"{self.__class__.__name__}('{self.instance}', '{self.prefix}')"


class CommaDelimitedObject(Prefixed):
    def prepare(self, value):
        if not value:
            return {}
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
                "get": {"summary": "Test", "parameters": list(parameters), "responses": {"200": {"description": "OK"}}}
            }
        },
    }


def assert_generates(testdir, raw_schema, expected, parameter):
    schema = schemathesis.openapi.from_dict(raw_schema)

    attribute = "path_parameters" if parameter == "path" else parameter

    @given(case=schema["/teapot"]["GET"].as_strategy())
    def test(case):
        assert getattr(case, attribute) in expected

    test()

    testdir.make_test(
        f"""
import json
from urllib.parse import quote, unquote

def chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i : i + n]

class Prefixed:
    def __init__(self, instance, prefix=""):
        self.instance = instance
        self.prefix = prefix

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


class JSONString(Prefixed):
    def prepare(self, value):
        return json.loads(unquote(value))


class CommaDelimitedObject(Prefixed):
    def prepare(self, value):
        if not value:
            return {{}}
        items = unquote(value).split(",")
        return dict(chunks(items, 2))


class DelimitedObject(Prefixed):
    def __init__(self, *args, delimiter=",", **kwargs):
        super().__init__(*args, **kwargs)
        self.delimiter = delimiter

    def prepare(self, value):
        items = unquote(value).split(self.delimiter)
        return dict(item.split("=") for item in items)

@schema.parametrize()
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.{attribute} in {expected!r}
    """,
        schema=raw_schema,
        generation_modes=[GenerationMode.POSITIVE],
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1)


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    ("schema", "explode", "style", "expected"),
    [
        # Based on examples from https://swagger.io/docs/specification/serialization/
        (OBJECT_SCHEMA, True, "deepObject", {"color[r]": "100", "color[g]": "200", "color[b]": "150"}),
        (OBJECT_SCHEMA, True, "form", {"r": "100", "g": "200", "b": "150"}),
        (OBJECT_SCHEMA, False, "form", {"color": CommaDelimitedObject("r,100,g,200,b,150")}),
        (ARRAY_SCHEMA, False, "pipeDelimited", {"color": "blue|black|brown"}),
        (ARRAY_SCHEMA, True, "pipeDelimited", {"color": ["blue", "black", "brown"]}),
        (ARRAY_SCHEMA, False, "spaceDelimited", {"color": "blue black brown"}),
        (ARRAY_SCHEMA, True, "spaceDelimited", {"color": ["blue", "black", "brown"]}),
        (ARRAY_SCHEMA, False, "form", {"color": "blue,black,brown"}),
        (ARRAY_SCHEMA, True, "form", {"color": ["blue", "black", "brown"]}),
    ],
)
def test_query_serialization_styles_openapi3(testdir, schema, explode, style, expected):
    raw_schema = make_openapi_schema(
        {"name": "color", "in": "query", "required": True, "schema": schema, "explode": explode, "style": style}
    )
    assert_generates(testdir, raw_schema, (expected,), "query")


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    ("schema", "explode", "expected"),
    [
        (ARRAY_SCHEMA, True, {"X-Api-Key": "blue,black,brown"}),
        (ARRAY_SCHEMA, False, {"X-Api-Key": "blue,black,brown"}),
        (OBJECT_SCHEMA, True, {"X-Api-Key": DelimitedObject("r=100,g=200,b=150")}),
        (OBJECT_SCHEMA, False, {"X-Api-Key": CommaDelimitedObject("r,100,g,200,b,150")}),
    ],
)
def test_header_serialization_styles_openapi3(testdir, schema, explode, expected):
    raw_schema = make_openapi_schema(
        {"name": "X-Api-Key", "in": "header", "required": True, "schema": schema, "explode": explode}
    )
    assert_generates(testdir, raw_schema, (expected,), "headers")


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    ("schema", "explode", "expected"),
    [
        (ARRAY_SCHEMA, True, {}),
        (ARRAY_SCHEMA, False, {"SessionID": "blue,black,brown"}),
        (OBJECT_SCHEMA, True, {}),
        (OBJECT_SCHEMA, False, {"SessionID": CommaDelimitedObject("r,100,g,200,b,150")}),
    ],
)
def test_cookie_serialization_styles_openapi3(testdir, schema, explode, expected):
    raw_schema = make_openapi_schema(
        {"name": "SessionID", "in": "cookie", "required": True, "schema": schema, "explode": explode}
    )
    assert_generates(testdir, raw_schema, (expected,), "cookies")


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    ("schema", "style", "explode", "expected"),
    [
        (ARRAY_SCHEMA, "simple", False, {"color": quote("blue,black,brown")}),
        (NULLABLE_ARRAY_SCHEMA, "simple", False, {"color": quote("blue,black,brown")}),
        (ARRAY_SCHEMA, "simple", True, {"color": quote("blue,black,brown")}),
        (NULLABLE_ARRAY_SCHEMA, "simple", True, {"color": quote("blue,black,brown")}),
        (OBJECT_SCHEMA, "simple", False, {"color": CommaDelimitedObject("r,100,g,200,b,150")}),
        (NULLABLE_OBJECT_SCHEMA, "simple", False, {"color": CommaDelimitedObject("r,100,g,200,b,150")}),
        (OBJECT_SCHEMA, "simple", True, {"color": DelimitedObject("r=100,g=200,b=150")}),
        (NULLABLE_OBJECT_SCHEMA, "simple", True, {"color": DelimitedObject("r=100,g=200,b=150")}),
        (PRIMITIVE_SCHEMA, "label", False, {"color": quote(".1")}),
        (NULLABLE_PRIMITIVE_SCHEMA, "label", False, {"color": quote(".1")}),
        (PRIMITIVE_SCHEMA, "label", True, {"color": quote(".1")}),
        (NULLABLE_PRIMITIVE_SCHEMA, "label", True, {"color": quote(".1")}),
        (ARRAY_SCHEMA, "label", False, {"color": quote(".blue,black,brown")}),
        (NULLABLE_ARRAY_SCHEMA, "label", False, {"color": quote(".blue,black,brown")}),
        (ARRAY_SCHEMA, "label", True, {"color": quote(".blue.black.brown")}),
        (NULLABLE_ARRAY_SCHEMA, "label", True, {"color": quote(".blue.black.brown")}),
        (OBJECT_SCHEMA, "label", False, {"color": CommaDelimitedObject(".r,100,g,200,b,150", prefix=".")}),
        (NULLABLE_OBJECT_SCHEMA, "label", False, {"color": CommaDelimitedObject(".r,100,g,200,b,150", prefix=".")}),
        (OBJECT_SCHEMA, "label", True, {"color": DelimitedObject(".r=100.g=200.b=150", prefix=".", delimiter=".")}),
        (
            NULLABLE_OBJECT_SCHEMA,
            "label",
            True,
            {"color": DelimitedObject(".r=100.g=200.b=150", prefix=".", delimiter=".")},
        ),
        (PRIMITIVE_SCHEMA, "matrix", False, {"color": quote(";color=1")}),
        (NULLABLE_PRIMITIVE_SCHEMA, "matrix", False, {"color": quote(";color=1")}),
        (PRIMITIVE_SCHEMA, "matrix", True, {"color": quote(";color=1")}),
        (NULLABLE_PRIMITIVE_SCHEMA, "matrix", True, {"color": quote(";color=1")}),
        (ARRAY_SCHEMA, "matrix", False, {"color": quote(";blue,black,brown")}),
        (NULLABLE_ARRAY_SCHEMA, "matrix", False, {"color": quote(";blue,black,brown")}),
        (ARRAY_SCHEMA, "matrix", True, {"color": quote(";color=blue;color=black;color=brown")}),
        (NULLABLE_ARRAY_SCHEMA, "matrix", True, {"color": quote(";color=blue;color=black;color=brown")}),
        (OBJECT_SCHEMA, "matrix", False, {"color": CommaDelimitedObject(";r,100,g,200,b,150", prefix=";")}),
        (NULLABLE_OBJECT_SCHEMA, "matrix", False, {"color": CommaDelimitedObject(";r,100,g,200,b,150", prefix=";")}),
        (OBJECT_SCHEMA, "matrix", True, {"color": DelimitedObject(";r=100;g=200;b=150", prefix=";", delimiter=";")}),
        (
            NULLABLE_OBJECT_SCHEMA,
            "matrix",
            True,
            {"color": DelimitedObject(";r=100;g=200;b=150", prefix=";", delimiter=";")},
        ),
    ],
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
    schema = schemathesis.openapi.from_dict(raw_schema)

    @given(case=schema["/teapot/{color}"]["GET"].as_strategy())
    def test(case):
        assert case.path_parameters == expected

    test()


@pytest.mark.hypothesis_nested
def test_query_serialization_styles_openapi_multiple_params(testdir):
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
    assert_generates(testdir, raw_schema, ({"color1": "blue|black|brown", "color2": "blue black brown"},), "query")


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    ("collection_format", "expected"),
    [
        ("csv", {"color": "blue,black,brown"}),
        ("ssv", {"color": "blue black brown"}),
        ("tsv", {"color": "blue\tblack\tbrown"}),
        ("pipes", {"color": "blue|black|brown"}),
        ("multi", {"color": ["blue", "black", "brown"]}),
    ],
)
def test_query_serialization_styles_swagger2(testdir, collection_format, expected):
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
    assert_generates(testdir, raw_schema, (expected,), "query")


@pytest.mark.parametrize(("item", "expected"), [({}, {}), ({"key": 1}, {"key": "TEST"})])
def test_item_is_missing(item, expected):
    # When there is no key in the data

    @conversion
    def convert_func(data, name):
        data[name] = "TEST"

    convert_func("key")(item)

    # Then the data should not be affected
    # And should be otherwise
    assert item == expected


class JSONString(Prefixed):
    def prepare(self, value):
        return json.loads(unquote(value))


def test_content_serialization(testdir):
    raw_schema = make_openapi_schema(
        {"in": "query", "name": "filter", "required": True, "content": {"application/json": {"schema": OBJECT_SCHEMA}}}
    )
    assert_generates(testdir, raw_schema, ({"filter": JSONString('{"r": "100", "g": "200", "b": "150"}')},), "query")


def make_array_schema(location, style):
    return {
        "name": "bbox",
        "in": location,
        "required": True,
        "schema": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "number", "enum": [1.1]}},
        "style": style,
        "explode": False,
    }


@pytest.mark.parametrize(
    ("parameter", "expected"),
    [
        (
            make_array_schema("query", "form"),
            ({"bbox": "1.1,1.1,1.1,1.1"},),
        ),
        (
            make_array_schema("path", "label"),
            ({"bbox": ".1.1%2C1.1%2C1.1%2C1.1"},),
        ),
        (
            make_array_schema("path", "matrix"),
            ({"bbox": "%3B1.1%2C1.1%2C1.1%2C1.1"},),
        ),
        (
            {
                "name": "bbox",
                "in": "query",
                "schema": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "enum": [1]},
                    "nullable": True,
                },
                "style": "form",
                "explode": False,
                "required": True,
            },
            ({"bbox": "1,1"}, {"bbox": ""}),
        ),
    ],
)
def test_non_string_serialization(testdir, parameter, expected):
    # GH: #651
    raw_schema = make_openapi_schema(parameter)
    assert_generates(testdir, raw_schema, expected, parameter["in"])


@pytest.mark.parametrize(
    ("func", "kwargs"),
    [
        (delimited, {"delimiter": ","}),
        (deep_object, {}),
        (comma_delimited_object, {}),
        (delimited_object, {}),
        (extracted_object, {}),
        (label_primitive, {}),
        (label_array, {"explode": True}),
        (label_array, {"explode": False}),
        (label_object, {"explode": True}),
        (label_object, {"explode": False}),
        (matrix_primitive, {}),
        (matrix_array, {"explode": True}),
        (matrix_array, {"explode": False}),
        (matrix_object, {"explode": True}),
        (matrix_object, {"explode": False}),
    ],
)
def test_nullable_parameters(
    func,
    kwargs,
):
    # Nullable parameters are converted to an empty string
    assert func("foo", **kwargs)({"foo": None}) == {"foo": ""}


def test_security_definition_parameter(ctx, testdir):
    # When the API contains an example for one of its parameters
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "parameters": [
                        {
                            "name": "body",
                            "in": "body",
                            "schema": {
                                "type": "object",
                                "example": {"foo": "bar"},
                            },
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        securityDefinitions={"token": {"type": "apiKey", "name": "Authorization", "in": "header"}},
        security=[{"token": []}],
        version="2.0",
    )
    testdir.make_test(
        """
@schema.parametrize()
@settings(phases=[Phase.explicit])
def test_(case):
    pass
        """,
        schema=schema,
    )
    result = testdir.runpytest("-v")
    # Then it should work as expected
    # And existing Open API serialization styles should not affect it
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize(
    "type_name",
    # `null` is not a valid Open API type, but it is possible to have `None` with custom hooks, therefore it is here
    # for simplicity
    ["null", "string", "boolean", "array", "integer", "number"],
)
def test_unusual_form_schema(ctx, type_name):
    # See GH-1152
    # When API schema defines multipart media type
    # And its schema is not an object or bytes (string + format=byte)
    schema = ctx.openapi.build_schema(
        {
            "/multipart": {
                "post": {
                    "requestBody": {
                        "content": {"multipart/form-data": {"schema": {"type": type_name}}},
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema)

    @given(case=schema["/multipart"]["POST"].as_strategy())
    @settings(max_examples=5, deadline=None)
    def test(case):
        # Then it should lead to a valid network request
        assert_requests_call(case)
        # And should contain the proper content type
        kwargs = case.as_transport_kwargs()
        content_type = kwargs["headers"]["Content-Type"]
        assert content_type.startswith("multipart/form-data; boundary=")
        # And data is a valid multipart
        message = EmailMessage()
        message.attach(kwargs["data"])
        assert message.is_multipart()
        # When custom headers are passed
        headers = case.as_transport_kwargs(headers={"Authorization": "Bearer FOO"})["headers"]
        # Then content type should be valid
        assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
        # And the original headers are preserved
        assert headers["Authorization"] == "Bearer FOO"
        # When the Content-Type header is passed explicitly
        headers = case.as_transport_kwargs(headers={"Content-Type": "text/plain"})["headers"]
        # Then it should be preferred
        assert headers["Content-Type"] == "text/plain"
        # And it should be case-insensitive
        headers = case.as_transport_kwargs(headers={"content-type": "text/plain"})["headers"]
        assert headers["content-type"] == "text/plain"
        assert list(headers) == [*list(get_default_headers()), SCHEMATHESIS_TEST_CASE_HEADER, "content-type"]

    test()
