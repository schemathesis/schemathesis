import pytest
from flask import jsonify
from hypothesis import given, settings

import schemathesis
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.parameters import SkippedParameter
from schemathesis.core.result import Err, Ok
from schemathesis.specs.openapi.adapter.responses import ResolvedSchema


def _schema(servers):
    return {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1"},
        "servers": servers,
        "paths": {"/x": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }


@pytest.mark.parametrize(
    "servers",
    [
        [{}],
        [{"url": None}],
        [{"url": 42}],
        [{"url": "{var}", "variables": None}],
        [{"url": "{var}", "variables": {"var": "x"}}],
        [{"url": "{var}", "variables": {"var": {}}}],
        [None],
        ["http://x"],
        "not-a-list",
        {"url": "http://x"},
        [{"url": "http://x/{undefined}"}],
    ],
    ids=[
        "missing_url",
        "url_none",
        "url_non_string",
        "variables_none",
        "variables_string_value",
        "variable_missing_default",
        "server_none",
        "server_string",
        "servers_string",
        "servers_dict",
        "url_undefined_variable",
    ],
)
def test_invalid_servers_v3(servers):
    with pytest.raises(InvalidSchema):
        schema = schemathesis.openapi.from_dict(_schema(servers))
        assert schema.base_path


def _swagger_schema(**overrides):
    base = {
        "swagger": "2.0",
        "info": {"title": "T", "version": "1"},
        "host": "api.example.com",
        "basePath": "/v1",
        "schemes": ["https"],
        "paths": {"/x": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "overrides",
    [
        {"basePath": None},
        {"basePath": 42},
        {"basePath": ["/v1"]},
    ],
    ids=[
        "basePath_none",
        "basePath_int",
        "basePath_list",
    ],
)
def test_invalid_base_path_v2(overrides):
    schema = schemathesis.openapi.from_dict(_swagger_schema(**overrides))
    with pytest.raises(InvalidSchema):
        assert schema.base_path


@pytest.mark.parametrize(
    "overrides",
    [
        {"parameters": None},
        {"parameters": [None]},
        {"parameters": ["not-a-dict"]},
    ],
    ids=[
        "parameters_none",
        "parameter_none",
        "parameter_string",
    ],
)
def test_invalid_parameters_v2(overrides):
    base = _swagger_schema()
    base["paths"]["/x"]["get"].update(overrides)
    schema = schemathesis.openapi.from_dict(base)
    results = list(schema.get_all_operations())
    assert results
    for result in results:
        assert isinstance(result, Err)
        assert isinstance(result.err(), InvalidSchema)


@pytest.mark.parametrize(
    "parameters",
    [None, [None], ["not-a-dict"], [42]],
    ids=["parameters_none", "parameter_none", "parameter_string", "parameter_int"],
)
def test_invalid_parameters_v3(parameters):
    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1"},
            "paths": {"/x": {"get": {"parameters": parameters, "responses": {"200": {"description": "OK"}}}}},
        }
    )
    results = list(schema.get_all_operations())
    assert results
    for result in results:
        assert isinstance(result, Err)
        assert isinstance(result.err(), InvalidSchema)


def _first_operation(schema):
    return next(iter(schema.get_all_operations()))


