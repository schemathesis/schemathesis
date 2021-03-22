import json
from unittest.mock import ANY

import pytest
import requests

import schemathesis
from schemathesis.models import APIOperation, Case, OperationDefinition
from schemathesis.parameters import ParameterSet, PayloadAlternatives
from schemathesis.specs.openapi.links import Link, get_container
from schemathesis.specs.openapi.parameters import OpenAPI20Body, OpenAPI30Body, OpenAPI30Parameter
from schemathesis.stateful import ParsedData, Stateful

API_OPERATION = APIOperation(
    path="/users/{user_id}",
    method="get",
    definition=ANY,
    schema=ANY,
    base_url=ANY,
    path_parameters=ParameterSet(
        [
            OpenAPI30Parameter({"in": "path", "name": "user_id", "schema": {"type": "integer"}}),
        ]
    ),
    query=ParameterSet(
        [
            OpenAPI30Parameter({"in": "query", "name": "code", "schema": {"type": "integer"}}),
            OpenAPI30Parameter({"in": "query", "name": "user_id", "schema": {"type": "integer"}}),
            OpenAPI30Parameter({"in": "query", "name": "common", "schema": {"type": "integer"}}),
        ]
    ),
)
LINK = Link(
    name="GetUserByUserId",
    operation=API_OPERATION,
    parameters={"path.user_id": "$response.body#/id", "query.user_id": "$response.body#/id"},
)


@pytest.fixture(scope="module")
def case():
    return Case(API_OPERATION)


@pytest.fixture(scope="module")
def response():
    response = requests.Response()
    response._content = b'{"id": 5}'
    response.status_code = 201
    response.headers["Content-Type"] = "application/json"
    return response


@pytest.mark.parametrize(
    "url, expected",
    (
        (
            "/users/",
            [
                Link(
                    name="GetUserByUserId",
                    operation=APIOperation(
                        path="/users/{user_id}",
                        method="get",
                        definition=ANY,
                        schema=ANY,
                        base_url=ANY,
                        path_parameters=ANY,
                        query=ANY,
                    ),
                    parameters={"path.user_id": "$response.body#/id", "query.user_id": "$response.body#/id"},
                ),
                Link(
                    name="UpdateUserById",
                    operation=ANY,
                    parameters={"user_id": "$response.body#/id"},
                ),
            ],
        ),
        ("/unknown", []),
    ),
)
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_get_links(openapi3_base_url, schema_url, url, expected):
    schema = schemathesis.from_uri(schema_url)
    response = requests.post(f"{openapi3_base_url}{url}", json={"first_name": "TEST", "last_name": "TEST"})
    tests = schema["/users/"]["POST"].get_stateful_tests(response, Stateful.links)
    assert len(tests) == len(expected)
    for test, value in zip(tests, expected):
        assert test.name == value.name
        assert test.parameters == value.parameters


def test_parse(case, response):
    assert LINK.parse(case, response) == ParsedData({"path.user_id": 5, "query.user_id": 5})


EXPECTED_PATH_PARAMETERS = [
    {
        "additionalProperties": False,
        "properties": {"user_id": {"const": 1, "in": "path", "name": "user_id", "type": "integer"}},
        "required": ["user_id"],
        "type": "object",
    },
    {
        "additionalProperties": False,
        "properties": {"user_id": {"const": 3, "in": "path", "name": "user_id", "type": "integer"}},
        "required": ["user_id"],
        "type": "object",
    },
]


@pytest.mark.parametrize(
    "value, path_user_id, query_user_id, code",
    (
        (
            [{"path.user_id": 1, "query.user_id": 2, "code": 7}, {"path.user_id": 3, "query.user_id": 4, "code": 5}],
            [1, 3],
            {"enum": [2, 4]},
            {"enum": [7, 5]},
        ),
        (
            [{"path.user_id": 1}, {"path.user_id": 3}],
            [1, 3],
            {"type": "integer"},
            {"type": "integer"},
        ),
    ),
)
def test_make_operation(value, path_user_id, query_user_id, code):
    operation = LINK.make_operation(list(map(ParsedData, value)))
    # There is only one path parameter
    assert len(operation.path_parameters) == 1
    assert sorted(operation.path_parameters[0].definition["schema"]["enum"], key=json.dumps) == path_user_id
    assert len(operation.query) == 3

    for item in operation.query:
        schema = item.definition["schema"]
        if item.name == "code":
            assert_schema(schema, code)
        elif item.name == "user_id":
            assert_schema(schema, query_user_id)
        else:
            assert schema == {"type": "integer"}


