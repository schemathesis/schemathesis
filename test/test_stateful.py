import re

import pytest

import schemathesis
from schemathesis.core.errors import InvalidStateMachine

pytestmark = [pytest.mark.openapi_version("3.0")]


def add_link(schema, target, **kwargs):
    schema.add_link(source=schema["/users/"]["POST"], target=target, status_code="201", **kwargs)
    responses = schema["/users/"]["POST"].definition.raw["responses"]["201"]
    if "$ref" in responses:
        _, responses = schema.resolver.resolve(responses["$ref"])
    return responses["links"]


EXPECTED_LINK_PARAMETERS = {"parameters": {"userId": "$response.body#/id"}}


@pytest.mark.operations("create_user", "get_user", "update_user")
def test_add_link_default(schema_url):
    schema = schemathesis.openapi.from_url(schema_url)
    # When we add a link to the target API operation
    # And it is an `APIOperation` instance
    # And it has the `operationId` key
    target = schema["/users/{user_id}"]["GET"]
    links = add_link(schema, target, parameters={"userId": "$response.body#/id"})
    # Then it should be added without errors
    assert links[f"{target.method.upper()} {target.path}"] == {
        "operationId": "getUser",
        **EXPECTED_LINK_PARAMETERS,
    }


@pytest.mark.parametrize("status_code", ["201", 201])
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_add_link_no_operations_cache(schema_url, status_code):
    schema = schemathesis.openapi.from_url(schema_url)
    # When we add a link to the target API operation
    source = schema["/users/"]["POST"]
    target = schema["/users/{user_id}"]["GET"]
    schema.add_link(
        source=source,
        target=target,
        status_code=status_code,
        parameters={"userId": "$response.body#/id"},
    )
    # Then it should be added without errors
    response = schema["/users/"]["POST"].definition.raw["responses"]["201"]
    if "$ref" in response:
        _, response = schema.resolver.resolve(response["$ref"])
    links = response["links"]
    assert links[f"{target.method.upper()} {target.path}"] == {
        "operationId": "getUser",
        **EXPECTED_LINK_PARAMETERS,
    }


@pytest.mark.operations("create_user", "get_user", "update_user")
def test_add_link_no_operation_id(schema_url):
    schema = schemathesis.openapi.from_url(schema_url)
    target = schema["/users/{user_id}"]["GET"]
    del target.definition.raw["operationId"]
    links = add_link(schema, target, parameters={"userId": "$response.body#/id"})
    assert links[f"{target.method.upper()} {target.path}"] == {
        "operationRef": "#/paths/~1users~1{user_id}/get",
        **EXPECTED_LINK_PARAMETERS,
    }


@pytest.mark.operations("create_user", "get_user", "update_user")
def test_add_link_by_reference(schema_url):
    schema = schemathesis.openapi.from_url(schema_url)
    links = add_link(schema, "#/paths/~1users~1{user_id}/get", parameters={"userId": "$response.body#/id"})
    assert links["#/paths/~1users~1{user_id}/get"] == {
        "operationRef": "#/paths/~1users~1{user_id}/get",
        **EXPECTED_LINK_PARAMETERS,
    }


@pytest.mark.operations("create_user", "get_user", "update_user")
def test_add_link_by_reference_twice(schema_url):
    schema = schemathesis.openapi.from_url(schema_url)
    add_link(schema, "#/paths/~1users~1{user_id}/get", parameters={"userId": "$response.body#/id"})
    links = add_link(schema, "#/paths/~1users~1{user_id}/get", request_body="#/foo")
    assert links["#/paths/~1users~1{user_id}/get"] == {
        "operationRef": "#/paths/~1users~1{user_id}/get",
        **EXPECTED_LINK_PARAMETERS,
    }
    assert links["#/paths/~1users~1{user_id}/get_new"] == {
        "operationRef": "#/paths/~1users~1{user_id}/get",
        "requestBody": "#/foo",
    }


