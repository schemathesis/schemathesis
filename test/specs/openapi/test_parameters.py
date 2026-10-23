import base64
import datetime
import re

import jsonschema_rs
import pytest
from flask import Flask, jsonify, request
from hypothesis import HealthCheck, assume, find, given, settings
from hypothesis.errors import FailedHealthCheck, NoSuchExample, Unsatisfiable

import schemathesis
from schemathesis.core import NOT_SET
from schemathesis.core.errors import InvalidSchema, RejectedSchemaDefinition
from schemathesis.core.transforms import deepclone
from schemathesis.generation.hypothesis.examples import generate_many
from schemathesis.generation.modes import GenerationMode
from schemathesis.openapi.generation.filters import is_valid_header
from schemathesis.specs.openapi._hypothesis import get_default_format_strategies
from schemathesis.specs.openapi.adapter.security import ORIGINAL_SECURITY_TYPE_KEY
from test.utils import assert_requests_call, run_given


def _parameter(version, name, location, type_):
    if version == "2.0":
        return {"name": name, "in": location, "required": True, "type": type_}
    return {"name": name, "in": location, "required": True, "schema": {"type": type_}}


@pytest.mark.parametrize("version", ["2.0", "3.0.2"])
@pytest.mark.parametrize("type_", ["string", "integer", "array", "boolean", "number"])
def test_headers(ctx, version, type_):
    operation = ctx.openapi.load_operation(
        "GET /users",
        {"parameters": [_parameter(version, "X-Header", "header", type_)], "responses": {"200": {"description": "OK"}}},
        version=version,
    )

    @run_given(case=operation.as_strategy(), max_examples=3)
    def test(case):
        assert isinstance(case.headers["X-Header"], str)
        assert_requests_call(case)


@pytest.mark.parametrize("type_", ["string", "integer", "array", "object", "boolean", "number"])
def test_cookies(ctx, type_):
    parameter = _parameter("3.0.2", "token", "cookie", type_)
    # Exploded array/object cookies serialize to no value: one cookie name cannot carry repeated values.
    if type_ in ("array", "object"):
        parameter["explode"] = False
    operation = ctx.openapi.load_operation(
        "GET /users", {"parameters": [parameter], "responses": {"200": {"description": "OK"}}}
    )

    @run_given(case=operation.as_strategy(), max_examples=20)
    def test(case):
        assert isinstance(case.cookies["token"], str)
        assert_requests_call(case)


def test_cookie_names_are_case_sensitive(ctx):
    operation = ctx.openapi.load_operation(
        "GET /x",
        {
            "parameters": [
                {"name": "sid", "in": "cookie", "required": True, "schema": {"enum": ["lower"]}},
                {"name": "SID", "in": "cookie", "required": True, "schema": {"enum": ["upper"]}},
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    @run_given(case=operation.as_strategy(), max_examples=1)
    def test(case):
        assert case.cookies == {"sid": "lower", "SID": "upper"}


@pytest.mark.parametrize(
    ("label", "definition", "version", "is_expected"),
    [
        (
            "POST /users",
            {"parameters": [{"name": "id", "in": "body", "required": True, "schema": {"type": "integer"}}]},
            "2.0",
            lambda case: isinstance(case.body, int),
        ),
        (
            "GET /users/{user_id}",
            {"parameters": [{"name": "user_id", "required": True, "in": "path", "type": "integer"}]},
            "2.0",
            lambda case: isinstance(case.path_parameters["user_id"], int),
        ),
        (
            "GET /users/{user_id}/{event_id}",
            {
                "parameters": [
                    {"name": "user_id", "required": True, "in": "path", "type": "integer"},
                    {"name": "event_id", "required": True, "in": "path", "type": "integer"},
                ]
            },
            "2.0",
            lambda case: (
                isinstance(case.path_parameters["user_id"], int) and isinstance(case.path_parameters["event_id"], int)
            ),
        ),
        (
            "POST /users",
            {"parameters": [{"name": "status", "in": "formData", "required": True, "type": "string"}]},
            "2.0",
            lambda case: isinstance(case.body["status"], str),
        ),
        (
            "POST /users",
            {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/problem+json": {
                            "schema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}
                        }
                    },
                }
            },
            "3.0.2",
            lambda case: (
                case.as_transport_kwargs()["headers"]["Content-Type"] == "application/problem+json"
                and "key" in case.as_transport_kwargs()["json"]
            ),
        ),
    ],
    ids=["body", "path", "multiple-path-variables", "form-data", "json-media-type"],
)
def test_parameter_location_is_generated(ctx, label, definition, version, is_expected):
    operation = ctx.openapi.load_operation(
        label, {**definition, "responses": {"200": {"description": "OK"}}}, version=version
    )

    @run_given(case=operation.as_strategy(), max_examples=3)
    def test(case):
        assert is_expected(case), case
        assert_requests_call(case)


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


def test_security_definitions_api_key(ctx, schema, location):
    container = "headers" if location == "header" else location

    @run_given(case=ctx.openapi.from_full_schema(schema).as_strategy(), max_examples=1)
    def test(case):
        assert isinstance(getattr(case, container)["api_key"], str)
        assert_requests_call(case)