@pytest.mark.parametrize("required", [False, True], ids=["optional", "required"])
def test_body_parameter_with_unresolvable_ref_v2(ctx, required):
    schema = ctx.openapi.load_schema(
        {
            "/things": {
                "get": {
                    "parameters": [
                        {
                            "in": "body",
                            "name": "payload",
                            "required": required,
                            "schema": {"$ref": "#/definitions/Missing"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="2.0",
    )
    result = _first_operation(schema)
    assert isinstance(result, Ok)
    assert list(result.ok().body) == []
    assert result.ok().skipped_parameters == [
        SkippedParameter(location="body", name="payload", reference="#/definitions/Missing", required=required)
    ]


@pytest.mark.parametrize("required", [False, True], ids=["optional", "required"])
def test_query_parameter_with_unresolvable_ref_v3(ctx, required):
    schema = ctx.openapi.load_schema(
        {
            "/things": {
                "get": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "filter",
                            "required": required,
                            "schema": {"$ref": "#/components/schemas/Missing"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    result = _first_operation(schema)
    if required:
        assert isinstance(result, Err)
        assert isinstance(result.err(), InvalidSchema)
    else:
        assert isinstance(result, Ok)
        assert list(result.ok().query) == []


@pytest.mark.parametrize("required", [False, True], ids=["optional", "required"])
def test_request_body_with_unresolvable_ref_v3(ctx, required):
    schema = ctx.openapi.load_schema(
        {
            "/things": {
                "post": {
                    "requestBody": {
                        "required": required,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Missing"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    result = _first_operation(schema)
    assert isinstance(result, Ok)
    assert list(result.ok().body) == []
    assert result.ok().skipped_parameters == [
        SkippedParameter(location="body", name=None, reference="#/components/schemas/Missing", required=required)
    ]


# The dangling `$ref` sits three levels down in an otherwise usable object; bundling is all-or-nothing.
def test_nested_unresolvable_ref_in_optional_parameter_v3(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/things": {
                "get": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "filter",
                            "required": False,
                            "schema": {
                                "type": "object",
                                "properties": {"nested": {"items": {"$ref": "#/components/schemas/Missing"}}},
                            },
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    result = _first_operation(schema)
    assert isinstance(result, Ok)
    assert list(result.ok().query) == []


def test_request_is_sent_when_optional_parameter_is_dropped(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/things": {
                "get": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "filter",
                            "required": False,
                            "schema": {"$ref": "#/components/schemas/Missing"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/things")
    def things():
        return jsonify([])

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    operation = _first_operation(schema).ok()

    @given(case=operation.as_strategy())
    @settings(max_examples=1, deadline=None)
    def test(case):
        assert case.call().status_code == 200

    test()


# `required` and the parameter's name live behind the `$ref`, not on the stub that names it.
@pytest.mark.parametrize("required", [False, True], ids=["optional", "required"])
def test_referenced_parameter_with_unresolvable_ref_v3(ctx, required):
    schema = ctx.openapi.load_schema(
        {
            "/things": {
                "get": {
                    "parameters": [{"$ref": "#/components/parameters/Filter"}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "parameters": {
                "Filter": {
                    "in": "query",
                    "name": "filter",
                    "required": required,
                    "schema": {"$ref": "#/components/schemas/Missing"},
                }
            }
        },
    )
    result = _first_operation(schema)
    if required:
        assert isinstance(result, Err)
        assert isinstance(result.err(), InvalidSchema)
    else:
        assert isinstance(result, Ok)
        assert result.ok().skipped_parameters == [
            SkippedParameter(location="query", name="filter", reference="#/components/schemas/Missing")
        ]


def test_response_schema_with_unresolvable_ref_is_unvalidatable(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/things": {
                "get": {
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}},
                        "404": {
                            "description": "Not Found",
                            "content": {"*/*": {"schema": {"$ref": "#/components/schemas/Missing"}}},
                        },
                    }
                }
            }
        }
    )
    responses = schema["/things"]["GET"].responses

    assert responses.get("200").get_schema("application/json") == ResolvedSchema(
        schema={"type": "object"}, media_type="application/json", name_to_uri={}, unresolvable_reference=None
    )
    assert responses.get("404").get_schema("application/json") == ResolvedSchema(
        schema=None, media_type="*/*", name_to_uri={}, unresolvable_reference="#/components/schemas/Missing"
    )


def test_response_header_with_unresolvable_ref_is_unvalidatable(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/things": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "headers": {"X-Total": {"schema": {"$ref": "#/components/schemas/Missing"}}},
                        }
                    }
                }
            }
        }
    )
    header = dict(schema["/things"]["GET"].responses.get("200").headers.items())["X-Total"]

    assert header.unresolvable_reference == "#/components/schemas/Missing"
    assert header.schema == {}
