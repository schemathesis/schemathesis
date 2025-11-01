import datetime

import pytest
from hypothesis import HealthCheck, assume, find, given, settings
from hypothesis.errors import FailedHealthCheck, NoSuchExample, Unsatisfiable

import schemathesis
from schemathesis.core import NOT_SET
from schemathesis.core.errors import InvalidSchema
from schemathesis.generation.modes import GenerationMode
from schemathesis.openapi.generation.filters import is_valid_header
from schemathesis.specs.openapi._hypothesis import get_default_format_strategies
from schemathesis.specs.openapi.adapter.security import ORIGINAL_SECURITY_TYPE_KEY

from .utils import as_param


@pytest.mark.parametrize("schema_name", ["simple_swagger.yaml", "simple_openapi.yaml"])
@pytest.mark.parametrize("type_", ["string", "integer", "array", "boolean", "number"])
def test_headers(testdir, schema_name, type_):
    # When parameter is specified for "header"
    if schema_name == "simple_swagger.yaml":
        kwargs = {"type": type_}
    else:
        kwargs = {"schema": {"type": type_}}
    testdir.make_test(
        """
@schema.parametrize()
@settings(suppress_health_check=[HealthCheck.filter_too_much], deadline=None, max_examples=3)
def test_(case):
    assert_str(case.headers["X-Header"])
    assert_requests_call(case)
        """,
        schema_name=schema_name,
        **as_param({"name": "X-Header", "in": "header", "required": True, **kwargs}),
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then the generated test case should contain it in its `headers` attribute
    testdir.run_and_assert(passed=1)


@pytest.mark.parametrize("type_", ["string", "integer", "array", "object", "boolean", "number"])
def test_cookies(testdir, type_):
    # When parameter is specified for "cookie"
    testdir.make_test(
        """
@schema.parametrize()
@settings(suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.data_too_large], deadline=None, max_examples=20)
def test_(case):
    assert_str(case.cookies["token"])
    assert_requests_call(case)
        """,
        schema_name="simple_openapi.yaml",
        **as_param({"name": "token", "in": "cookie", "required": True, "schema": {"type": type_}}),
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then the generated test case should contain it in its `cookies` attribute
    testdir.run_and_assert(passed=1)


def test_body(testdir):
    # When parameter is specified for "body"
    testdir.make_test(
        """
@schema.include(method="POST").parametrize()
@settings(max_examples=3, deadline=None)
def test_(case):
    assert_int(case.body)
    assert_requests_call(case)
        """,
        paths={
            "/users": {
                "post": {
                    "parameters": [{"name": "id", "in": "body", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then the generated test case should contain it in its `body` attribute
    testdir.run_and_assert(passed=1)


def test_path(testdir):
    # When parameter is specified for "path"
    testdir.make_test(
        """
@schema.include(path_regex="/users/{user_id}").parametrize()
@settings(max_examples=3, deadline=None)
def test_(case):
    if not hasattr(case.meta.phase.data, "description"):
        assert_int(case.path_parameters["user_id"])
    assert_requests_call(case)
        """,
        paths={
            "/users/{user_id}": {
                "get": {
                    "parameters": [{"name": "user_id", "required": True, "in": "path", "type": "integer"}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then the generated test case should contain it its `path_parameters` attribute
    testdir.run_and_assert(passed=1)


def test_multiple_path_variables(testdir):
    # When there are multiple parameters for "path"
    testdir.make_test(
        """
@schema.include(path_regex="/users/{user_id}/{event_id}").parametrize()
@settings(max_examples=3, deadline=None)
def test_(case):
    if not hasattr(case.meta.phase.data, "description"):
        assert_int(case.path_parameters["user_id"])
        assert_int(case.path_parameters["event_id"])
    assert_requests_call(case)
        """,
        paths={
            "/users/{user_id}/{event_id}": {
                "get": {
                    "parameters": [
                        {"name": "user_id", "required": True, "in": "path", "type": "integer"},
                        {"name": "event_id", "required": True, "in": "path", "type": "integer"},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then the generated test case should contain them its `path_parameters` attribute
    testdir.run_and_assert(passed=1)


def test_form_data(testdir):
    # When parameter is specified for "formData"
    testdir.make_test(
        """
@schema.include(method="POST").parametrize()
@settings(max_examples=1, deadline=None)
def test_(case):
    assert_str(case.body["status"])
    assert_requests_call(case)
        """,
        paths={
            "/users": {
                "post": {
                    "parameters": [{"name": "status", "in": "formData", "required": True, "type": "string"}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then the generated test case should contain it in its `body` attribute
    testdir.run_and_assert(passed=1)


@pytest.fixture(params=["swagger", "openapi"])
def schema_spec(request):
    return request.param


@pytest.fixture
def base_schema(request, schema_spec):
    if schema_spec == "swagger":
        return request.getfixturevalue("simple_schema")
    if schema_spec == "openapi":
        return request.getfixturevalue("simple_openapi")


@pytest.fixture(params=["header", "query"])
def location(request):
    return request.param


@pytest.fixture
def schema(schema_spec, location, base_schema):
    # It is the same for Swagger & Open API
    definition = {"api_key": {"type": "apiKey", "name": "api_key", "in": location}}
    if schema_spec == "swagger":
        base_schema["securityDefinitions"] = definition
    if schema_spec == "openapi":
        components = base_schema.setdefault("components", {})
        components["securitySchemes"] = definition
    base_schema["security"] = [{"api_key": []}]
    return base_schema


def test_security_definitions_api_key(testdir, schema, location):
    # When schema contains "apiKeySecurity" security definition
    # And it is in query or header
    location = "headers" if location == "header" else location
    testdir.make_test(
        f"""
@schema.parametrize()
@settings(max_examples=1, deadline=None)
def test_(case):
    assert_str(case.{location}["api_key"])
    assert_requests_call(case)
        """,
        schema=schema,
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then the generated test case should contain API key in a proper place
    testdir.run_and_assert(passed=1)


@pytest.fixture
def cookie_schema(simple_openapi):
    components = simple_openapi.setdefault("components", {})
    components["securitySchemes"] = {"api_key": {"type": "apiKey", "name": "api_key", "in": "cookie"}}
    simple_openapi["security"] = [{"api_key": []}]
    return simple_openapi


def test_security_definitions_api_key_cookie(testdir, cookie_schema):
    # When schema contains "apiKeySecurity" security definition
    # And it is in cookie
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1, deadline=None)
def test_(case):
    assert_str(case.cookies["api_key"])
    assert_requests_call(case)
        """,
        schema=cookie_schema,
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then the generated test case should contain API key in a proper place
    testdir.run_and_assert(passed=1)


def _assert_parameter(schema, schema_spec, location, expected=None):
    # When security definition is defined as "apiKey"
    schema = schemathesis.openapi.from_dict(schema)
    if schema_spec == "swagger":
        operation = schema["/users"]["get"]
        expected = (
            expected
            if expected is not None
            else [
                {
                    "in": location,
                    "name": "api_key",
                    "type": "string",
                    "required": True,
                    ORIGINAL_SECURITY_TYPE_KEY: "apiKey",
                }
            ]
        )
    else:
        operation = schema["/query"]["get"]
        expected = (
            expected
            if expected is not None
            else [
                {
                    "in": location,
                    "name": "api_key",
                    "schema": {"type": "string"},
                    "required": True,
                    ORIGINAL_SECURITY_TYPE_KEY: "apiKey",
                }
            ]
        )
    parameters = [param for param in operation.security.iter_parameters() if param["in"] == location]
    # Then it should be presented as a "string" parameter
    assert parameters == expected


def test_security_as_parameters_api_key(schema, schema_spec, location):
    _assert_parameter(schema, schema_spec, location)


def test_security_as_parameters_api_key_cookie(cookie_schema):
    _assert_parameter(cookie_schema, "openapi", "cookie")


def test_security_as_parameters_api_key_overridden(overridden_security_schema, schema_spec, location):
    _assert_parameter(overridden_security_schema, schema_spec, location, [])


@pytest.fixture
def overridden_security_schema(schema, schema_spec):
    if schema_spec == "swagger":
        schema["paths"]["/users"]["get"]["security"] = []
    if schema_spec == "openapi":
        schema["paths"]["/query"]["get"]["security"] = []
    return schema


def test_security_definitions_override(testdir, overridden_security_schema, location):
    # When "security" is an empty list in the API operation definition
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1, deadline=None)
def test_(case):
    assert "api_key" not in (case.headers or [])
    assert "api_key" not in (case.query or [])
    assert_requests_call(case)
        """,
        schema=overridden_security_schema,
    )
    # Then the generated test case should not contain API key
    testdir.run_and_assert(passed=1)


@pytest.fixture
def basic_auth_schema(base_schema, schema_spec):
    if schema_spec == "swagger":
        base_schema["securityDefinitions"] = {"basic_auth": {"type": "basic"}}
    if schema_spec == "openapi":
        components = base_schema.setdefault("components", {})
        components["securitySchemes"] = {"basic_auth": {"type": "http", "scheme": "basic"}}
    base_schema["security"] = [{"basic_auth": []}]
    return base_schema


def test_security_definitions_basic_auth(testdir, basic_auth_schema):
    # When schema is using HTTP Basic Auth
    testdir.make_test(
        """
import base64

@schema.parametrize()
@settings(max_examples=10, deadline=None)
def test_(case):
    assert "Authorization" in case.headers
    auth = case.headers["Authorization"]
    assert auth.startswith("Basic ")
    assert isinstance(base64.b64decode(auth[6:]), bytes)
    assert_requests_call(case)
        """,
        schema=basic_auth_schema,
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then the generated data should contain a valid "Authorization" header
    testdir.run_and_assert(passed=1)


def test_security_definitions_bearer_auth(testdir, simple_openapi):
    # When schema is using HTTP Bearer Auth scheme
    components = simple_openapi.setdefault("components", {})
    components["securitySchemes"] = {"bearer_auth": {"type": "http", "scheme": "bearer"}}
    simple_openapi["security"] = [{"bearer_auth": []}]
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1, deadline=None)
def test_(case):
    assert "Authorization" in case.headers
    auth = case.headers["Authorization"]
    assert auth.startswith("Bearer ")
    assert_requests_call(case)
        """,
        schema=simple_openapi,
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then the generated test case should contain a valid "Authorization" header
    testdir.run_and_assert("-s", passed=1)


def test_bearer_auth_valid_header():
    # When an HTTP Bearer Auth headers is generated
    # Then it should be a valid header
    # And no invalid headers should be generated
    strategy = get_default_format_strategies()["_bearer_auth"]
    with pytest.raises(NoSuchExample):
        find(strategy, lambda x: not is_valid_header({"x": x}))


def test_unknown_data(testdir):
    # When parameter is specified for unknown "in"
    # And schema validation is disabled
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(case):
    pass
        """,
        **as_param({"name": "status", "in": "unknown", "required": True, "type": "string"}),
    )
    # Then the generated test ignores this parameter
    testdir.run_and_assert(passed=1)


@pytest.mark.hypothesis_nested
def test_date_deserializing(ctx):
    # When dates in schema are written without quotes (achieved by dumping the schema with date instances)
    schema_path = ctx.openapi.write_schema(
        {
            "/teapot": {
                "get": {
                    "summary": "Test",
                    "parameters": [
                        {
                            "name": "key",
                            "in": "query",
                            "required": True,
                            "schema": {
                                "allOf": [
                                    # For sake of example to check allOf logic
                                    {"type": "string", "example": datetime.date(2020, 1, 1)},
                                    {"type": "string", "example": datetime.date(2020, 1, 1)},
                                ]
                            },
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        format="yaml",
    )
    # Then yaml loader should ignore it
    # And data generation should work without errors
    schema = schemathesis.openapi.from_path(str(schema_path))

    @given(case=schema["/teapot"]["GET"].as_strategy())
    @settings(suppress_health_check=[HealthCheck.filter_too_much])
    def test(case):
        assert isinstance(case.query["key"], str)

    test()


def test_json_media_type(testdir):
    # When API operation expects a JSON-compatible media type
    testdir.make_test(
        """
@settings(max_examples=10, deadline=None)
@schema.parametrize()
def test_(case):
    kwargs = case.as_transport_kwargs()
    assert kwargs["headers"]["Content-Type"] == "application/problem+json"
    assert "key" in kwargs["json"]
    assert_requests_call(case)
        """,
        schema={
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "paths": {
                "/users": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/problem+json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"key": {"type": "string"}},
                                        "required": ["key"],
                                    }
                                }
                            },
                        },
                        "responses": {
                            "200": {
                                "description": "OK",
                            }
                        },
                    }
                }
            },
        },
        generation_modes=[GenerationMode.POSITIVE],
    )
    # Then the payload should be serialized as json
    testdir.run_and_assert(passed=1)


def test_nullable_body_behind_a_reference(ctx):
    # When a body parameter is nullable and is behind a reference
    raw_schema = ctx.openapi.build_schema(
        {
            "/payload": {
                "post": {
                    "parameters": [{"$ref": "#/parameters/Foo"}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        parameters={
            "Foo": {
                "in": "body",
                "name": "payload",
                "required": True,
                "schema": {"type": "string"},
                "x-nullable": True,
            }
        },
    )
    # Then it should be properly collected
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/payload"]["POST"]
    # And its definition is not transformed to JSON Schema
    assert operation.body[0].definition == {
        "in": "body",
        "name": "payload",
        "required": True,
        "schema": {"type": "string"},
        "x-nullable": True,
    }


@pytest.fixture(params=["aiohttp", "flask"])
def api_schema(ctx, request, openapi_version):
    if openapi_version.is_openapi_2:
        schema = ctx.openapi.build_schema(
            {
                "/payload": {
                    "post": {
                        "parameters": [
                            {
                                "in": "body",
                                "required": True,
                                "name": "payload",
                                "schema": {"type": "boolean", "x-nullable": True},
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
            version="2.0",
        )
    else:
        schema = ctx.openapi.build_schema(
            {
                "/payload": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {"application/json": {"schema": {"type": "boolean", "nullable": True}}},
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            }
        )
    schema = schemathesis.openapi.from_dict(schema)
    if request.param == "aiohttp":
        base_url = request.getfixturevalue("base_url")
        schema.config.update(base_url=base_url)
        return schema
    schema.app = request.getfixturevalue("flask_app")
    schema.config.update(base_url="http://127.0.0.1/api")
    return schema


@pytest.mark.hypothesis_nested
@pytest.mark.operations("payload")
def test_null_body(api_schema):
    # When API operation expects `null` as payload

    @given(case=api_schema["/payload"]["POST"].as_strategy())
    @settings(max_examples=5, deadline=None)
    def test(case):
        assume(case.body is None)
        # Then it should be possible to send `null`
        response = case.call_and_validate()
        # And the application should return what was sent (`/payload` behaves this way)
        assert response.content.strip() == b"null"

    test()


@pytest.mark.operations("read_only")
def test_read_only(schema_url):
    # When API operation has `readOnly` properties
    schema = schemathesis.openapi.from_url(schema_url)

    @given(case=schema["/read_only"]["GET"].as_strategy())
    @settings(max_examples=1, deadline=None)
    def test(case):
        # Then `writeOnly` should not affect the response schema
        response = case.call_and_validate()
        assert "write" not in response.json()
        assert "read" in response.json()

    test()


@pytest.mark.operations("write_only")
def test_write_only(schema_url):
    # When API operation has `writeOnly` properties
    schema = schemathesis.openapi.from_url(schema_url)

    @given(case=schema["/write_only"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        # Then `writeOnly` should be used only in requests
        assert "write" in case.body
        assert "read" not in case.body
        # And `readOnly` should only occur in responses
        response = case.call_and_validate()
        assert "write" not in response.json()
        assert "read" in response.json()

    test()


@pytest.mark.parametrize("location", ["path", "query", "header", "cookie"])
def test_missing_content_and_schema(ctx, location):
    # When an Open API 3 parameter is missing `schema` & `content`
    schema = ctx.openapi.build_schema(
        {"/foo": {"get": {"parameters": [{"in": location, "name": "X-Foo", "required": True}]}}}
    )
    schema = schemathesis.openapi.from_dict(schema)

    @given(schema["/foo"]["GET"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        pass

    # Then the proper error should be shown
    with pytest.raises(
        InvalidSchema,
        match=f"Can not generate data for {location} parameter `X-Foo`! "
        "It should have either `schema` or `content` keywords defined",
    ):
        test()


@pytest.mark.operations("headers")
def test_ascii_codec_for_headers(openapi3_schema_url):
    schema = schemathesis.openapi.from_url(openapi3_schema_url)
    schema.config.generation.codec = "ascii"

    @given(case=schema["/headers"]["GET"].as_strategy())
    @settings(max_examples=50)
    def test(case):
        assert case.headers["X-Token"].isascii()

    test()


@pytest.mark.operations("headers")
def test_exclude_chars_and_no_x00_for_headers(openapi3_schema_url):
    schema = schemathesis.openapi.from_url(openapi3_schema_url)
    schema.config.generation.exclude_header_characters = "abc"
    schema.config.generation.allow_x00 = False

    @given(case=schema["/headers"]["GET"].as_strategy())
    @settings(max_examples=50)
    def test(case):
        assert "\x00" not in case.headers["X-Token"]
        assert all(ch not in case.headers["X-Token"] for ch in schema.config.generation.exclude_header_characters)

    test()


@pytest.mark.filterwarnings("error")
def test_parameter_with_boolean_true_schema(ctx, cli, openapi3_base_url, snapshot_cli):
    # When a parameter has a boolean true schema (accepts anything in OpenAPI 3.1)
    paths = {
        "/success": {
            "get": {
                "parameters": [
                    {
                        "name": "h",
                        "in": "header",
                        "schema": True,
                        "required": True,
                    },
                    {
                        "name": "q",
                        "in": "query",
                        "schema": True,
                        "required": True,
                    },
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    raw_schema = ctx.openapi.build_schema(paths, version="3.1.0")

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/success"]["GET"]

    # Then the optimized schema should handle boolean true correctly
    # Header parameters should default to string type for practical generation
    assert operation.headers[0].optimized_schema == {"type": "string"}
    assert operation.query[0].optimized_schema is True

    @given(case=operation.as_strategy())
    @settings(max_examples=5)
    def test_positive(case):
        assert "h" in case.headers
        assert isinstance(case.headers["h"], str)
        assert "q" in case.query

    test_positive()

    @given(case=operation.as_strategy(GenerationMode.NEGATIVE))
    @settings(max_examples=1, suppress_health_check=list(HealthCheck))
    def test_negative(case):
        pass

    test_negative()

    schema_path = ctx.openapi.write_schema(paths, version="3.1.0")

    assert (
        cli.run(str(schema_path), "--max-examples=1", f"--url={openapi3_base_url}", "--checks=not_a_server_error")
        == snapshot_cli
    )


def test_parameter_with_boolean_false_schema(ctx):
    # When a parameter has a boolean false schema (accepts nothing - unusual but valid)
    raw_schema = ctx.openapi.build_schema(
        {
            "/test": {
                "get": {
                    "parameters": [
                        {
                            "name": "impossible",
                            "in": "query",
                            "schema": False,  # Boolean false - accepts nothing
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )
    # Then it should load without crashing
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/test"]["GET"]
    parameter = operation.query[0]
    # The optimized schema should be preserved or handled
    assert parameter.optimized_schema is False


@pytest.mark.filterwarnings("error")
def test_request_body_with_boolean_true_schema(ctx, cli, openapi3_base_url, snapshot_cli):
    # When a request body has a boolean true schema
    paths = {
        "/payload": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": True,
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    raw_schema = ctx.openapi.build_schema(paths, version="3.1.0")

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/payload"]["POST"]

    # Then the optimized schema should handle boolean true correctly
    assert operation.body[0].optimized_schema is True

    @given(case=operation.as_strategy())
    @settings(max_examples=5)
    def test_positive(case):
        assert case.body is not NOT_SET

    test_positive()

    @given(case=operation.as_strategy(GenerationMode.NEGATIVE))
    @settings(max_examples=1)
    def test_negative(case):
        pass

    # It is not possible to generate data for a schema that accepts everything
    with pytest.raises((Unsatisfiable, FailedHealthCheck)):
        test_negative()

    schema_path = ctx.openapi.write_schema(paths, version="3.1.0")

    assert (
        cli.run(str(schema_path), "--max-examples=1", f"--url={openapi3_base_url}", "--checks=not_a_server_error")
        == snapshot_cli
    )


def test_parameter_type_detection(ctx, cli, openapi3_base_url, snapshot_cli):
    # See GH-3149
    schema_path = ctx.openapi.write_schema(
        {
            "/success": {
                "get": {
                    "parameters": [
                        {
                            "name": "longitude",
                            "in": "query",
                            "schema": {
                                "maximum": 180,
                            },
                        }
                    ]
                }
            }
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={openapi3_base_url}", "--checks=not_a_server_error", "--max-examples=5")
        == snapshot_cli
    )
