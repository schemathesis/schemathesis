from __future__ import annotations

import pytest
import requests
from hypothesis import given, settings

import schemathesis
from schemathesis.config import GenerationConfig
from schemathesis.core import deserialization
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import Response
from schemathesis.generation.modes import GenerationMode
from schemathesis.resources.repository import MAX_CONTEXTS_PER_TYPE, PER_CONTEXT_CAPACITY
from schemathesis.specs.openapi.extra_data_source import ParameterRequirement
from schemathesis.specs.openapi.negative import GeneratedValue

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


def test_data_source_provides_captured_variants(user_schema_builder):
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

    variants = data_source.get_captured_variants(
        operation=get_operation, location=ParameterLocation.PATH, schema=path_params_schema
    )

    assert variants is not None
    assert len(variants) == 2
    assert {"user_id": "1"} in variants
    assert {"user_id": "2"} in variants


def test_wildcard_status_code_matching(user_schema_builder):
    schema = user_schema_builder(status_code="2XX")
    data_source = schema.create_extra_data_source()

    data_source.repository.record_response(operation=POST_USERS, status_code=201, payload={"id": "1"})
    data_source.repository.record_response(operation=POST_USERS, status_code=404, payload={"id": "2"})

    resources = list(data_source.repository.iter_instances(USER_RESOURCE))
    assert len(resources) == 1
    assert resources[0].data["id"] == "1"


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
            PER_CONTEXT_CAPACITY,  # All have same (empty) context, so limited by per-context capacity
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

    # Verify example is used in captured variants
    get_operation = schema["/users/{user_id}"]["GET"]
    path_schema = get_operation.path_parameters.schema
    variants = data_source.get_captured_variants(
        operation=get_operation, location=ParameterLocation.PATH, schema=path_schema
    )
    assert variants == [{"user_id": "example-user-123"}]


def test_object_level_augmentation_preserves_relationships(ctx):
    user_schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    post_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "userId": {"type": "string"}},
        "required": ["id", "userId"],
    }
    spec = ctx.openapi.build_schema(
        {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "responses": {"201": {"content": {"application/json": {"schema": user_schema}}}},
                }
            },
            "/users/{userId}/posts": {
                "post": {
                    "operationId": "createPost",
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"201": {"content": {"application/json": {"schema": post_schema}}}},
                }
            },
            "/users/{userId}/posts/{postId}": {
                "get": {
                    "operationId": "getPost",
                    "parameters": [
                        {"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "postId", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"content": {"application/json": {"schema": post_schema}}}},
                }
            },
        }
    )
    schema = schemathesis.openapi.from_dict(spec)
    data_source = schema.create_extra_data_source()

    # Record a post creation with userId in context (from path parameter)
    data_source.repository.record_response(
        operation="POST /users/{userId}/posts",
        status_code=201,
        payload={"id": "post-123", "userId": "user-456"},
        context={"userId": "user-456"},  # Path parameter from request
    )

    # Get the path parameters for GET /users/{userId}/posts/{postId}
    get_operation = schema["/users/{userId}/posts/{postId}"]["GET"]
    path_schema = get_operation.path_parameters.schema

    # get_captured_variants() returns the variants for hybrid strategy
    variants = data_source.get_captured_variants(
        operation=get_operation, location=ParameterLocation.PATH, schema=path_schema
    )
    assert variants is not None
    assert len(variants) == 1

    # Variant contains both userId and postId with correct values
    variant = variants[0]
    assert variant["userId"] == "user-456"
    assert variant["postId"] == "post-123"


def test_context_aware_eviction_maintains_diversity(ctx):
    pet_schema = {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}
    spec = ctx.openapi.build_schema(
        {
            "/owners/{ownerId}/pets": {
                "post": {
                    "operationId": "createPet",
                    "parameters": [{"name": "ownerId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"201": {"content": {"application/json": {"schema": pet_schema}}}},
                }
            },
        }
    )
    schema = schemathesis.openapi.from_dict(spec)
    data_source = schema.create_extra_data_source()

    # Record pets from multiple owners - more than would fit in a single context bucket
    for owner_id in range(1, 6):
        for pet_id in range(1, 100):
            data_source.repository.record_response(
                operation="POST /owners/{ownerId}/pets",
                status_code=201,
                payload={"id": pet_id + owner_id * 100},
                context={"ownerId": owner_id},
            )

    instances = data_source.repository.iter_instances("Pet")

    # Should have instances from all 5 owners (context diversity preserved)
    owner_ids = {inst.context.get("ownerId") for inst in instances}
    assert len(owner_ids) == 5

    # Each owner should have at most PER_CONTEXT_CAPACITY pets
    for owner_id in range(1, 6):
        owner_pets = [inst for inst in instances if inst.context.get("ownerId") == owner_id]
        assert len(owner_pets) <= PER_CONTEXT_CAPACITY

    # Total instances capped by contexts * per-context capacity
    assert len(instances) <= MAX_CONTEXTS_PER_TYPE * PER_CONTEXT_CAPACITY


def test_negative_aware_strategy_with_captured_values(ctx):
    spec = ctx.openapi.build_schema(
        {
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Success"}},
                }
            },
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "string"}}}
                                }
                            },
                            "links": {
                                "GetItem": {"operationId": "getItem", "parameters": {"id": "$response.body#/id"}}
                            },
                        }
                    },
                }
            },
        }
    )
    schema = schemathesis.openapi.from_dict(spec)
    data_source = schema.create_extra_data_source()

    for i in range(5):
        data_source.repository.record_response(operation="POST /items", status_code=201, payload={"id": f"item-{i}"})

    operation = schema["/items/{id}"]["GET"]
    config = GenerationConfig()

    strategy = operation.path_parameters.get_strategy(
        operation, config, GenerationMode.NEGATIVE, extra_data_source=data_source
    )

    results = []

    @given(strategy)
    @settings(max_examples=20, database=None)
    def collect_samples(value):
        results.append(value)

    collect_samples()

    assert all(isinstance(r, GeneratedValue) for r in results)


def test_negative_aware_strategy_with_captured_values_body(ctx):
    spec = ctx.openapi.build_schema(
        {
            "/projects": {
                "post": {
                    "operationId": "createProject",
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "string"}}}
                                }
                            },
                            "links": {
                                "CreateTask": {
                                    "operationId": "createTask",
                                    "parameters": {"project_id": "$response.body#/id"},
                                }
                            },
                        }
                    },
                }
            },
            "/tasks": {
                "post": {
                    "operationId": "createTask",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"project_id": {"type": "string"}, "title": {"type": "string"}},
                                    "required": ["project_id"],
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {"201": {"description": "Created"}},
                }
            },
        }
    )
    schema = schemathesis.openapi.from_dict(spec)
    data_source = schema.create_extra_data_source()

    for i in range(5):
        data_source.repository.record_response(
            operation="POST /projects", status_code=201, payload={"id": f"project-{i}"}
        )

    operation = schema["/tasks"]["POST"]
    config = GenerationConfig()
    body = operation.body[0]

    strategy = body.get_strategy(operation, config, GenerationMode.NEGATIVE, extra_data_source=data_source)

    results = []

    @given(strategy)
    @settings(max_examples=20, database=None)
    def collect_samples(value):
        results.append(value)

    collect_samples()

    assert all(isinstance(r, GeneratedValue) for r in results)
