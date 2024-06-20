import datetime

import pytest
import yaml
from hypothesis import HealthCheck, assume, find, given, settings
from hypothesis.errors import NoSuchExample

import schemathesis
from schemathesis.exceptions import OperationSchemaError
from schemathesis.internal.copy import fast_deepcopy
from schemathesis.specs.openapi._hypothesis import get_default_format_strategies, is_valid_header

from .utils import as_param


@pytest.mark.parametrize("schema_name", ("simple_swagger.yaml", "simple_openapi.yaml"))
@pytest.mark.parametrize("type_", ("string", "integer", "array", "boolean", "number"))
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
    )
    # Then the generated test case should contain it in its `headers` attribute
    testdir.run_and_assert(passed=1)


@pytest.mark.parametrize("type_", ("string", "integer", "array", "object", "boolean", "number"))
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
    )
    # Then the generated test case should contain it in its `cookies` attribute
    testdir.run_and_assert(passed=1)


def test_body(testdir):
    # When parameter is specified for "body"
    testdir.make_test(
        """
@schema.parametrize(method="POST")
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
    )
    # Then the generated test case should contain it in its `body` attribute
    testdir.run_and_assert(passed=1)


def test_path(testdir):
    # When parameter is specified for "path"
    testdir.make_test(
        """
@schema.parametrize(endpoint="/users/{user_id}")
@settings(max_examples=3, deadline=None)
def test_(case):
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
    )
    # Then the generated test case should contain it its `path_parameters` attribute
    testdir.run_and_assert(passed=1)


def test_multiple_path_variables(testdir):
    # When there are multiple parameters for "path"
    testdir.make_test(
        """
@schema.parametrize(endpoint="/users/{user_id}/{event_id}")
@settings(max_examples=3, deadline=None)
def test_(case):
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
    )
    # Then the generated test case should contain them its `path_parameters` attribute
    testdir.run_and_assert(passed=1)


def test_form_data(testdir):
    # When parameter is specified for "formData"
    testdir.make_test(
        """
@schema.parametrize(method="POST")
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
    )
    # Then the generated test case should contain it in its `body` attribute
    testdir.run_and_assert(passed=1)


@pytest.fixture(params=["swagger", "openapi"])
def schema_spec(request):
    return request.param


@pytest.fixture
def base_schema(request, schema_spec):
    if schema_spec == "swagger":
        return fast_deepcopy(request.getfixturevalue("simple_schema"))
    if schema_spec == "openapi":
        return fast_deepcopy(request.getfixturevalue("simple_openapi"))


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
    assert case.operation.get_security_requirements() == ["api_key"]
    assert_str(case.{location}["api_key"])
    assert_requests_call(case)
        """,
        schema=schema,
    )
    # Then the generated test case should contain API key in a proper place
    testdir.run_and_assert(passed=1)


@pytest.fixture
def cookie_schema(simple_openapi):
    schema = fast_deepcopy(simple_openapi)
    components = schema.setdefault("components", {})
    components["securitySchemes"] = {"api_key": {"type": "apiKey", "name": "api_key", "in": "cookie"}}
    schema["security"] = [{"api_key": []}]
    return schema


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
    )
    # Then the generated test case should contain API key in a proper place
    testdir.run_and_assert(passed=1)


def _assert_parameter(schema, schema_spec, location, expected=None):
    # When security definition is defined as "apiKey"
    schema = schemathesis.from_dict(schema)
    if schema_spec == "swagger":
        operation = schema["/users"]["get"]
        expected = (
            expected
            if expected is not None
            else [{"in": location, "name": "api_key", "type": "string", "required": True}]
        )
    else:
        operation = schema["/query"]["get"]
        expected = (
            expected
            if expected is not None
            else [{"in": location, "name": "api_key", "schema": {"type": "string"}, "required": True}]
        )
    parameters = schema.security.get_security_definitions_as_parameters(
        schema.raw_schema, operation, schema.resolver, location
    )
    # Then it should be presented as a "string" parameter
    assert parameters == expected


def test_security_as_parameters_api_key(schema, schema_spec, location):
    _assert_parameter(schema, schema_spec, location)


def test_security_as_parameters_api_key_cookie(cookie_schema):
    _assert_parameter(cookie_schema, "openapi", "cookie")


def test_security_as_parameters_api_key_overridden(overridden_security_schema, schema_spec, location):
    _assert_parameter(overridden_security_schema, schema_spec, location, [])


@pytest.fixture()
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


@pytest.fixture()
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
    )
    # Then the generated data should contain a valid "Authorization" header
    testdir.run_and_assert(passed=1)


def test_security_definitions_bearer_auth(testdir, simple_openapi):
    # When schema is using HTTP Bearer Auth scheme
    schema = fast_deepcopy(simple_openapi)
    components = schema.setdefault("components", {})
    components["securitySchemes"] = {"bearer_auth": {"type": "http", "scheme": "bearer"}}
    schema["security"] = [{"bearer_auth": []}]
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
        schema=schema,
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
        validate_schema=False,
    )
    # Then the generated test ignores this parameter
    testdir.run_and_assert(passed=1)


@pytest.mark.hypothesis_nested
def test_date_deserializing(testdir):
    # When dates in schema are written without quotes (achieved by dumping the schema with date instances)
    schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
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
    }

    schema_path = testdir.makefile(".yaml", schema=yaml.dump(schema))
    # Then yaml loader should ignore it
    # And data generation should work without errors
    schema = schemathesis.from_path(str(schema_path))

    @given(case=schema["/teapot"]["GET"].as_strategy())
    @settings(suppress_health_check=[HealthCheck.filter_too_much])
    def test(case):
        assert isinstance(case.query["key"], str)

    test()


def test_get_request_with_body(testdir, schema_with_get_payload):
    # When schema defines a payload for GET request
    # And schema validation is turned on
    testdir.make_test(
        """
