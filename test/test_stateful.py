import re

import pytest

import schemathesis
from schemathesis.specs.openapi import expressions
from schemathesis.stateful import ParsedData
from schemathesis.utils import NOT_SET

pytestmark = [pytest.mark.openapi_version("3.0")]


@pytest.mark.parametrize(
    "parameters, body", (({"a": 1}, None), ({"a": 1}, NOT_SET), ({"a": 1}, {"value": 1}), ({"a": 1}, [1, 2, 3]))
)
def test_hashable(parameters, body):
    # All parsed data should be hashable
    hash(ParsedData(parameters, body))


def add_link(schema, target, **kwargs):
    schema.add_link(source=schema["/users/"]["POST"], target=target, status_code="201", **kwargs)
    return schema["/users/"]["POST"].definition.resolved["responses"]["201"]["links"]


EXPECTED_LINK_PARAMETERS = {"parameters": {"userId": "$response.body#/id"}}


@pytest.mark.operations("create_user", "get_user", "update_user")
def test_add_link_default(schema_url):
    schema = schemathesis.from_uri(schema_url)
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


@pytest.mark.parametrize("status_code", ("201", 201))
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_add_link_no_operations_cache(schema_url, status_code):
    schema = schemathesis.from_uri(schema_url)
    # When we add a link to the target API operation
    source = schema["/users/"]["POST"]
    target = schema["/users/{user_id}"]["GET"]
    # And the operations are not cached
    delattr(schema, "_operations")
    schema.add_link(
        source=source,
        target=target,
        status_code=status_code,
        parameters={"userId": "$response.body#/id"},
    )
    # Then it should be added without errors
    # And the cache cleanup should be no-op
    links = schema["/users/"]["POST"].definition.resolved["responses"]["201"]["links"]
    assert links[f"{target.method.upper()} {target.path}"] == {
        "operationId": "getUser",
        **EXPECTED_LINK_PARAMETERS,
    }


@pytest.mark.operations("create_user", "get_user", "update_user")
def test_add_link_no_operation_id(schema_url):
    schema = schemathesis.from_uri(schema_url)
    target = schema["/users/{user_id}"]["GET"]
    del target.definition.resolved["operationId"]
    links = add_link(schema, target, parameters={"userId": "$response.body#/id"})
    assert links[f"{target.method.upper()} {target.path}"] == {
        "operationRef": "#/paths/~1users~1{user_id}/get",
        **EXPECTED_LINK_PARAMETERS,
    }


@pytest.mark.operations("create_user", "get_user", "update_user")
def test_add_link_by_reference(schema_url):
    schema = schemathesis.from_uri(schema_url)
    links = add_link(schema, "#/paths/~1users~1{user_id}/get", parameters={"userId": "$response.body#/id"})
    assert links["#/paths/~1users~1{user_id}/get"] == {
        "operationRef": "#/paths/~1users~1{user_id}/get",
        **EXPECTED_LINK_PARAMETERS,
    }


@pytest.mark.operations("create_user", "get_user", "update_user")
def test_add_link_by_reference_twice(schema_url):
    schema = schemathesis.from_uri(schema_url)
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
    schema = schemathesis.from_uri(schema_url)
    # When all methods for an API operation are behind a reference
    schema.raw_schema["components"]["methods"] = {
        "users": schema.raw_schema["paths"]["/users/"],
        "user-details": schema.raw_schema["paths"]["/users/{user_id}"],
    }
    schema.raw_schema["paths"]["/users/"] = {"$ref": "#/components/methods/users"}
    schema.raw_schema["paths"]["/users/{user_id}"] = {"$ref": "#/components/methods/user-details"}
    assert not hasattr(schema, "_operations")
    # And a link is added
    add_link(schema, schema["/users/{user_id}"]["GET"], parameters={"userId": "$response.body#/id"})
    # Then the source API operation should have the new link
    operation = schema["/users/"]["POST"]
    links = operation.definition.resolved["responses"]["201"]["links"]
    assert len(links) == 3
    assert links["GET /users/{user_id}"] == {"parameters": {"userId": "$response.body#/id"}, "operationId": "getUser"}


