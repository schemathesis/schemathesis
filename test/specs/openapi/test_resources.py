from __future__ import annotations

import pytest
import requests

import schemathesis
from schemathesis.core import deserialization
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import Response
from schemathesis.resources.repository import PER_TYPE_CAPACITY
from schemathesis.specs.openapi.extra_data_source import ParameterRequirement

USER_RESOURCE = "User"
POST_USERS = "POST /users"
CREATED = 201


@pytest.fixture
def user_schema_builder(ctx):
    def build(*, status_code="201", response_schema=None, extra_endpoints=None):
        if response_schema is None:
            response_schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}

        paths = {
            "/users": {
                "post": {"responses": {status_code: {"content": {"application/json": {"schema": response_schema}}}}}
            }
        }
        if extra_endpoints:
            paths.update(extra_endpoints)

        spec = ctx.openapi.build_schema(paths)
        return schemathesis.openapi.from_dict(spec)

    return build


@pytest.fixture
def user_schema(user_schema_builder):
    return user_schema_builder()


@pytest.fixture
def user_data_source(user_schema):
    return user_schema.create_extra_data_source()


def test_store_single_resource(user_data_source):
    user_data_source.repository.record_response(
        operation=POST_USERS, status_code=CREATED, payload={"id": "123", "name": "Jane"}
    )
    resources = list(user_data_source.repository.iter_instances(USER_RESOURCE))
    assert len(resources) == 1
    assert resources[0].data["id"] == "123"


def test_ignore_non_matching_status_code(user_data_source):
    # Non-2xx responses should be ignored even though the schema expects 2xx
    user_data_source.repository.record_response(operation=POST_USERS, status_code=404, payload={"id": "1"})
    assert list(user_data_source.repository.iter_instances(USER_RESOURCE)) == []


def test_lenient_2xx_matching(user_data_source):
    # Schema expects 201 but server returns 200 - should still record (both are 2xx)
    user_data_source.repository.record_response(operation=POST_USERS, status_code=200, payload={"id": "1"})
    resources = list(user_data_source.repository.iter_instances(USER_RESOURCE))
    assert len(resources) == 1
    assert resources[0].data["id"] == "1"


def test_many_cardinality_extracts_each_item(user_schema_builder):
    array_schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            }
        },
    }
    schema = user_schema_builder(response_schema=array_schema)
    data_source = schema.create_extra_data_source()

    payload = {"items": [{"id": "a"}, {"id": "b"}]}
    data_source.repository.record_response(operation=POST_USERS, status_code=CREATED, payload=payload)

    resources = list(data_source.repository.iter_instances(USER_RESOURCE))
    assert {instance.data["id"] for instance in resources} == {"a", "b"}


def test_data_source_augments_schema(user_schema_builder):
    user_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    get_endpoint = {
        "/users/{user_id}": {
            "get": {
                "operationId": "getUser",
                "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {
                    "200": {"description": "Success", "content": {"application/json": {"schema": user_schema}}}
                },
            }
        }
    }
    schema = user_schema_builder(response_schema=user_schema, extra_endpoints=get_endpoint)
    data_source = schema.create_extra_data_source()

    data_source.repository.record_response(
        operation=POST_USERS, status_code=CREATED, payload={"id": "1", "name": "Alice"}
    )
    data_source.repository.record_response(
        operation=POST_USERS, status_code=CREATED, payload={"id": "2", "name": "Bob"}
    )

    get_operation = schema["/users/{user_id}"]["GET"]
    path_params_schema = get_operation.path_parameters.schema

    augmented = data_source.augment(operation=get_operation, location=ParameterLocation.PATH, schema=path_params_schema)

    assert augmented is not path_params_schema
    options = augmented["properties"]["user_id"]["anyOf"]
    assert options[1]["enum"] == ["1", "2"]


def test_wildcard_status_code_matching(user_schema_builder):
    schema = user_schema_builder(status_code="2XX")
    data_source = schema.create_extra_data_source()

    data_source.repository.record_response(operation=POST_USERS, status_code=201, payload={"id": "1"})
    data_source.repository.record_response(operation=POST_USERS, status_code=404, payload={"id": "2"})

    resources = list(data_source.repository.iter_instances(USER_RESOURCE))
    assert len(resources) == 1
    assert resources[0].data["id"] == "1"


