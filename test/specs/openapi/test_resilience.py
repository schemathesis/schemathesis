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
        [{"url": "http://x/}"}],
        [{"url": "http://x/{"}],
        [{"url": "http://x/{var!}", "variables": {"var": {"default": "v"}}}],
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
        "url_stray_closing_brace",
        "url_unclosed_brace",
        "url_bad_conversion",
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


_OK_RESPONSES = {"responses": {"200": {"description": "OK"}}}


@pytest.mark.parametrize("version", ["2.0", "3.0.2"], ids=["v2", "v3"])
@pytest.mark.parametrize("definition", [None, "not-an-object"], ids=["none", "string"])
def test_malformed_operation_node(ctx, version, definition):
    schema = ctx.openapi.load_schema({"/things": {"post": definition}, "/ok": {"get": _OK_RESPONSES}}, version=version)
    results = list(schema.get_all_operations())
    assert isinstance(results[0], Err)
    assert isinstance(results[0].err(), InvalidSchema)
    assert "Location:\n    paths -> /things -> post" in str(results[0].err())
    assert schema.statistic.operations.total == len(results)


@pytest.mark.parametrize("version", ["2.0", "3.0.2"], ids=["v2", "v3"])
@pytest.mark.parametrize("responses", [None, "not-an-object", 42], ids=["none", "string", "integer"])
def test_malformed_responses_node(ctx, version, responses):
    schema = ctx.openapi.load_schema(
        {"/things": {"post": {"responses": responses}}, "/ok": {"get": _OK_RESPONSES}}, version=version
    )
    results = list(schema.get_all_operations())
    assert isinstance(results[0], Err)
    assert isinstance(results[0].err(), InvalidSchema)
    assert schema.statistic.operations.total == len(results)


@pytest.mark.parametrize("version", ["2.0", "3.0.2"], ids=["v2", "v3"])
@pytest.mark.parametrize("definition", [None, "not-an-object"], ids=["none", "string"])
def test_operation_lookup_survives_malformed_sibling(ctx, version, definition):
    schema = ctx.openapi.load_schema(
        {"/things": {"post": definition}, "/ok": {"get": {"operationId": "ok", **_OK_RESPONSES}}},
        version=version,
    )
    assert schema.find_operation_by_id("ok").label == "GET /ok"


@pytest.mark.parametrize("body_schema", [True, False], ids=["true", "false"])
def test_boolean_body_schema_v2(ctx, body_schema):
    schema = ctx.openapi.load_schema(
        {
            "/things": {
                "post": {
                    "parameters": [{"in": "body", "name": "body", "required": True, "schema": body_schema}],
                    **_OK_RESPONSES,
                }
            }
        },
        version="2.0",
    )
    results = list(schema.get_all_operations())
    assert isinstance(results[0], Ok)
    assert schema.statistic.operations.total == len(results)


@pytest.mark.parametrize(
    "request_body",
    ["not-an-object", {"content": None}, {"content": "not-an-object"}, {"content": {"application/json": None}}],
    ids=["request_body_string", "content_none", "content_string", "media_type_none"],
)
def test_malformed_request_body_node_v3(ctx, request_body):
    schema = ctx.openapi.load_schema({"/things": {"post": {"requestBody": request_body, **_OK_RESPONSES}}})
    result = _first_operation(schema)
    assert isinstance(result, Err)
    assert isinstance(result.err(), InvalidSchema)
    assert "Location:\n    paths -> /things -> post -> requestBody" in str(result.err())


@pytest.mark.parametrize("security", [{"api_key": []}, "api_key"], ids=["mapping", "string"])
def test_non_list_security_requirements_v3(ctx, security):
    schema = ctx.openapi.load_schema(
        {"/things": {"post": _OK_RESPONSES}},
        security=security,
        components={"securitySchemes": {"api_key": {"type": "apiKey", "name": "k", "in": "header"}}},
    )
    result = _first_operation(schema)
    assert isinstance(result, Err)
    assert isinstance(result.err(), InvalidSchema)
    assert "Location:\n    security" in str(result.err())


@pytest.mark.parametrize(
    "components",
    ["oops", 42, {"securitySchemes": "oops"}],
    ids=["string", "integer", "security_schemes_string"],
)
def test_malformed_components_node_v3(ctx, components):
    result = _first_operation(ctx.openapi.load_schema({"/things": {"post": _OK_RESPONSES}}, components=components))
    assert isinstance(result, Err)
    assert isinstance(result.err(), InvalidSchema)
    assert "Location:\n    components" in str(result.err())


@pytest.mark.parametrize("version", ["2.0", "3.0.2"], ids=["v2", "v3"])
@pytest.mark.parametrize("parameters", [{"a": 1}, "not-a-list", 42], ids=["mapping", "string", "integer"])
def test_malformed_path_item_parameters(ctx, version, parameters):
    schema = ctx.openapi.load_schema(
        {"/things": {"parameters": parameters, "post": _OK_RESPONSES}, "/ok": {"get": _OK_RESPONSES}},
        version=version,
    )
    broken, valid = list(schema.get_all_operations())
    assert isinstance(broken, Err)
    assert isinstance(broken.err(), InvalidSchema)
    assert "Location:\n    paths -> /things -> parameters" in str(broken.err())
    assert isinstance(valid, Ok)


@pytest.mark.parametrize(
    ("operation", "components", "location"),
    [
        (
            {"requestBody": {"$ref": "#/components/requestBodies/Broken"}, **_OK_RESPONSES},
            {"requestBodies": {"Broken": "not-an-object"}},
            "components -> requestBodies -> Broken",
        ),
        (
            {"parameters": [{"$ref": "#/components/parameters/Broken"}], **_OK_RESPONSES},
            {"parameters": {"Broken": "not-an-object"}},
            "components -> parameters -> Broken",
        ),
        (
            {"responses": {"200": {"$ref": "#/components/responses/Broken"}}},
            {"responses": {"Broken": "not-an-object"}},
            "components -> responses -> Broken",
        ),
    ],
    ids=["request_body", "parameter", "response"],
)
def test_reference_to_non_object_node_v3(ctx, operation, components, location):
    schema = ctx.openapi.load_schema(
        {"/things": {"post": operation}, "/ok": {"get": _OK_RESPONSES}}, components=components
    )
    broken, valid = list(schema.get_all_operations())
    assert isinstance(broken, Err)
    assert isinstance(broken.err(), InvalidSchema)
    assert f"Location:\n    {location}" in str(broken.err())
    assert isinstance(valid, Ok)


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


@pytest.mark.parametrize("reference", ["missing.json", "missing\x00.json"], ids=["missing_file", "null_byte"])
def test_parameter_ref_that_names_no_file_v3(ctx, reference):
    # A reference is arbitrary text, and text with a null byte in it reaches the filesystem as a path.
    schema = ctx.openapi.load_schema(
        {
            "/things": {
                "get": {
                    "parameters": [{"in": "query", "name": "q", "required": True, "schema": {"$ref": reference}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    result = _first_operation(schema)
    assert isinstance(result, Err)
    assert "Unresolvable reference" in str(result.err())


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
