import json
from unittest.mock import ANY

import pytest
import requests

import schemathesis
from schemathesis.models import Case, Endpoint
from schemathesis.specs.openapi.links import Link
from schemathesis.stateful import ParsedData

ENDPOINT = Endpoint(
    path="/users/{user_id}",
    method="GET",
    definition=ANY,
    schema=ANY,
    base_url=ANY,
    path_parameters={
        "properties": {"user_id": {"in": "path", "name": "user_id", "type": "integer"}},
        "additionalProperties": False,
        "type": "object",
        "required": ["user_id"],
    },
    query={
        "properties": {
            "code": {"in": "query", "name": "code", "type": "integer"},
            "user_id": {"in": "query", "name": "user_id", "type": "integer"},
            "common": {"in": "query", "name": "common", "type": "integer"},
        },
        "additionalProperties": False,
        "type": "object",
        "required": ["code", "user_id", "common"],
    },
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
                LINK,
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
def test_get_links(base_url, schema_url, url, expected):
    schema = schemathesis.from_uri(schema_url)
    response = requests.post(f"{base_url}{url}", json={"username": "TEST"})
    assert schema.endpoints["/users/"]["POST"].get_stateful_tests(response, "links") == expected


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
    "value, path_parameters, query",
    (
        (
            [{"path.user_id": 1, "query.user_id": 2, "code": 7}, {"path.user_id": 3, "query.user_id": 4, "code": 5}],
            EXPECTED_PATH_PARAMETERS,
            [
                {
                    "additionalProperties": False,
                    "properties": {
                        "code": {"const": 5, "in": "query", "name": "code", "type": "integer"},
                        "user_id": {"const": 4, "in": "query", "name": "user_id", "type": "integer"},
                        "common": {"in": "query", "name": "common", "type": "integer"},
                    },
                    "required": ["code", "user_id", "common"],
                    "type": "object",
                },
                {
                    "additionalProperties": False,
                    "properties": {
                        "code": {"const": 7, "in": "query", "name": "code", "type": "integer"},
                        "user_id": {"const": 2, "in": "query", "name": "user_id", "type": "integer"},
                        "common": {"in": "query", "name": "common", "type": "integer"},
                    },
                    "required": ["code", "user_id", "common"],
                    "type": "object",
                },
            ],
        ),
        (
            [{"path.user_id": 1}, {"path.user_id": 3}],
            EXPECTED_PATH_PARAMETERS,
            [
                {
                    "additionalProperties": False,
                    "properties": {
                        "code": {"in": "query", "name": "code", "type": "integer"},
                        "user_id": {"in": "query", "name": "user_id", "type": "integer"},
                        "common": {"in": "query", "name": "common", "type": "integer"},
                    },
                    "required": ["code", "user_id", "common"],
                    "type": "object",
                },
                {
                    "additionalProperties": False,
                    "properties": {
                        "code": {"in": "query", "name": "code", "type": "integer"},
                        "user_id": {"in": "query", "name": "user_id", "type": "integer"},
                        "common": {"in": "query", "name": "common", "type": "integer"},
                    },
                    "required": ["code", "user_id", "common"],
                    "type": "object",
                },
            ],
        ),
    ),
)
def test_make_endpoint(value, path_parameters, query):
    endpoint = LINK.make_endpoint(list(map(ParsedData, value)))
    assert len(endpoint.path_parameters) == 1
    assert sorted(endpoint.path_parameters["anyOf"], key=json.dumps) == path_parameters
    assert len(endpoint.query) == 1
    assert sorted(endpoint.query["anyOf"], key=json.dumps) == query


def test_make_endpoint_single():
    endpoint = LINK.make_endpoint([ParsedData({"path.user_id": 1, "query.user_id": 2, "code": 7})])
    assert endpoint.path_parameters == {
        "properties": {"user_id": {"in": "path", "name": "user_id", "type": "integer", "const": 1}},
        "additionalProperties": False,
        "type": "object",
        "required": ["user_id"],
    }
    assert endpoint.query == {
        "properties": {
            "code": {"in": "query", "name": "code", "type": "integer", "const": 7},
            "user_id": {"in": "query", "name": "user_id", "type": "integer", "const": 2},
            "common": {"in": "query", "name": "common", "type": "integer"},
        },
        "additionalProperties": False,
        "type": "object",
        "required": ["code", "user_id", "common"],
    }


@pytest.mark.parametrize("parameter", ("wrong.id", "unknown", "header.id"))
def test_make_endpoint_invalid_location(parameter):
    with pytest.raises(ValueError, match=f"Parameter `{parameter}` is not defined in endpoint GET /users/{{user_id}}"):
        LINK.make_endpoint([ParsedData({parameter: 4})])