@schema.parametrize()
def test_(case):
    pass
        """,
        schema=schema_with_get_payload,
    )
    # Then an error should be propagated with a relevant error message
    result = testdir.run_and_assert(failed=1)
    result.stdout.re_match_lines([r"E +BodyInGetRequestError: GET requests should not contain body parameters."])


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
    )
    # Then the payload should be serialized as json
    testdir.run_and_assert(passed=1)


def test_nullable_body_behind_a_reference(empty_open_api_2_schema):
    # When a body parameter is nullable and is behind a reference
    empty_open_api_2_schema["parameters"] = {
        "Foo": {
            "in": "body",
            "name": "payload",
            "required": True,
            "schema": {"type": "string"},
            "x-nullable": True,
        }
    }
    empty_open_api_2_schema["paths"] = {
        "/payload": {
            "post": {
                "parameters": [{"$ref": "#/parameters/Foo"}],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    # Then it should be properly collected
    schema = schemathesis.from_dict(empty_open_api_2_schema)
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
def api_schema(request, openapi_version):
    if openapi_version.is_openapi_2:
        schema = request.getfixturevalue("empty_open_api_2_schema")
        schema["paths"] = {
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
        }
    else:
        schema = request.getfixturevalue("empty_open_api_3_schema")
        schema["paths"] = {
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
    if request.param == "aiohttp":
        base_url = request.getfixturevalue("base_url")
        return schemathesis.from_dict(schema, base_url=base_url)
    app = request.getfixturevalue("flask_app")
    return schemathesis.from_dict(schema, app=app, base_url="/api")


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
        if case.app is None:
            data = response.content
        else:
            data = response.data.strip()
        # And the application should return what was sent (`/payload` behaves this way)
        assert data == b"null"

    test()


@pytest.mark.operations("read_only")
def test_read_only(schema_url):
    # When API operation has `readOnly` properties
    schema = schemathesis.from_uri(schema_url)

    @given(case=schema["/read_only"]["GET"].as_strategy())
    @settings(max_examples=1, deadline=None)
    def test(case):
        # Then `writeOnly` should not affect the response schema
        response = case.call_and_validate()
        assert "write" not in response.json()

    test()


@pytest.mark.operations("write_only")
def test_write_only(schema_url):
    # When API operation has `writeOnly` properties
    schema = schemathesis.from_uri(schema_url)

    @given(case=schema["/write_only"]["POST"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        # Then `writeOnly` should be used only in requests
        assert "write" in case.body
        assert "read" not in case.body
        # And `readOnly` should only occur in responses
        response = case.call_and_validate()
        assert "write" not in response.json()

    test()


@pytest.mark.parametrize("location", ("path", "query", "header", "cookie"))
def test_missing_content_and_schema(empty_open_api_3_schema, location):
    # When an Open API 3 parameter is missing `schema` & `content`
    empty_open_api_3_schema["paths"] = {
        "/foo": {"get": {"parameters": [{"in": location, "name": "X-Foo", "required": True}]}}
    }
    schema = schemathesis.from_dict(empty_open_api_3_schema, validate_schema=False)

    @given(schema["/foo"]["GET"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        pass

    # Then the proper error should be shown
    with pytest.raises(
        OperationSchemaError,
        match=f'Can not generate data for {location} parameter "X-Foo"! '
        "It should have either `schema` or `content` keywords defined",
    ):
        test()