@pytest.fixture
def cookie_schema(simple_openapi):
    components = simple_openapi.setdefault("components", {})
    components["securitySchemes"] = {"api_key": {"type": "apiKey", "name": "api_key", "in": "cookie"}}
    simple_openapi["security"] = [{"api_key": []}]
    return simple_openapi


def test_security_definitions_api_key_cookie(ctx, cookie_schema):
    @run_given(case=ctx.openapi.from_full_schema(cookie_schema).as_strategy(), max_examples=1)
    def test(case):
        assert isinstance(case.cookies["api_key"], str)
        assert_requests_call(case)


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
    parameters = [param.definition for param in operation.security.iter_parameters() if param.location == location]
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


def test_security_definitions_override(ctx, overridden_security_schema, location):
    strategy = ctx.openapi.from_full_schema(overridden_security_schema).as_strategy()

    @run_given(case=strategy, max_examples=1)
    def test(case):
        assert "api_key" not in (case.headers or [])
        assert "api_key" not in (case.query or [])
        assert_requests_call(case)


@pytest.fixture
def basic_auth_schema(base_schema, schema_spec):
    if schema_spec == "swagger":
        base_schema["securityDefinitions"] = {"basic_auth": {"type": "basic"}}
    if schema_spec == "openapi":
        components = base_schema.setdefault("components", {})
        components["securitySchemes"] = {"basic_auth": {"type": "http", "scheme": "basic"}}
    base_schema["security"] = [{"basic_auth": []}]
    return base_schema


def test_security_definitions_basic_auth(ctx, basic_auth_schema):
    @run_given(case=ctx.openapi.from_full_schema(basic_auth_schema).as_strategy(), max_examples=10)
    def test(case):
        auth = case.headers["Authorization"]
        assert auth.startswith("Basic ")
        base64.b64decode(auth[6:])
        assert_requests_call(case)


def test_security_definitions_bearer_auth(ctx, simple_openapi):
    components = simple_openapi.setdefault("components", {})
    components["securitySchemes"] = {"bearer_auth": {"type": "http", "scheme": "bearer"}}
    simple_openapi["security"] = [{"bearer_auth": []}]

    @run_given(case=ctx.openapi.from_full_schema(simple_openapi).as_strategy(), max_examples=1)
    def test(case):
        assert case.headers["Authorization"].startswith("Bearer ")
        assert_requests_call(case)


def test_bearer_auth_valid_header():
    # When an HTTP Bearer Auth headers is generated
    # Then it should be a valid header
    # And no invalid headers should be generated
    strategy = get_default_format_strategies()["_bearer_auth"]
    with pytest.raises(NoSuchExample):
        find(strategy, lambda x: not is_valid_header({"x": x}))


def test_positive_bearer_tokens_are_ascii(ctx):
    operation = ctx.openapi.load_operation(
        "GET /x",
        {"responses": {"200": {"description": "OK"}}},
        components={"securitySchemes": {"bearer_auth": {"type": "http", "scheme": "bearer"}}},
        security=[{"bearer_auth": []}],
    )
    strategy = operation.as_strategy(generation_mode=GenerationMode.POSITIVE)
    with pytest.raises(NoSuchExample):
        find(strategy, lambda case: not case.headers["Authorization"].isascii())


def test_unknown_data(ctx):
    operation = ctx.openapi.load_operation(
        "GET /users",
        {
            "parameters": [{"name": "status", "in": "unknown", "required": True, "type": "string"}],
            "responses": {"200": {"description": "OK"}},
        },
        version="2.0",
    )

    @run_given(case=operation.as_strategy(), max_examples=1)
    def test(case):
        assert not case.query and not case.headers and not case.cookies, case


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