def assert_schema(target, expected):
    if "enum" in expected:
        assert len(target) == 1
        assert sorted(target["enum"]) == sorted(expected["enum"])
    else:
        assert target == expected


def test_make_operation_single():
    operation = LINK.make_operation([ParsedData({"path.user_id": 1, "query.user_id": 2, "code": 7})])
    assert len(operation.path_parameters) == 1
    assert isinstance(operation.path_parameters[0], OpenAPI30Parameter)
    assert operation.path_parameters[0].definition == {"in": "path", "name": "user_id", "schema": {"enum": [1]}}
    for item in operation.query:
        schema = item.definition["schema"]
        if item.name == "code":
            assert schema == {"enum": [7]}
        elif item.name == "user_id":
            assert schema == {"enum": [2]}
        else:
            assert schema == {"type": "integer"}


BODY_SCHEMA = {"required": ["foo"], "type": "object", "properties": {"foo": {"type": "string"}}}


@pytest.mark.parametrize(
    "body",
    (
        OpenAPI20Body(
            {
                "name": "attributes",
                "in": "body",
                "required": True,
                "schema": BODY_SCHEMA,
            },
            media_type="application/json",
        ),
        OpenAPI30Body(definition={"schema": BODY_SCHEMA}, media_type="application/json", required=True),
    ),
)
def test_make_operation_body(body):
    # See GH-1069
    # When `requestBody` is present in the link definition
    # And in the target operation
    operation = APIOperation(
        path="/users/",
        method="post",
        definition=ANY,
        schema=ANY,
        base_url=ANY,
        body=PayloadAlternatives([body]),
    )
    body = {"foo": "bar"}  # Literal value
    link = Link(
        name="Link",
        operation=operation,
        parameters={},
        request_body={"requestBody": body},
    )
    # Then it should be taken into account during creation a modified version of that operation
    new_operation = link.make_operation([ParsedData({}, body=body)])  # Actual parsed data will contain the literal
    assert new_operation.body[0].definition["schema"] == {"enum": [body]}


def test_invalid_request_body_definition():
    # When a link defines `requestBody` for operation that does not accept one
    operation = APIOperation(
        path="/users/",
        method="get",
        definition=ANY,
        schema=ANY,
        base_url=ANY,
    )
    # Then a proper error should be triggered
    with pytest.raises(ValueError):
        Link(name="Link", operation=operation, parameters={}, request_body={"requestBody": {"foo": "bar"}})


@pytest.mark.parametrize("parameter", ("wrong.id", "unknown", "header.id"))
def test_make_operation_invalid_location(parameter):
    with pytest.raises(
        ValueError, match=f"Parameter `{parameter}` is not defined in API operation GET /users/{{user_id}}"
    ):
        LINK.make_operation([ParsedData({parameter: 4})])


def test_get_container_invalid_location():
    operation = APIOperation(
        path="/users/{user_id}",
        method="get",
        schema=None,
        definition=OperationDefinition(
            raw={},
            resolved={},
            scope="",
            parameters=[
                OpenAPI30Parameter({"in": "query", "name": "code", "type": "integer"}),
                OpenAPI30Parameter({"in": "query", "name": "user_id", "type": "integer"}),
                OpenAPI30Parameter({"in": "query", "name": "common", "type": "integer"}),
            ],
        ),
    )
    case = operation.make_case()
    with pytest.raises(ValueError, match="Parameter `unknown` is not defined in API operation `GET /users/{user_id}`"):
        get_container(case, None, "unknown")