@pytest.mark.parametrize(
    ("parameter_schema", "expected_augmented"),
    [
        pytest.param(
            {"type": "string"},
            {"anyOf": [{"type": "string", "minLength": 1}, {"enum": ["123"]}]},
            id="simple-type",
        ),
        pytest.param(
            {"anyOf": [{"type": "string"}, {"type": "number"}]},
            {"anyOf": [{"type": "string"}, {"type": "number"}, {"enum": ["123"]}]},
            id="existing-anyof",
        ),
        pytest.param(
            {"oneOf": [{"type": "string"}, {"type": "number"}]},
            {"anyOf": [{"oneOf": [{"type": "string"}, {"type": "number"}]}, {"enum": ["123"]}]},
            id="oneof",
        ),
        pytest.param(
            {"allOf": [{"type": "string"}, {"minLength": 5}]},
            {"anyOf": [{"allOf": [{"type": "string"}, {"minLength": 5}]}, {"enum": ["123"]}]},
            id="allof",
        ),
        pytest.param(
            {"not": {"type": "null"}},
            {"anyOf": [{"not": {"type": "null"}}, {"enum": ["123"]}]},
            id="not",
        ),
        pytest.param(
            True,
            None,
            id="boolean-schema",
        ),
    ],
)
def test_augment_wraps_schemas_in_anyof(user_schema_builder, parameter_schema, expected_augmented):
    get_endpoint = {
        "/users/{user_id}": {
            "get": {
                "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": parameter_schema}],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    schema = user_schema_builder(extra_endpoints=get_endpoint)
    data_source = schema.create_extra_data_source()
    data_source.repository.record_response(operation=POST_USERS, status_code=CREATED, payload={"id": "123"})

    operation = schema["/users/{user_id}"]["GET"]
    path_schema = operation.path_parameters.schema

    augmented = data_source.augment(operation=operation, location=ParameterLocation.PATH, schema=path_schema)

    if expected_augmented is None:
        assert augmented is path_schema
    else:
        original_property = dict(path_schema["properties"]["user_id"])
        assert augmented["properties"]["user_id"] == expected_augmented
        assert augmented is not path_schema
        assert path_schema["properties"]["user_id"] == original_property


@pytest.mark.parametrize(
    ("payloads", "expected"),
    [
        pytest.param(
            [{"id": "valid", "other": ["list", "of", "values"]}],
            ["valid"],
            id="filters-lists",
        ),
        pytest.param(
            [{"id": "valid", "other": {"nested": "dict"}}],
            ["valid"],
            id="filters-dicts",
        ),
        pytest.param(
            [{"id": "valid", "other": None}],
            ["valid"],
            id="filters-none",
        ),
        pytest.param(
            [{"id": "same"}] * 5,
            ["same"],
            id="deduplicates",
        ),
        pytest.param(
            [{"id": str(i)} for i in range(100)],
            PER_TYPE_CAPACITY,
            id="respects-capacity-limit",
        ),
    ],
)
def test_collect_values(user_data_source, payloads, expected):
    for payload in payloads:
        user_data_source.repository.record_response(operation=POST_USERS, status_code=CREATED, payload=payload)

    requirement = ParameterRequirement(USER_RESOURCE, "id")
    values = user_data_source._collect_values(requirement)

    if isinstance(expected, int):
        assert len(values) == expected
    else:
        assert values == expected


@pytest.mark.parametrize(
    ("field", "payloads", "expected_values"),
    [
        pytest.param(
            "tags",
            [{"tags": ["python", "api"]}, {"tags": ["python", "testing"]}, {"tags": ["python", "api"]}],
            [["python", "api"], ["python", "testing"]],
            id="list-deduplication",
        ),
        pytest.param(
            "metadata",
            [
                {"metadata": {"role": "admin", "level": 5}},
                {"metadata": {"level": 5, "role": "admin"}},
                {"metadata": {"role": "user", "level": 1}},
            ],
            [{"role": "admin", "level": 5}, {"role": "user", "level": 1}],
            id="dict-deduplication",
        ),
        pytest.param(
            "id",
            [{"id": "123"}, {"id": 123}, {"id": 123.0}],
            ["123", 123, 123.0],
            id="type-aware-deduplication",
        ),
    ],
)
def test_collect_complex_values(user_data_source, field, payloads, expected_values):
    for payload in payloads:
        user_data_source.repository.record_response(operation=POST_USERS, status_code=CREATED, payload=payload)

    requirement = ParameterRequirement(USER_RESOURCE, field)
    values = user_data_source._collect_values(requirement)

    assert len(values) == len(expected_values)
    for expected in expected_values:
        assert expected in values


def test_unresolvable_pointer(user_schema_builder):
    array_schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            }
        },
    }
    schema = user_schema_builder(response_schema=array_schema)
    data_source = schema.create_extra_data_source()

    payload = {"data": [{"id": "a"}]}
    data_source.repository.record_response(operation=POST_USERS, status_code=CREATED, payload=payload)

    resources = list(data_source.repository.iter_instances(USER_RESOURCE))
    assert resources == []