def test_nullable_body_behind_a_reference(ctx):
    # When a body parameter is nullable and is behind a reference
    operation = ctx.openapi.load_operation(
        "POST /payload",
        {
            "parameters": [{"$ref": "#/parameters/Foo"}],
            "responses": {"200": {"description": "OK"}},
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
    # And its definition is not transformed to JSON Schema
    assert operation.body[0].definition == {
        "in": "body",
        "name": "payload",
        "required": True,
        "schema": {"type": "string"},
        "x-nullable": True,
    }


@pytest.mark.hypothesis_nested
def test_null_body(ctx):
    api = ctx.openapi.apps.payload()
    # When API operation expects `null` as payload
    schema = ctx.openapi.load_schema(
        {
            "/api/payload": {
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
    schema.config.update(base_url=api.base_url)

    @run_given(case=schema["/api/payload"]["POST"].as_strategy(), max_examples=5)
    def test(case):
        assume(case.body is None)
        # Then it should be possible to send `null`
        response = case.call_and_validate()
        # And the application should return what was sent (`/api/payload` behaves this way)
        assert response.content.strip() == b"null"


def test_read_only(ctx):
    # When API operation has `readOnly` properties
    api = ctx.openapi.apps.read_only()
    schema = schemathesis.openapi.from_url(api.schema_url)

    @run_given(case=schema["/api/read_only"]["GET"].as_strategy(), max_examples=1)
    def test(case):
        # Then `writeOnly` should not affect the response schema
        response = case.call_and_validate()
        assert "write" not in response.json()
        assert "read" in response.json()


def test_write_only(ctx):
    # When API operation has `writeOnly` properties
    api = ctx.openapi.apps.write_only()
    schema = schemathesis.openapi.from_url(api.schema_url)

    @run_given(case=schema["/api/write_only"]["POST"].as_strategy(), max_examples=1)
    def test(case):
        # Then `writeOnly` should be used only in requests
        assert "write" in case.body
        assert "read" not in case.body
        # And `readOnly` should only occur in responses
        response = case.call_and_validate()
        assert "write" not in response.json()
        assert "read" in response.json()


READ_ONLY_ID = {"type": "integer", "readOnly": True}
NAME = {"type": "string"}


def _json_body(schema):
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": schema}}}}


def _keys(value):
    if isinstance(value, dict):
        yield from value
        for item in value.values():
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


@pytest.mark.parametrize(
    ("version", "definition", "components"),
    [
        pytest.param(
            "3.0.2",
            _json_body(
                {
                    "type": "object",
                    "properties": {"id": {"$ref": "#/components/schemas/Id"}, "name": NAME},
                    "required": ["name"],
                }
            ),
            {"schemas": {"Id": READ_ONLY_ID}},
            id="ref-to-read-only",
        ),
        pytest.param(
            "3.0.2",
            _json_body(
                {
                    "type": "object",
                    "properties": {"id": {"allOf": [{"$ref": "#/components/schemas/Id"}]}, "name": NAME},
                    "required": ["name"],
                }
            ),
            {"schemas": {"Id": READ_ONLY_ID}},
            id="all-of-ref",
        ),
        pytest.param(
            "3.0.2",
            _json_body(
                {
                    "type": "object",
                    "properties": {
                        "id": {"allOf": [{"$ref": "#/components/schemas/Id"}], "readOnly": False},
                        "name": NAME,
                    },
                    "required": ["name"],
                }
            ),
            {"schemas": {"Id": READ_ONLY_ID}},
            id="all-of-ref-with-writable-sibling",
        ),
        pytest.param(
            "3.0.2",
            _json_body(
                {"type": "object", "properties": {"id": {"allOf": [READ_ONLY_ID]}, "name": NAME}, "required": ["name"]}
            ),
            {},
            id="inline-all-of",
        ),
        pytest.param(
            "3.0.2",
            _json_body({"properties": {"id": READ_ONLY_ID, "name": NAME}, "required": ["name"]}),
            {},
            id="untyped-parent",
        ),
        pytest.param(
            "3.0.2",
            _json_body({"$ref": "#/components/schemas/Resource"}),
            {"schemas": {"Resource": {"properties": {"id": READ_ONLY_ID, "name": NAME}, "required": ["name"]}}},
            id="untyped-parent-behind-ref",
        ),
        pytest.param(
            "3.0.2",
            _json_body(
                {
                    "allOf": [
                        {"$ref": "#/components/schemas/Resource"},
                        {"properties": {"name": NAME}, "required": ["name"]},
                    ]
                }
            ),
            {"schemas": {"Resource": {"properties": {"id": READ_ONLY_ID}}}},
            id="all-of-untyped-base",
        ),
        pytest.param(
            "3.0.2",
            _json_body(
                {
                    "allOf": [{"$ref": "#/components/schemas/Resource"}],
                    "properties": {"name": NAME},
                    "required": ["id", "name"],
                }
            ),
            {"schemas": {"Resource": {"properties": {"id": READ_ONLY_ID}}}},
            id="required-in-parent-of-ref-base",
        ),
        pytest.param(
            "3.0.2",
            _json_body(
                {
                    "type": "object",
                    "properties": {"id": {"$ref": "#/components/schemas/Id"}, "name": NAME},
                    "required": ["id", "name"],
                }
            ),
            {"schemas": {"Id": READ_ONLY_ID}},
            id="required-ref",
        ),
        pytest.param(
            "3.0.2",
            _json_body(
                {
                    "type": "object",
                    "properties": {"id": {"$ref": "#/components/schemas/Id"}, "name": NAME},
                    "required": ["name"],
                }
            ),
            {"schemas": {"Id": {**READ_ONLY_ID, "nullable": True}}},
            id="nullable-ref-target",
        ),
        pytest.param(
            "3.0.2",
            _json_body(
                {
                    "type": "object",
                    "properties": {
                        "name": NAME,
                        "owner": {"$ref": "#/components/schemas/Id"},
                        "child": {"$ref": "#/components/schemas/Child"},
                    },
                    "required": ["name", "child"],
                }
            ),
            {
                "schemas": {
                    "Id": {**READ_ONLY_ID, "nullable": True},
                    "Child": {
                        "properties": {"id": {"$ref": "#/components/schemas/Id"}, "name": NAME},
                        "required": ["name"],
                    },
                }
            },
            id="nullable-ref-target-shared",
        ),
        pytest.param(
            "3.0.2",
            _json_body(
                {
                    "type": "object",
                    "properties": {
                        "name": NAME,
                        "child": {
                            "properties": {"id": {"$ref": "#/components/schemas/Id"}, "name": NAME},
                            "required": ["name"],
                        },
                    },
                    "required": ["name", "child"],
                }
            ),
            {"schemas": {"Id": READ_ONLY_ID}},
            id="nested-object",
        ),
        pytest.param(
            "3.0.2",
            _json_body(
                {
                    "type": "object",
                    "properties": {
                        "name": NAME,
                        "items": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "properties": {"id": {"$ref": "#/components/schemas/Id"}, "name": NAME},
                                "required": ["name"],
                            },
                        },
                    },
                    "required": ["name", "items"],
                }
            ),
            {"schemas": {"Id": READ_ONLY_ID}},
            id="array-items",
        ),
        pytest.param(
            "2.0",
            {
                "parameters": [
                    {"in": "body", "name": "body", "required": True, "schema": {"$ref": "#/definitions/Resource"}}
                ]
            },
            None,
            id="swagger-2.0",
        ),
        pytest.param(
            "3.1.0",
            _json_body(
                {
                    "type": "object",
                    "properties": {"id": {"$ref": "#/components/schemas/Id"}, "name": NAME},
                    "required": ["name"],
                }
            ),
            {"schemas": {"Id": READ_ONLY_ID}},
            id="openapi-3.1-ref",
        ),
        pytest.param(
            "3.1.0",
            _json_body(
                {
                    "type": "object",
                    "properties": {
                        "id": {"anyOf": [{"$ref": "#/components/schemas/Id"}, {"type": "null"}]},
                        "name": NAME,
                    },
                    "required": ["name"],
                }
            ),
            {"schemas": {"Id": READ_ONLY_ID}},
            id="openapi-3.1-nullable-ref",
        ),
    ],
)
def test_read_only_properties_are_not_generated_in_request_bodies(ctx, version, definition, components):
    kwargs = {"definitions": {"Resource": {"properties": {"id": READ_ONLY_ID, "name": NAME}, "required": ["name"]}}}
    if components is not None:
        kwargs = {"components": components}
    operation = ctx.openapi.load_operation(
        "POST /items", {**definition, "responses": {"200": {"description": "OK"}}}, version=version, **kwargs
    )
    bodies = [case.body for case in generate_many(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), 30)]
    assert any(isinstance(body, dict) and "name" in body for body in bodies)
    for body in bodies:
        assert "id" not in set(_keys(body)), f"Read-only field sent: {body!r}"


@pytest.mark.parametrize("required", [True, "id"], ids=["boolean", "string"])
@pytest.mark.parametrize("behind_ref", [False, True], ids=["inline", "ref"])
def test_read_only_property_under_malformed_required_is_reported_as_schema_error(ctx, required, behind_ref):
    resource = {"properties": {"id": READ_ONLY_ID, "name": NAME}, "required": required}
    operation = ctx.openapi.load_operation(
        "POST /items",
        {
            "parameters": [
                {
                    "in": "body",
                    "name": "body",
                    "required": True,
                    "schema": {"$ref": "#/definitions/Resource"} if behind_ref else resource,
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
        version="2.0",
        definitions={"Resource": resource},
    )
    with pytest.raises(RejectedSchemaDefinition, match="Invalid `required` definition"):
        generate_many(operation.as_strategy(), 1)


@pytest.mark.parametrize(
    "read_only",
    ["true", 1],
    ids=["string", "integer"],
)
def test_truthy_read_only_flag_is_honored(ctx, read_only):
    operation = ctx.openapi.load_operation(
        "POST /items",
        _json_body(
            {
                "type": "object",
                "properties": {"id": {"type": "integer", "readOnly": read_only}, "name": NAME},
                "required": ["name"],
            }
        )
        | {"responses": {"200": {"description": "OK"}}},
    )
    for case in generate_many(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), 30):
        assert "id" not in case.body, f"Read-only field sent: {case.body!r}"


@pytest.mark.timeout(30)
def test_read_only_property_in_self_referencing_all_of(ctx):
    operation = ctx.openapi.load_operation(
        "POST /items",
        _json_body(
            {
                "allOf": [{"$ref": "#/components/schemas/Node"}],
                "properties": {"name": NAME, "child": {"allOf": [{"$ref": "#/components/schemas/Node"}]}},
                "required": ["id", "name"],
            }
        )
        | {"responses": {"200": {"description": "OK"}}},
        components={
            "schemas": {"Node": {"allOf": [{"$ref": "#/components/schemas/Node"}], "properties": {"id": READ_ONLY_ID}}}
        },
    )
    # Checked outside generation, which would swallow the timeout.
    assert not operation.body[0].is_valid({"name": "x", "child": {"id": 1}})
    bodies = [case.body for case in generate_many(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), 30)]
    assert any(isinstance(body, dict) and "name" in body for body in bodies)
    for body in bodies:
        assert "id" not in set(_keys(body)), f"Read-only field sent: {body!r}"


def test_read_only_property_is_sent_where_another_media_type_keeps_it_writable(ctx):
    operation = ctx.openapi.load_operation(
        "POST /items",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"id": READ_ONLY_ID, "name": NAME},
                            "required": ["name"],
                        }
                    },
                    "application/x-www-form-urlencoded": {
                        "schema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}
                    },
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )
    bodies = {
        case.media_type: case.body
        for case in generate_many(operation.as_strategy(generation_mode=GenerationMode.POSITIVE), 30)
    }
    assert "id" not in bodies["application/json"]
    assert "id" in bodies["application/x-www-form-urlencoded"]


