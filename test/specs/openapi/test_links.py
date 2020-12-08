import json
from unittest.mock import ANY

import pytest
import requests

import schemathesis
from schemathesis.models import Case, Endpoint, EndpointDefinition
from schemathesis.specs.openapi.links import Link, get_container
from schemathesis.specs.openapi.parameters import OpenAPI30Parameter
from schemathesis.stateful import ParsedData, Stateful

ENDPOINT = Endpoint(
    path="/users/{user_id}",
    method="get",
    definition=ANY,
    schema=ANY,
    base_url=ANY,
    path_parameters=[
        OpenAPI30Parameter({"in": "path", "name": "user_id", "schema": {"type": "integer"}}),
    ],
    query=[
        OpenAPI30Parameter({"in": "query", "name": "code", "schema": {"type": "integer"}}),
        OpenAPI30Parameter({"in": "query", "name": "user_id", "schema": {"type": "integer"}}),
        OpenAPI30Parameter({"in": "query", "name": "common", "schema": {"type": "integer"}}),
    ],
)
LINK = Link(
    name="GetUserByUserId",
    endpoint=ENDPOINT,
    parameters={"path.user_id": "$response.body#/id", "query.user_id": "$response.body#/id"},
)


@pytest.fixture(scope="module")
def case():
    return Case(ENDPOINT)


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
                    endpoint=Endpoint(
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
                    endpoint=ANY,
                    parameters={"user_id": "$response.body#/id"},
                    request_body={"username": "foo"},
                ),
            ],
        ),
        ("/unknown", []),
    ),
)
@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_get_links(openapi3_base_url, schema_url, url, expected):
    schema = schemathesis.from_uri(schema_url)
    response = requests.post(f"{openapi3_base_url}{url}", json={"username": "TEST"})
    assert schema.endpoints["/users/"]["POST"].get_stateful_tests(response, Stateful.links) == expected


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
def test_make_endpoint(value, path_user_id, query_user_id, code):
    endpoint = LINK.make_endpoint(list(map(ParsedData, value)))
    # There is only one path parameter
    assert len(endpoint.path_parameters) == 1
    assert sorted(endpoint.path_parameters[0].definition["schema"]["enum"], key=json.dumps) == path_user_id
    assert len(endpoint.query) == 3

    for item in endpoint.query:
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


def test_make_endpoint_single():
    endpoint = LINK.make_endpoint([ParsedData({"path.user_id": 1, "query.user_id": 2, "code": 7})])
    assert endpoint.path_parameters == [OpenAPI30Parameter({"in": "path", "name": "user_id", "schema": {"enum": [1]}})]
    for item in endpoint.query:
        schema = item.definition["schema"]
        if item.name == "code":
            assert schema == {"enum": [7]}
        elif item.name == "user_id":
            assert schema == {"enum": [2]}
        else:
            assert schema == {"type": "integer"}


@pytest.mark.parametrize("parameter", ("wrong.id", "unknown", "header.id"))
def test_make_endpoint_invalid_location(parameter):
    with pytest.raises(ValueError, match=f"Parameter `{parameter}` is not defined in endpoint GET /users/{{user_id}}"):
        LINK.make_endpoint([ParsedData({parameter: 4})])


def test_get_container_invalid_location():
    endpoint = Endpoint(
        path="/users/{user_id}",
        method="get",
        schema=None,
        definition=EndpointDefinition(
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
    case = endpoint.make_case()
    with pytest.raises(ValueError, match="Parameter `unknown` is not defined in endpoint `GET /users/{user_id}`"):
        get_container(case, None, "unknown")