@pytest.mark.operations("create_user", "get_user", "update_user")
def test_add_link_behind_a_reference(schema_url):
    # See GH-824
    schema = schemathesis.openapi.from_url(schema_url)
    # When all methods for an API operation are behind a reference
    schema.raw_schema["components"]["methods"] = {
        "users": schema.raw_schema["paths"]["/users/"],
        "user-details": schema.raw_schema["paths"]["/users/{user_id}"],
    }
    schema.raw_schema["paths"]["/users/"] = {"$ref": "#/components/methods/users"}
    schema.raw_schema["paths"]["/users/{user_id}"] = {"$ref": "#/components/methods/user-details"}
    # And a link is added
    add_link(schema, schema["/users/{user_id}"]["GET"], parameters={"userId": "$response.body#/id"})
    # Then the source API operation should have the new link
    operation = schema["/users/"]["POST"]
    response = operation.definition.raw["responses"]["201"]
    if "$ref" in response:
        _, response = schema.resolver.resolve(response["$ref"])
    links = response["links"]
    assert len(links) == 3
    assert links["GET /users/{user_id}"] == {"parameters": {"userId": "$response.body#/id"}, "operationId": "getUser"}


@pytest.mark.operations("create_user", "get_user", "update_user")
def test_add_link_nothing_is_provided(schema_url):
    schema = schemathesis.openapi.from_url(schema_url)
    # When the user doesn't provide parameters or request_body
    with pytest.raises(ValueError, match="You need to provide `parameters` or `request_body`."):
        # Then there should be an error
        schema.add_link(
            source=schema["/users/"]["POST"],
            target="#/paths/~1users~1{user_id}/get",
            status_code="201",
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda e: setattr(e, "method", "GET"),
            "Method `GET` not found. Available methods: POST",
        ),
        (
            lambda e: setattr(e, "path", "/userz/"),
            "`/userz/` not found. Did you mean `/users/`?",
        ),
        (lambda e: setattr(e, "path", "/what?/"), "`/what?/` not found"),
    ],
    ids=("method-change", "path-with-suggestion", "path-without-suggestion"),
)
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_add_link_unknown_operation(schema_url, change, message):
    schema = schemathesis.openapi.from_url(schema_url)
    # When the source API operation is modified and can't be found
    source = schema["/users/"]["POST"]
    change(source)
    with pytest.raises(LookupError, match=re.escape(message)):
        # Then there should be an error about it.
        schema.add_link(source=source, target="#/paths/~1users~1{user_id}/get", status_code="201", request_body="#/foo")


@pytest.mark.operations("create_user", "get_user", "update_user")
def test_links_access(schema_url):
    schema = schemathesis.openapi.from_url(schema_url)
    links = schema["/users/"]["POST"].links["201"]
    assert len(links) == 2
    assert links["GetUserByUserId"].ok().name == "GetUserByUserId"


@pytest.mark.parametrize(
    ("schema_code", "link_code"),
    [
        (200, "200"),
        (200, 200),
        ("200", "200"),
        ("200", 200),
        ("2XX", "2XX"),
    ],
)
def test_link_override(ctx, schema_code, link_code):
    # See GH-1022
    # When the schema contains response codes as integers
    schema = ctx.openapi.build_schema(
        {
            "/foo": {
                "get": {
                    "parameters": [
                        {"in": "query", "name": "key", "schema": {"type": "integer"}, "required": True},
                    ],
                    "responses": {schema_code: {"description": "OK", "schema": {"type": "object"}}},
                }
            },
        }
    )
    schema = schemathesis.openapi.from_dict(schema)
    schema.add_link(
        source=schema["/foo"]["GET"], target=schema["/foo"]["GET"], status_code=link_code, parameters={"key": "42"}
    )
    assert "links" in schema.raw_schema["paths"]["/foo"]["get"]["responses"][schema_code]


def test_missing_operation(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/users/": {
                "post": {
                    "responses": {
                        "201": {
                            "description": "OK",
                            "links": {
                                "GetUserByUserId": {
                                    "operationId": "unknown",
                                    "parameters": {"path.user_id": "$response.body#/id"},
                                },
                            },
                        }
                    },
                }
            },
            "/users/{user_id}": {
                "get": {"operationId": "getUser", "responses": {"200": {"description": "OK"}}},
            },
        }
    )

    schema = schemathesis.openapi.from_dict(schema)

    with pytest.raises(InvalidStateMachine) as exc:
        schema.as_state_machine()
    assert "Operation 'unknown' not found" in str(exc.value)