@pytest.mark.parametrize("location", ["path", "query", "header", "cookie"])
def test_missing_content_and_schema(ctx, location):
    # When an Open API 3 parameter is missing `schema` & `content`
    operation = ctx.openapi.load_operation(
        "GET /foo", {"parameters": [{"in": location, "name": "X-Foo", "required": True}]}
    )

    @given(operation.as_strategy())
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


@pytest.mark.parametrize(
    ("version", "location"),
    [("3.0.2", "query"), ("3.2.0", "querystring")],
    ids=["openapi-3.0-query", "openapi-3.2-querystring"],
)
def test_empty_parameter_content(ctx, version, location):
    operation = ctx.openapi.load_operation(
        "GET /x",
        {
            "parameters": [{"name": "q", "in": location, "required": True, "content": {}}],
            "responses": {"200": {"description": "OK"}},
        },
        version=version,
    )

    @given(case=operation.as_strategy())
    @settings(max_examples=1)
    def test(case):
        pass

    with pytest.raises(
        InvalidSchema,
        match=f"Can not generate data for {location} parameter `q`! Its `content` must contain exactly one entry",
    ):
        test()


def test_non_object_parameter_media_type(ctx):
    operation = ctx.openapi.load_operation(
        "GET /x",
        {
            "parameters": [{"name": "a", "in": "query", "required": True, "content": {"application/json": "oops"}}],
            "responses": {"200": {"description": "OK"}},
        },
    )

    @given(case=operation.as_strategy())
    @settings(max_examples=1)
    def test(case):
        pass

    with pytest.raises(InvalidSchema, match="Media type `application/json` for query parameter `a` must be an object"):
        test()