@pytest.mark.operations("create_user", "get_user", "update_user")
def test_add_link_nothing_is_provided(schema_url):
    schema = schemathesis.from_uri(schema_url)
    # When the user doesn't provide parameters or request_body
    with pytest.raises(ValueError, match="You need to provide `parameters` or `request_body`."):
        # Then there should be an error
        schema.add_link(
            source=schema["/users/"]["POST"],
            target="#/paths/~1users~1{user_id}/get",
            status_code="201",
        )


@pytest.mark.parametrize(
    "change, message",
    (
        (
            lambda s, e: setattr(e, "method", "GET"),
            "No such API operation: `GET /users/`. Did you mean `POST /users/`?",
        ),
        (
            lambda s, e: setattr(e, "path", "/userz/"),
            "No such API operation: `POST /userz/`. Did you mean `POST /users/`?",
        ),
        (lambda s, e: setattr(e, "path", "/what?/"), "No such API operation: `POST /what?/`."),
        (
            lambda s, e: s.raw_schema["paths"].__setitem__("/users/", {}),
            "No such API operation: `POST /users/`.",
        ),
    ),
)
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_add_link_unknown_operation(schema_url, change, message):
    schema = schemathesis.from_uri(schema_url)
    # When the source API operation is modified and can't be found
    source = schema["/users/"]["POST"]
    change(schema, source)
    with pytest.raises(
        ValueError, match=re.escape(f"{message} Check if the requested API operation passes the filters in the schema.")
    ):
        # Then there should be an error about it.
        schema.add_link(source=source, target="#/paths/~1users~1{user_id}/get", status_code="201", request_body="#/foo")


@pytest.mark.operations("create_user", "get_user", "update_user")
def test_links_access(schema_url):
    schema = schemathesis.from_uri(schema_url)
    links = schema["/users/"]["POST"].links["201"]
    assert len(links) == 2
    assert links["GetUserByUserId"].name == "GetUserByUserId"


@pytest.mark.parametrize(
    "parameter, message",
    (
        ("userId", "No such parameter in `GET /users/{user_id}`: `userId`. Did you mean `user_id`?"),
        ("what?", "No such parameter in `GET /users/{user_id}`: `what?`."),
    ),
)
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_misspelled_parameter(schema_url, parameter, message):
    schema = schemathesis.from_uri(schema_url)
    # When the user supplies a parameter definition, that points to location which has no parameters defined in the
    # schema
    add_link(schema, "#/paths/~1users~1{user_id}/get", parameters={f"header.{parameter}": "$response.body#/id"})
    case = schema["/users/{user_id}"]["GET"].make_case()
    link = schema["/users/"]["POST"].links["201"]["#/paths/~1users~1{user_id}/get"]
    with pytest.raises(ValueError, match=re.escape(message)):
        link.set_data(case, elapsed=1.0, context=expressions.ExpressionContext(case=case, response=None))


@pytest.mark.parametrize(
    "schema_code, link_code",
    (
        (200, "200"),
        (200, 200),
        ("200", "200"),
        ("200", 200),
        ("2XX", "2XX"),
    ),
)
def test_link_override(empty_open_api_3_schema, schema_code, link_code):
    # See GH-1022
    # When the schema contains response codes as integers
    empty_open_api_3_schema["paths"] = {
        "/foo": {
            "get": {
                "parameters": [
                    {"in": "query", "name": "key", "schema": {"type": "integer"}, "required": True},
                ],
                "responses": {schema_code: {"description": "OK", "schema": {"type": "object"}}},
            }
        },
    }
    schema = schemathesis.from_dict(empty_open_api_3_schema, validate_schema=False)
    schema.add_link(
        source=schema["/foo"]["GET"], target=schema["/foo"]["GET"], status_code=link_code, parameters={"key": "42"}
    )
    assert "links" in schema.raw_schema["paths"]["/foo"]["get"]["responses"][schema_code]