def test_augment_body_without_properties(user_schema):
    operation = user_schema["/users"]["POST"]
    array_schema = {"type": "array", "items": {"type": "string"}}

    augmented = user_schema.analysis.extra_data_source.augment(
        operation=operation, location=ParameterLocation.BODY, schema=array_schema
    )

    assert augmented is array_schema


def test_custom_deserializer(ctx):
    @deserialization.deserializer("text/x-keyvalue")
    def deserialize_keyvalue(ctx, response):
        data = {}
        for line in response.content.decode("utf-8").strip().split("\n"):
            if "=" in line:
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip()
        return data

    spec = ctx.openapi.build_schema(
        {
            "/users": {
                "post": {
                    "responses": {
                        "201": {
                            "content": {
                                "text/x-keyvalue": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                        "required": ["id"],
                                    }
                                }
                            },
                            "links": {
                                "GetUserById": {
                                    "operationId": "getUser",
                                    "parameters": {"user_id": "$response.body#/id"},
                                }
                            },
                        }
                    }
                }
            },
            "/users/{user_id}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Success"}},
                }
            },
        }
    )
    schema = schemathesis.openapi.from_dict(spec)
    data_source = schema.create_extra_data_source()

    operation = schema["/users"]["POST"]
    case = operation.Case()

    req = requests.Request("POST", "http://test.example/users")
    prepared = req.prepare()
    response = Response(
        status_code=201,
        headers={"content-type": ["text/x-keyvalue"]},
        content=b"id=user123\nname=Alice",
        request=prepared,
        elapsed=0.1,
        verify=True,
    )

    data_source.record_response(operation=operation, response=response, case=case)

    resources = list(data_source.repository.iter_instances(USER_RESOURCE))
    assert len(resources) == 1
    assert resources[0].data["id"] == "user123"
    assert resources[0].data["name"] == "Alice"


def test_deeper_pointer(user_schema_builder):
    nested_schema = {
        "type": "object",
        "properties": {
            "data": {
                "type": "object",
                "properties": {
                    "users": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                            "required": ["id"],
                        },
                    }
                },
            }
        },
    }
    schema = user_schema_builder(response_schema=nested_schema)
    data_source = schema.create_extra_data_source()

    payload = {"data": {"users": [{"id": "nested1"}, {"id": "nested2"}]}}
    data_source.repository.record_response(operation=POST_USERS, status_code=CREATED, payload=payload)

    resources = list(data_source.repository.iter_instances(USER_RESOURCE))
    assert {instance.data["id"] for instance in resources} == {"nested1", "nested2"}


def test_prepopulate_from_response_examples(ctx):
    user_schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    spec = ctx.openapi.build_schema(
        {
            "/users": {
                "post": {
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": user_schema,
                                    "example": {"id": "example-user-123", "name": "Example User"},
                                }
                            }
                        }
                    }
                }
            },
            "/users/{user_id}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {"description": "Success", "content": {"application/json": {"schema": user_schema}}}
                    },
                }
            },
        }
    )
    schema = schemathesis.openapi.from_dict(spec)
    data_source = schema.create_extra_data_source()

    # Without calling record_response(), pool should already have the example value
    resources = list(data_source.repository.iter_instances(USER_RESOURCE))
    assert len(resources) == 1
    assert resources[0].data["id"] == "example-user-123"

    # Verify example is used in augmentation
    get_operation = schema["/users/{user_id}"]["GET"]
    path_schema = get_operation.path_parameters.schema
    augmented = data_source.augment(operation=get_operation, location=ParameterLocation.PATH, schema=path_schema)
    assert augmented["properties"]["user_id"]["anyOf"][1]["enum"] == ["example-user-123"]