def test_ascii_codec_for_headers(ctx):
    api = ctx.openapi.apps.headers()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.generation.codec = "ascii"

    @run_given(case=schema["/api/headers"]["GET"].as_strategy(), max_examples=50)
    def test(case):
        assert case.headers["X-Token"].isascii()


def test_exclude_chars_and_no_x00_for_headers(ctx):
    api = ctx.openapi.apps.headers()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.generation.exclude_header_characters = "abc"
    schema.config.generation.allow_x00 = False

    @run_given(case=schema["/api/headers"]["GET"].as_strategy(), max_examples=50)
    def test(case):
        assert "\x00" not in case.headers["X-Token"]
        assert all(ch not in case.headers["X-Token"] for ch in schema.config.generation.exclude_header_characters)


def test_parameter_with_boolean_true_schema(ctx, cli, snapshot_cli):
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
    schema = ctx.openapi.load_schema(paths, version="3.1.0")
    operation = schema["/success"]["GET"]

    # Then the optimized schema should handle boolean true correctly
    # Header parameters should default to string type for practical generation
    assert operation.headers[0].optimized_schema == {"type": "string"}
    assert operation.query[0].optimized_schema is True

    @run_given(case=operation.as_strategy(), max_examples=5)
    def test_positive(case):
        assert "h" in case.headers
        assert isinstance(case.headers["h"], str)
        assert "q" in case.query

    @run_given(case=operation.as_strategy(GenerationMode.NEGATIVE), max_examples=1)
    def test_negative(case):
        pass

    schema_path = ctx.openapi.write_schema(paths, version="3.1.0")
    api = ctx.openapi.apps.success()

    assert (
        cli.run(str(schema_path), "--max-examples=1", f"--url={api.base_url}/api", "--checks=not_a_server_error")
        == snapshot_cli
    )


def test_parameter_with_boolean_false_schema(ctx):
    # When a parameter has a boolean false schema (accepts nothing - unusual but valid)
    operation = ctx.openapi.load_operation(
        "GET /test",
        {
            "parameters": [
                {
                    "name": "impossible",
                    "in": "query",
                    "schema": False,  # Boolean false - accepts nothing
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )
    # Then it should load without crashing
    parameter = operation.query[0]
    # The optimized schema should be preserved or handled
    assert parameter.optimized_schema is False


def test_request_body_with_boolean_true_schema(ctx, cli, snapshot_cli):
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
    schema = ctx.openapi.load_schema(paths, version="3.1.0")
    operation = schema["/payload"]["POST"]

    # Then the optimized schema should handle boolean true correctly
    assert operation.body[0].optimized_schema is True

    @run_given(case=operation.as_strategy(), max_examples=5)
    def test_positive(case):
        assert case.body is not NOT_SET

    @given(case=operation.as_strategy(GenerationMode.NEGATIVE))
    @settings(max_examples=1, suppress_health_check=list(HealthCheck))
    def test_negative(case):
        pass

    # It is not possible to generate data for a schema that accepts everything
    with pytest.raises((Unsatisfiable, FailedHealthCheck)):
        test_negative()

    app, _ = ctx.openapi.make_flask_app(paths, version="3.1.0")

    @app.route("/payload", methods=["POST"])
    def payload():
        return jsonify({}), 200

    assert cli.run_openapi_app(app, "--max-examples=1", "--checks=not_a_server_error") == snapshot_cli


def test_optimized_schema_untouched_under_draft_2020_12(ctx):
    # A 3.1 array schema reaches generation as written; respelling it in Draft 4 vocabulary would fail
    # 2020-12 canonicalization and silently disable the canonical engine for it.
    array_schema = {
        "type": "array",
        "prefixItems": [{"type": "integer"}],
        "items": {"type": "string"},
        "allOf": [{"contains": {"const": "A"}}, {"contains": {"const": "B"}}],
    }
    operation = ctx.openapi.load_operation(
        "POST /payload",
        {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": array_schema}},
            },
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )

    assert operation.body[0].optimized_schema == array_schema


def test_parameter_type_detection(ctx, cli, snapshot_cli):
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
    api = ctx.openapi.apps.success()
    assert (
        cli.run(str(schema_path), f"--url={api.base_url}/api", "--checks=not_a_server_error", "--max-examples=5")
        == snapshot_cli
    )


def test_yaml_boolean_parameter_name(tmp_path, app_runner, cli):
    # YAML 1.1 reads an unquoted `on` as a boolean, but the API still expects the query key `on`.
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text(
        """
openapi: 3.0.2
info:
  title: Example
  version: 1.0.0
paths:
  /foo:
    get:
      parameters:
        - in: query
          name: on
          required: true
          schema:
            type: string
      responses:
        '200':
          description: OK
"""
    )
    received = []
    app = Flask(__name__)

    @app.route("/foo")
    def foo():
        received.append(sorted(request.args))
        return jsonify({})

    port = app_runner.run_flask_app(app)
    cli.run(str(schema_path), f"--url=http://127.0.0.1:{port}", "--max-examples=1", "--checks=not_a_server_error")

    assert {key for keys in received for key in keys} == {"on"}


def test_non_string_parameter_name(ctx):
    # Specs converted from YAML 1.1 carry `name: on` as the JSON boolean `true`; the name reaches
    # the wire as text either way.
    operation = ctx.openapi.load_operation(
        "GET /foo",
        {
            "parameters": [{"in": "query", "name": True, "required": True, "schema": {"type": "string"}}],
            "responses": {"200": {"description": "OK"}},
        },
    )

    @run_given(case=operation.as_strategy(), max_examples=1)
    def test(case):
        assert set(case.query) == {"true"}


@pytest.mark.parametrize("location", ["query", "body"])
@pytest.mark.parametrize("mode", list(GenerationMode), ids=[mode.value for mode in GenerationMode])
def test_non_string_property_name(ctx, location, mode):
    # Specs loaded with a YAML 1.1 parser carry a property named `on` as the boolean key `True`.
    object_schema = {
        "type": "object",
        "properties": {True: {"type": "string", "enum": ["ALPHA"]}, 200: {"type": "string", "enum": ["BETA"]}},
        "required": ["true", "200"],
        "additionalProperties": False,
    }
    if location == "query":
        operation = {"parameters": [{"in": "query", "name": "key", "required": True, "schema": object_schema}]}
    else:
        operation = {
            "requestBody": {"required": True, "content": {"application/json": {"schema": object_schema}}},
        }
    schema = ctx.openapi.load_schema({"/foo": {"post": {**operation, "responses": {"200": {"description": "OK"}}}}})

    @run_given(case=schema["/foo"]["POST"].as_strategy(generation_mode=mode), max_examples=1)
    def test(case):
        if mode == GenerationMode.POSITIVE:
            assert (case.query if location == "query" else case.body) == {"true": "ALPHA", "200": "BETA"}


def test_yaml_file_with_non_string_property_name():
    schema = schemathesis.openapi.from_file(
        """
openapi: 3.0.2
info: {title: Test, version: "1.0"}
paths:
  /foo:
    post:
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                on: {type: string, enum: [ALPHA]}
                200: {type: string, enum: [BETA]}
              required: ["on", "200"]
              additionalProperties: false
      responses:
        "200": {description: OK}
"""
    )

    @run_given(case=schema["/foo"]["POST"].as_strategy(generation_mode=GenerationMode.POSITIVE), max_examples=1)
    def test(case):
        assert case.body == {"on": "ALPHA", "200": "BETA"}


def test_non_string_path_parameter_name_recovered_from_template(ctx):
    # The template is the only place the original name survives, and its schema must still drive the value.
    operation = ctx.openapi.load_operation(
        "GET /foo/{on}",
        {
            "parameters": [
                {"in": "path", "name": True, "required": True, "schema": {"type": "string", "enum": ["ALPHA"]}}
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    @run_given(case=operation.as_strategy(), max_examples=1)
    def test(case):
        assert case.path_parameters == {"on": "ALPHA"}
        assert case.formatted_path == "/foo/ALPHA"


def test_non_string_path_parameter_name_kept_when_placeholders_are_ambiguous(ctx):
    operation = ctx.openapi.load_operation(
        "GET /foo/{on}/{off}",
        {
            "parameters": [
                {"in": "path", "name": True, "required": True, "schema": {"type": "string", "enum": ["ALPHA"]}}
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    assert sorted(parameter.name for parameter in operation.path_parameters) == ["off", "on", "true"]


@pytest.mark.parametrize(
    ("version", "path_item_parameter", "operation_parameter"),
    [
        pytest.param(
            "3.0.2",
            {"in": "query", "name": "Action", "schema": {"type": "string"}},
            {"in": "query", "name": "Action", "required": True, "schema": {"type": "string", "enum": ["CancelJob"]}},
            id="openapi-3",
        ),
        pytest.param(
            "2.0",
            {"in": "query", "name": "Action", "type": "string"},
            {"in": "query", "name": "Action", "required": True, "type": "string", "enum": ["CancelJob"]},
            id="swagger-2",
        ),
    ],
)
def test_operation_parameter_overrides_path_item_parameter(ctx, version, path_item_parameter, operation_parameter):
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "parameters": [path_item_parameter],
                "get": {"parameters": [operation_parameter], "responses": {"200": {"description": "OK"}}},
            }
        },
        version=version,
    )
    operation = schema["/foo"]["GET"]
    assert [parameter.definition for parameter in operation.query] == [operation_parameter]

    @run_given(case=operation.as_strategy(), max_examples=5)
    def test(case):
        assert case.query == {"Action": "CancelJob"}


# Overriding replaces the whole declaration, so a path item's required parameter turns optional.
@pytest.mark.parametrize(
    ("version", "path_item_parameter", "operation_parameter"),
    [
        pytest.param(
            "3.0.2",
            {"in": "query", "name": "Action", "required": True, "schema": {"type": "string"}},
            {"in": "query", "name": "Action", "schema": {"type": "string", "enum": ["CancelJob"]}},
            id="openapi-3",
        ),
        pytest.param(
            "2.0",
            {"in": "query", "name": "Action", "required": True, "type": "string"},
            {"in": "query", "name": "Action", "type": "string", "enum": ["CancelJob"]},
            id="swagger-2",
        ),
    ],
)
def test_operation_parameter_override_relaxes_required(ctx, version, path_item_parameter, operation_parameter):
    schema = ctx.openapi.load_schema(
        {
            "/foo": {
                "parameters": [path_item_parameter],
                "get": {"parameters": [operation_parameter], "responses": {"200": {"description": "OK"}}},
            }
        },
        version=version,
    )
    assert schema["/foo"]["GET"].query.schema == {
        "properties": {"Action": {"type": "string", "enum": ["CancelJob"]}},
        "additionalProperties": False,
        "type": "object",
    }


@pytest.fixture
def schema_with_referenced_body(ctx):
    return ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Item"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "schemas": {
                "Item": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, "tag": {"type": "string", "nullable": True}},
                    "required": ["id"],
                }
            }
        },
    )


def test_body_validate_accepts_conforming_value(schema_with_referenced_body):
    body = schema_with_referenced_body["/items"]["POST"].body[0]
    assert body.validate({"id": 1, "tag": None}) is None
    assert body.is_valid({"id": 1, "tag": None})


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"id": "1"}, '"1" is not of type "integer"'),
        ({"tag": "x"}, '"id" is a required property'),
    ],
    ids=["wrong-type", "missing-required"],
)
def test_body_validate_rejects_violating_value(schema_with_referenced_body, value, message):
    body = schema_with_referenced_body["/items"]["POST"].body[0]
    with pytest.raises(jsonschema_rs.ValidationError, match=re.escape(message)):
        body.validate(value)
    assert not body.is_valid(value)


def test_query_parameter_validate(ctx):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1}}],
            "responses": {"200": {"description": "OK"}},
        },
    )
    limit = operation.query[0]
    assert limit.validate(5) is None
    with pytest.raises(jsonschema_rs.ValidationError):
        limit.validate(0)


@pytest.mark.parametrize(
    "parameter_schema",
    [{"type": "boolean"}, {"type": "string", "enum": ["asc"]}, {"type": "string", "minLength": 1}],
    ids=["boolean", "enum", "min-length"],
)
@pytest.mark.parametrize("allow_empty_value", [True, False, None], ids=["allowed", "forbidden", "default"])
def test_query_parameter_allow_empty_value(ctx, parameter_schema, allow_empty_value):
    parameter = {"name": "filter", "in": "query", "required": True, "schema": parameter_schema}
    if allow_empty_value is not None:
        parameter["allowEmptyValue"] = allow_empty_value
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [parameter],
            "responses": {"200": {"description": "OK"}},
        },
    )
    query = operation.query[0]

    assert query.is_valid("") is (allow_empty_value is True)


def test_query_parameter_allow_empty_value_with_recursive_reference(ctx):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {
                    "name": "filter",
                    "in": "query",
                    "required": True,
                    "allowEmptyValue": True,
                    "schema": {"$ref": "#/components/schemas/Node"},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
        components={
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {"child": {"$ref": "#/components/schemas/Node"}},
                    "additionalProperties": False,
                }
            }
        },
    )
    query = operation.query[0]

    assert [query.is_valid(value) for value in ("", {}, {"child": {"child": {}}}, "x")] == [True, True, True, False]


@pytest.mark.parametrize("location", ["query", "formData"])
@pytest.mark.parametrize("allow_empty_value", [True, False, None], ids=["allowed", "forbidden", "default"])
def test_swagger_parameter_allow_empty_value(ctx, location, allow_empty_value):
    parameter = {"name": "filter", "in": location, "required": True, "type": "boolean"}
    if allow_empty_value is not None:
        parameter["allowEmptyValue"] = allow_empty_value
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [parameter],
            "responses": {"200": {"description": "OK"}},
        },
        version="2.0",
        consumes=["application/x-www-form-urlencoded"],
    )

    if location == "query":
        is_valid = operation.query[0].is_valid("")
    else:
        is_valid = operation.body[0].is_valid({"filter": ""})
    assert is_valid is (allow_empty_value is True)


# An empty array serializes to nothing, so generation raises `minItems`; the contract still admits it.
def test_validate_ignores_serialization_bounds(ctx):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {
                    "name": "tags",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "array", "items": {"type": "string"}},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    tags = operation.query[0]
    assert tags.wire_bounds == {"minItems": 1}
    assert tags.is_valid([])


ITEM_SCHEMA = {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}


def item_body(media_type="application/json"):
    return {
        "requestBody": {
            "required": True,
            "content": {media_type: {"schema": {"$ref": "#/components/schemas/Item"}}},
        },
        "responses": {"200": {"description": "OK"}},
    }


def load_item_schema(ctx, second_media_type="application/json"):
    return ctx.openapi.load_schema(
        {"/a": {"post": item_body()}, "/b": {"post": item_body(second_media_type)}},
        components={"schemas": {"Item": ITEM_SCHEMA}},
    )


def test_operations_with_the_same_request_body_share_its_converted_schemas(ctx):
    schema = load_item_schema(ctx)
    first, second = (schema[path]["POST"].body[0] for path in ("/a", "/b"))

    assert (first.optimized_schema is second.optimized_schema, first.validation_schema is second.validation_schema) == (
        True,
        True,
    )


def test_request_body_changed_by_a_hook_keeps_its_own_converted_schema(ctx):
    schema = load_item_schema(ctx)

    @schema.hook
    def before_init_operation(context, operation):
        if operation.path == "/a":
            operation.body[0].definition["schema"] = {"type": "object", "properties": {"id": {"type": "string"}}}

    first, second = (schema[path]["POST"].body[0] for path in ("/a", "/b"))

    assert (first.optimized_schema["properties"]["id"], second.optimized_schema["properties"]["id"]) == (
        {"type": "string"},
        {"type": "integer"},
    )


def test_form_and_json_request_bodies_do_not_share_converted_schemas(ctx):
    schema = load_item_schema(ctx, second_media_type="application/x-www-form-urlencoded")
    first, second = (schema[path]["POST"].body[0] for path in ("/a", "/b"))

    assert first.optimized_schema is not second.optimized_schema


def test_generating_request_bodies_leaves_the_shared_schemas_unchanged(ctx):
    schema = load_item_schema(ctx)
    first, second = (schema[path]["POST"] for path in ("/a", "/b"))
    body = first.body[0]
    shared = (body.optimized_schema, body.validation_schema)
    before = deepclone(shared)

    for operation in (first, second):
        for mode in (GenerationMode.POSITIVE, GenerationMode.NEGATIVE):
            generate_many(operation.as_strategy(generation_mode=mode), 5)

    assert shared == before


# Only recursive references keep named components in a bundle.
TREE_SCHEMA = {
    "type": "object",
    "properties": {"children": {"type": "array", "items": {"$ref": "#/components/schemas/Item"}}},
}


def test_request_bodies_share_converted_schemas_across_other_bundled_parameters(ctx):
    filter_parameter = {"name": "filter", "in": "query", "schema": {"$ref": "#/components/schemas/Filter"}}
    schema = ctx.openapi.load_schema(
        {
            "/a": {"post": item_body()},
            "/b": {"get": {"parameters": [filter_parameter], "responses": {"200": {"description": "OK"}}}},
            "/c": {"post": item_body()},
        },
        components={
            "schemas": {
                "Item": TREE_SCHEMA,
                "Filter": {"type": "object", "properties": {"next": {"$ref": "#/components/schemas/Filter"}}},
            }
        },
    )
    operations = [result.ok() for result in schema.get_all_operations()]
    first, second = (operation.body[0] for operation in operations if operation.method == "post")

    assert first.optimized_schema is second.optimized_schema


def test_converted_schema_changed_by_a_hook_stays_with_its_operation(ctx):
    schema = load_item_schema(ctx)

    @schema.hook
    def before_init_operation(context, operation):
        if operation.path == "/a":
            operation.body[0].optimized_schema["properties"]["id"]["type"] = "string"

    first, second = (schema[path]["POST"].body[0] for path in ("/a", "/b"))

    assert (first.optimized_schema["properties"]["id"], second.optimized_schema["properties"]["id"]) == (
        {"type": "string"},
        {"type": "integer"},
    )


def test_swagger_body_parameters_share_converted_schemas_across_other_bundled_parameters(ctx):
    def body_operation():
        return {
            "consumes": ["application/json"],
            "parameters": [{"name": "body", "in": "body", "required": True, "schema": {"$ref": "#/definitions/Item"}}],
            "responses": {"200": {"description": "OK"}},
        }

    filter_parameter = {"name": "filter", "in": "body", "schema": {"$ref": "#/definitions/Filter"}}
    schema = ctx.openapi.load_schema(
        {
            "/a": {"post": body_operation()},
            "/b": {"put": {"parameters": [filter_parameter], "responses": {"200": {"description": "OK"}}}},
            "/c": {"post": body_operation()},
        },
        version="2.0",
        definitions={
            "Item": {
                "type": "object",
                "properties": {"children": {"type": "array", "items": {"$ref": "#/definitions/Item"}}},
            },
            "Filter": {"type": "object", "properties": {"next": {"$ref": "#/definitions/Filter"}}},
        },
    )
    operations = [result.ok() for result in schema.get_all_operations()]
    first, second = (operation.body[0] for operation in operations if operation.method == "post")

    assert first.optimized_schema is second.optimized_schema
