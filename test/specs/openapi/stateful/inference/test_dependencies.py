from __future__ import annotations

import pytest
from flask import Flask, jsonify, request
from syrupy.extensions.json import JSONSnapshotExtension

import schemathesis
from schemathesis.specs.openapi.stateful.dependencies import analyze, naming

KNOWN_INCORRECT_FIELD_MAPPINGS = {
    "merge-empty-schema-then-detailed": frozenset(
        [
            "Directory",
        ]
    ),
    "id-no-match": frozenset(
        [
            "Brand",
        ]
    ),
}


def make_user_paths(content, include_operation_id=True, post_status: str = "201"):
    """Build User CRUD paths with optional operationId."""
    post = {
        "responses": {post_status: {"content": content}},
    }
    if include_operation_id:
        post["operationId"] = "createUser"

    get = {
        "parameters": [
            {
                "name": "id",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        ],
    }
    if include_operation_id:
        get["operationId"] = "getUser"

    return {
        "/users": {"post": post},
        "/users/{id}": {"get": get},
    }


def response(status: str, schema=None, content_type: str = "application/json"):
    container = {"responses": {status: {"description": "Text"}}}
    if schema is not None:
        container["responses"][status]["content"] = {content_type: {"schema": schema}}
    return container


def path_param(name: str, param_type: str = "string", required: bool = True):
    return {"name": name, "in": "path", "required": required, "schema": {"type": param_type}}


def operation(method: str, path: str, response_status: str, response_schema=None, parameters=None, operation_id=None):
    operation = response(response_status, response_schema)
    if parameters:
        operation["parameters"] = parameters
    if operation_id:
        operation["operationId"] = operation_id
    return {path: {method: operation}}


def ref(ref_path: str):
    """Build a $ref object."""
    return {"$ref": ref_path}


def component_ref(schema_name: str):
    """Build a reference to a component schema."""
    return ref(f"#/components/schemas/{schema_name}")


def json_response(status: str, schema):
    """Shorthand for JSON response."""
    return response(status, schema, "application/json")


USER_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string"}},
    "required": ["id"],
}
SCHEMA_WITH_ID = {
    "type": "object",
    "properties": {"id": {"type": "string"}},
    "required": ["id"],
}


@pytest.fixture
def snapshot_json(snapshot):
    return snapshot.with_defaults(extension_class=JSONSnapshotExtension)


@pytest.mark.parametrize(
    ["paths", "components"],
    [
        pytest.param(
            make_user_paths(
                {
                    "application/json": {
                        "schema": USER_SCHEMA,
                    }
                }
            ),
            None,
            id="normal-schema",
        ),
        pytest.param(
            operation("post", "/users", "201", USER_SCHEMA),
            None,
            id="only-producer",
        ),
        pytest.param(
            {
                **operation("post", "/users", "201", component_ref("User")),
                **operation("post", "/users-2", "201", component_ref("User")),
            },
            {
                "schemas": {
                    "User": USER_SCHEMA,
                }
            },
            id="normal-schema-two-producers",
        ),
        pytest.param(
            {
                **operation("get", "/users/{id}", "200", component_ref("User"), [path_param("id")]),
                **operation("get", "/users/{userId}/messages", "200", component_ref("User"), [path_param("userId")]),
            },
            {
                "schemas": {
                    "User": USER_SCHEMA,
                }
            },
            id="different-names-for-same-resource-two-consumers",
        ),
        pytest.param(
            {
                **operation("delete", "/spaces/{id}", "200"),
                **operation("post", "/spaces/{spaceId}/topic", "201"),
            },
            None,
            id="different-names-for-same-resource",
        ),
        pytest.param(
            operation("get", "/users/{id}", "200", component_ref("User")),
            {
                "schemas": {
                    "User": USER_SCHEMA,
                }
            },
            id="only-consumer",
        ),
        pytest.param(
            make_user_paths(
                {
                    "application/json": {
                        "schema": USER_SCHEMA,
                    }
                },
                post_status="200",
            ),
            None,
            id="producer-non-201",
        ),
        pytest.param(
            make_user_paths({"application/json": {}}),
            None,
            id="missing-schema",
        ),
        pytest.param(
            make_user_paths(
                {
                    "application/json": {
                        "schema": True,
                    }
                }
            ),
            None,
            id="schema-true",
        ),
        pytest.param(
            make_user_paths({"application/json": {"schema": {}}}),
            None,
            id="schema-without-fields",
        ),
        pytest.param(
            make_user_paths(
                {
                    "application/json": {
                        "schema": component_ref("User"),
                    }
                },
            ),
            {"schemas": {"User": USER_SCHEMA}},
            id="ref-to-component",
        ),
        pytest.param(
            make_user_paths({"application/json": {"schema": USER_SCHEMA}}, include_operation_id=False),
            None,
            id="infer-from-path",
        ),
        pytest.param(
            operation("post", "/items", "400"),
            None,
            id="producer-response-no-match",
        ),
        pytest.param(
            operation("get", "/items", "400"),
            None,
            id="consumer-response-no-match",
        ),
        pytest.param(
            operation("get", "/items", "202"),
            None,
            id="consumer-response-non-200",
        ),
        pytest.param(
            operation("post", "/", "201", USER_SCHEMA),
            None,
            id="empty-path-producer",
        ),
        pytest.param(
            operation("get", "/", "200", USER_SCHEMA),
            None,
            id="empty-path-consumer",
        ),
        pytest.param(
            operation("post", "/", "201", True),
            None,
            id="empty-path-boolean",
        ),
        pytest.param(
            make_user_paths(
                {
                    "application/json": {
                        "schema": {
                            "$ref": "#/unknown",
                        }
                    }
                }
            ),
            None,
            id="unresolvable-ref",
        ),
        pytest.param(
            {"/path": {"get": {"responses": []}}},
            None,
            id="malformed_operation",
        ),
        pytest.param(
            {
                **operation(
                    "get",
                    "/users/{userId}/posts",
                    "200",
                    SCHEMA_WITH_ID,
                    [
                        path_param("userId"),
                        {"name": "limit", "in": "query", "required": False, "schema": {"type": "integer"}},
                    ],
                ),
                **operation("post", "/users", "201", SCHEMA_WITH_ID),
            },
            None,
            id="path-and-query-params",
        ),
        pytest.param(
            {
                **operation("post", "/users", "201", USER_SCHEMA),
                **operation(
                    "post",
                    "/users/{userId}/posts",
                    "201",
                    SCHEMA_WITH_ID,
                    [path_param("userId")],
                ),
                **operation(
                    "get",
                    "/users/{userId}/posts/{postId}",
                    "200",
                    parameters=[
                        path_param("userId"),
                        path_param("postId"),
                    ],
                ),
            },
            None,
            id="nested-resources",
        ),
        pytest.param(
            {
                **operation("post", "/channels", "201", SCHEMA_WITH_ID),
                **operation(
                    "get",
                    "/channels/{channel_id}/messages",
                    "200",
                    {
                        "type": "array",
                        "items": component_ref("Message"),
                    },
                    [
                        path_param("channel_id"),
                    ],
                ),
            },
            {
                "schemas": {
                    "Message": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                        "required": ["id", "text"],
                    }
                }
            },
            id="array-response-at-root",
        ),
        pytest.param(
            {
                **operation("get", "/channels/", "200", {"type": "array", "items": True}),
            },
            None,
            id="array-response-with-true",
        ),
        pytest.param(
            {
                **operation(
                    "get",
                    "/channels/{channel_id}/messages",
                    "200",
                    {
                        "type": "object",
                        "properties": {
                            "data": {
                                "type": "array",
                                "items": component_ref("Message"),
                            },
                            "total": {"type": "integer"},
                            "page": {"type": "integer"},
                        },
                    },
                    [path_param("channel_id")],
                ),
                **operation("post", "/channels", "201", SCHEMA_WITH_ID),
            },
            {
                "schemas": {
                    "Message": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                        "required": ["id", "text"],
                    }
                }
            },
            id="pagination-django-style",
        ),
        pytest.param(
            {
                **operation(
                    "get",
                    "/groups",
                    "200",
                    {
                        "properties": {
                            "nextLink": {"type": "string"},
                            "value": {"items": {"properties": {"id": {"type": "string"}}}, "type": "array"},
                        }
                    },
                ),
                **operation(
                    "get",
                    "/groups/{groupId}/users",
                    "200",
                    {
                        "properties": {
                            "nextLink": {"type": "string"},
                            "value": {"items": {"properties": {"id": {"type": "string"}}}, "type": "array"},
                        }
                    },
                ),
            },
            None,
            id="pagination-azure-style",
        ),
        pytest.param(
            {
                **operation(
                    "get",
                    "/groups",
                    "200",
                    {
                        "properties": {
                            "nextLink": {"type": "string"},
                            "random-field-with_items": {
                                "items": {"properties": {"id": {"type": "string"}}},
                                "type": "array",
                            },
                        }
                    },
                ),
            },
            None,
            id="pagination-unknown-style",
        ),
        pytest.param(
            {
                **operation(
                    "get",
                    "/channels/{channel_id}/messages",
                    "200",
                    component_ref("MessageList"),
                    [path_param("channel_id")],
                ),
                **operation("post", "/channels", "201", SCHEMA_WITH_ID),
            },
            {
                "schemas": {
                    "MessageList": {
                        "type": "object",
                        "properties": {
                            "data": {"type": "array", "items": component_ref("Message")},
                            "total": {"type": "integer"},
                            "page": {"type": "integer"},
                        },
                    },
                    "Message": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                        "required": ["id", "text"],
                    },
                }
            },
            id="pagination-django-style-behind-ref",
        ),
        pytest.param(
            operation(
                "get",
                "/users",
                "200",
                {
                    "type": "object",
                    "properties": {
                        "_embedded": {
                            "type": "object",
                            "properties": {
                                "another": True,
                                "users": {
                                    "type": "array",
                                    "items": SCHEMA_WITH_ID,
                                },
                            },
                        },
                        "page": {
                            "type": "object",
                            "properties": {
                                "size": {"type": "integer"},
                                "totalElements": {"type": "integer"},
                            },
                        },
                    },
                },
            ),
            None,
            id="pagination-hal-embedded",
        ),
        pytest.param(
            {
                **operation(
                    "get",
                    "/items/runner-groups",
                    "200",
                    {
                        "properties": {
                            "runner_groups": {
                                "type": "array",
                                "items": SCHEMA_WITH_ID,
                            },
                            "total_count": {"type": "integer"},
                        }
                    },
                ),
                **operation(
                    "delete",
                    "/items/{runner_group_id}",
                    "200",
                ),
            },
            None,
            id="pagination-github-style",
        ),
        pytest.param(
            operation(
                "get",
                "/users",
                "200",
                {
                    "type": "object",
                    "properties": {
                        "_embedded": {
                            "type": "object",
                        },
                        "page": {
                            "type": "object",
                            "properties": {
                                "size": {"type": "integer"},
                                "totalElements": {"type": "integer"},
                            },
                        },
                    },
                },
            ),
            None,
            id="pagination-hal-embedded-broken",
        ),
        pytest.param(
            {
                **operation("post", "/users", "201", component_ref("User")),
                **operation("get", "/users/{userId}", "200", component_ref("User"), [path_param("userId")]),
            },
            {
                "schemas": {
                    "BaseEntity": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "createdAt": {"type": "string", "format": "date-time"},
                            "updatedAt": {"type": "string", "format": "date-time"},
                        },
                        "required": ["id"],
                    },
                    "User": {
                        "allOf": [
                            component_ref("BaseEntity"),
                            {
                                "type": "object",
                                "properties": {"email": {"type": "string"}, "name": {"type": "string"}},
                                "required": ["email"],
                            },
                        ]
                    },
                }
            },
            id="allOf-composition",
        ),
        pytest.param(
            operation(
                "post",
                "/users",
                "201",
                {
                    "allOf": [
                        {
                            "$ref": component_ref("product-example-1/value/data/pas"),
                        }
                    ]
                },
            ),
            {
                "schemas": {
                    "product-example-1": {
                        "value": {
                            "data": {
                                "pas": None,
                            }
                        }
                    }
                }
            },
            id="bundling-error",
        ),
        pytest.param(
            {
                "/users": {
                    "get": json_response("200", component_ref("PagedUsers")),
                    "post": json_response("201", component_ref("User")),
                },
            },
            {
                "schemas": {
                    "User": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "email": {"type": "string"},
                            "name": {"type": "string"},
                        },
                        "required": ["id", "email"],
                    },
                    "PaginationMeta": {
                        "type": "object",
                        "properties": {
                            "page": {"type": "integer"},
                            "pageSize": {"type": "integer"},
                            "total": {"type": "integer"},
                        },
                    },
                    "PagedUsers": {
                        "allOf": [
                            component_ref("PaginationMeta"),
                            {
                                "type": "object",
                                "properties": {"data": {"type": "array", "items": component_ref("User")}},
                            },
                        ]
                    },
                }
            },
            id="pagination-with-allof-mixin",
        ),
        pytest.param(
            operation("get", "/merchants", "200", component_ref("MerchantsResponse")),
            {
                "schemas": {
                    "Merchant": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "category": {"type": "string"},
                        },
                        "required": ["id"],
                    },
                    "Merchants": {"type": "array", "items": component_ref("Merchant")},
                    "MerchantsResponse": {
                        "type": "object",
                        "properties": {"Merchants": component_ref("Merchants")},
                    },
                }
            },
            id="externally-tagged-many",
        ),
        pytest.param(
            operation("get", "/merchants", "200", component_ref("MerchantsResponse")),
            {
                "schemas": {
                    "Base": {"type": "object"},
                    "Foo": {"type": "object"},
                    "Merchant": {"allOf": [component_ref("Base"), component_ref("Foo")]},
                    "MerchantsResponse": {
                        "type": "object",
                        "properties": {"Merchants": component_ref("Merchant")},
                    },
                }
            },
            id="externally-tagged-all-of-multiple-inner-refs",
        ),
        pytest.param(
            operation("get", "/geo/adminDivisions", "200", component_ref("PopulatedPlacesResponse")),
            {
                "schemas": {
                    "BaseCollectionResponse": {
                        "type": "object",
                        "properties": {
                            "links": {"type": "array", "items": {"type": "object"}},
                            "metadata": {"type": "object", "properties": {"totalCount": {"type": "integer"}}},
                        },
                    },
                    "PopulatedPlaceSummary": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                            "country": {"type": "string"},
                            "latitude": {"type": "number"},
                            "longitude": {"type": "number"},
                        },
                        "required": ["id", "name"],
                    },
                    "PopulatedPlacesResponse": {
                        "allOf": [
                            component_ref("BaseCollectionResponse"),
                            {
                                "type": "object",
                                "properties": {
                                    "data": {
                                        "type": "array",
                                        "items": component_ref("PopulatedPlaceSummary"),
                                    }
                                },
                            },
                        ]
                    },
                }
            },
            id="allof-pagination-with-data",
        ),
        pytest.param(
            operation(
                "get",
                "/channels",
                "200",
                {
                    "oneOf": [
                        {
                            "type": "array",
                            "items": component_ref("ChannelDetails"),
                        },
                        {"type": "array", "items": {"type": "string"}},
                    ]
                },
            ),
            {
                "schemas": {
                    "ChannelDetails": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "memberCount": {"type": "integer"},
                        },
                        "required": ["id"],
                    }
                }
            },
            id="oneof-structured-vs-primitive-array",
        ),
        pytest.param(
            operation(
                "get",
                "/status",
                "200",
                {
                    "oneOf": [
                        component_ref("DetailedStatus"),
                        {"type": "string"},
                    ]
                },
            ),
            {
                "schemas": {
                    "DetailedStatus": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "state": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
            id="oneof-object-vs-primitive",
        ),
        pytest.param(
            operation(
                "get",
                "/items",
                "200",
                {
                    "oneOf": [
                        component_ref("Product"),
                        component_ref("Service"),
                    ]
                },
            ),
            {
                "schemas": {
                    "Product": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "price": {"type": "number"}},
                    },
                    "Service": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "duration": {"type": "integer"}},
                    },
                }
            },
            id="oneof-multiple-structured-objects",
        ),
        pytest.param(
            operation(
                "get",
                "/data",
                "200",
                {
                    "anyOf": [
                        component_ref("DataRecord"),
                        {"type": "null"},
                    ]
                },
            ),
            {
                "schemas": {
                    "DataRecord": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "value": {"type": "string"}},
                    }
                }
            },
            id="anyof-object-vs-null",
        ),
        pytest.param(
            operation(
                "get",
                "/data",
                "200",
                {
                    "anyOf": [
                        {"type": "null"},
                        {"type": "string"},
                    ]
                },
            ),
            None,
            id="anyof-primitive-types",
        ),
        pytest.param(
            operation(
                "get",
                "/data",
                "200",
                {
                    "anyOf": [
                        {"items": True, "type": "array"},
                        {"allOf": [True]},
                    ]
                },
            ),
            None,
            id="anyof-nested-complex-types",
        ),
        pytest.param(
            operation("get", "/data", "200", {"anyOf": 42}),
            None,
            id="anyof-invalid",
        ),
        pytest.param(
            {
                **operation("get", "/directories/{id}", "200", {}, [path_param("id")]),
                **operation("get", "/files/{id}", "200", {}, [path_param("id")]),
                **operation(
                    "get",
                    "/oauth2/1/files/{id}",
                    "200",
                    {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "details": {"type": "object"},
                            "metadata": {"type": "string"},
                        },
                    },
                    [path_param("id")],
                ),
            },
            None,
            id="merge-empty-schema-then-detailed",
        ),
        pytest.param(
            operation(
                "get",
                "/tags/{resourceArn}",
                "200",
                component_ref("ListTagsForResourceOutput"),
                [path_param("resourceArn")],
            ),
            {
                "schemas": {
                    "ListTagsForResourceOutput": {
                        "type": "object",
                        "required": ["tags"],
                        "properties": {
                            "tags": {
                                "allOf": [
                                    component_ref("TagMap"),
                                    {},
                                ]
                            },
                        },
                    },
                    "TagMap": {
                        "type": "object",
                        "minProperties": 0,
                        "maxProperties": 200,
                        "properties": {"id": {"type": "string"}},
                    },
                }
            },
            id="allOf-inside-tagged-union",
        ),
        pytest.param(
            operation(
                "get",
                "/tags/{resourceArn}",
                "200",
                component_ref("ListTagsForResourceOutput"),
                [path_param("resourceArn")],
            ),
            {
                "schemas": {
                    "ListTagsForResourceOutput": {
                        "type": "object",
                        "required": ["tags"],
                        "properties": {
                            "tag": True,
                            "tags": {
                                "allOf": [
                                    True,
                                    component_ref("TagList"),
                                    {},
                                ]
                            },
                        },
                    },
                    "TagList": {
                        "items": component_ref("Tag"),
                    },
                    "Tag": {
                        "properties": {"id": {"type": "string"}},
                    },
                }
            },
            id="allOf-inside-tagged-union-with-items",
        ),
        pytest.param(
            {
                **operation("get", "/brands/{brandId}", "200", component_ref("Brand"), [path_param("brandId")]),
                **operation("post", "/brands", "201", component_ref("Brand")),
            },
            {
                "schemas": {
                    "Brand": {
                        "type": "object",
                        "required": ["BrandId"],
                        "properties": {
                            "BrandId": {"type": "string"},
                        },
                    },
                }
            },
            id="id-match-to-pascal-case",
        ),
        pytest.param(
            {
                **operation("get", "/brands/{id}", "200", component_ref("Brand"), [path_param("id")]),
                **operation("post", "/brands", "201", component_ref("Brand")),
            },
            {
                "schemas": {
                    "Brand": {
                        "type": "object",
                        "required": ["brandId"],
                        "properties": {
                            "brandId": {"type": "string"},
                        },
                    },
                }
            },
            id="id-match-id-to-prefixed",
        ),
        pytest.param(
            {
                **operation("get", "/brands/{id}", "200", component_ref("Brand"), [path_param("id")]),
                **operation("post", "/brands", "201", component_ref("Brand")),
            },
            {
                "schemas": {
                    "Brand": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string"},
                        },
                    },
                }
            },
            id="id-no-match",
        ),
        pytest.param(
            {
                **operation("get", "/brands/{id}", "200", component_ref("Brand"), [path_param("id")]),
                **operation("post", "/brands", "201", component_ref("Brand")),
            },
            {
                "schemas": {
                    "Brand": {
                        "type": "object",
                        "required": ["id"],
                        "properties": {
                            "id": {"type": "string"},
                        },
                    },
                }
            },
            id="id-direct-match",
        ),
    ],
)
def test_dependency_graph(request, ctx, paths, components, snapshot_json):
    kwargs = {}
    if components is not None:
        kwargs["components"] = components
    raw_schema = ctx.openapi.build_schema(paths, **kwargs)
    schema = schemathesis.openapi.from_dict(raw_schema)

    graph = analyze(schema)

    graph.assert_incorrect_field_mappings(request.node.callspec.id, KNOWN_INCORRECT_FIELD_MAPPINGS)
    assert graph.serialize() == snapshot_json

    data = list(graph.iter_links())
    for response_links in data:
        source = schema.get_operation_by_reference(response_links.producer_operation_ref)
        assert response_links.status_code in source.responses.status_codes
        for link in response_links.links.values():
            _ = schema.get_operation_by_reference(link.operation_ref)

    assert [[entry.status_code, entry.producer_operation_ref, entry.to_openapi()] for entry in data] == snapshot_json


@pytest.mark.parametrize(
    ["paths", "kwargs", "version"],
    [
        (
            {
                "/test": {
                    "put": {
                        "responses": {
                            "201": {
                                "schema": {
                                    "$ref": "#/definitions/WebService",
                                }
                            }
                        }
                    }
                }
            },
            {
                "definitions": {
                    "WebService": {
                        "allOf": [],
                        "properties": {
                            "key1": {
                                "$ref": "#/definitions/WebServiceProperties",
                            }
                        },
                    },
                    "WebServiceProperties": {
                        "properties": {
                            "key2": {
                                "$ref": "",
                            }
                        }
                    },
                }
            },
            "2.0",
        ),
        (
            operation("get", "/test", "200", component_ref("EmployeesResponse")),
            {"components": {"schemas": {"EmployeesResponse": {"allOf": [{"$ref": ""}]}}}},
            "3.0.0",
        ),
    ],
)
def test_recursion(ctx, paths, kwargs, version, snapshot_json):
    raw_schema = ctx.openapi.build_schema(paths, **kwargs, version=version)

    schema = schemathesis.openapi.from_dict(raw_schema)

    graph = analyze(schema)
    assert graph.serialize() == snapshot_json


@pytest.mark.parametrize(
    ["path", "expected"],
    [
        pytest.param("/users", "User", id="simple-plural"),
        pytest.param("/posts", "Post", id="simple-plural-posts"),
        pytest.param("/user", "User", id="already-singular"),
        pytest.param("/users/{id}", "User", id="with-path-param"),
        pytest.param("/users/{userId}", "User", id="with-named-param"),
        pytest.param("/posts/{postId}", "Post", id="posts-with-param"),
        pytest.param("/api/users", "User", id="api-prefix"),
        pytest.param("/v1/users", "User", id="version-prefix"),
        pytest.param("/api/v1/users", "User", id="api-and-version"),
        pytest.param("/api/v2/posts", "Post", id="api-v2-posts"),
        pytest.param("/users/{userId}/posts", "Post", id="nested-resource"),
        pytest.param("/users/{userId}/posts/{postId}", "Post", id="nested-with-params"),
        pytest.param("/organizations/{orgId}/teams/{teamId}/members", "Member", id="deeply-nested"),
        pytest.param("/user-profiles", "UserProfile", id="kebab-case"),
        pytest.param("/user_profiles", "UserProfile", id="snake-case"),
        pytest.param("/api-keys", "ApiKey", id="kebab-case-api-keys"),
        pytest.param("/categories", "Category", id="ies-to-y"),
        pytest.param("/statuses", "Status", id="es-to-empty"),
        pytest.param("/addresses", "Address", id="es-address"),
        pytest.param("/users/", "User", id="trailing-slash"),
        pytest.param("/api/v1/posts/", "Post", id="trailing-slash-with-prefix"),
        pytest.param("", None, id="empty path"),
    ],
)
def test_resource_name_from_path(path, expected):
    assert naming.from_path(path) == expected


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_schema_inference_discovers_state_corruption(cli, app_runner, snapshot_cli, ctx):
    product_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "price": {"type": "number"},
        },
        "required": ["id", "name", "price"],
    }
    schema = ctx.openapi.build_schema(
        {
            "/products": {
                "post": {
                    "operationId": "createProduct",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "price": {
                                            "type": "number",
                                        },
                                    },
                                    "required": ["name", "price"],
                                }
                            }
                        }
                    },
                    "responses": {
                        "201": {
                            "description": "Product created",
                            "content": {"application/json": {"schema": product_schema}},
                        }
                    },
                }
            },
            "/products/{productId}": {
                "get": {
                    "operationId": "getProduct",
                    "parameters": [
                        {
                            "name": "productId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Product details",
                            "content": {"application/json": {"schema": product_schema}},
                        },
                        "404": {"description": "Not found"},
                    },
                },
                "patch": {
                    "operationId": "updateProduct",
                    "parameters": [{"name": "productId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}, "price": {"type": "number"}},
                                }
                            }
                        }
                    },
                    "responses": {"204": {"description": "Updated"}, "404": {"description": "Not found"}},
                },
            },
        }
    )

    app = Flask(__name__)
    products = {}
    next_id = 1

    @app.route("/openapi.json")
    def get_schema():
        return jsonify(schema)

    @app.route("/products", methods=["POST"])
    def create_product():
        nonlocal next_id
        data = request.get_json() or {}
        if not isinstance(data, dict):
            return {"error": "Invalid input"}
        if not isinstance(data.get("price", 0), (int, float)):
            return {"error": "Invalid price"}

        product_id = str(next_id)
        next_id += 1

        products[product_id] = {
            "id": product_id,
            "name": str(data.get("name", "Product")),
            "price": float(data.get("price", 9.99)),
            "corrupted": False,
        }

        return jsonify(
            {
                "id": product_id,
                "name": products[product_id]["name"],
                "price": products[product_id]["price"],
            }
        ), 201

    @app.route("/products/<product_id>", methods=["GET"])
    def get_product(product_id):
        if product_id not in products:
            return "", 404

        product = products[product_id]

        # Corrupted products return null price (violates schema)
        if product.get("corrupted"):
            return jsonify(
                {
                    "id": str(1),
                    "name": product["name"],
                    "price": None,  # Schema requires number, not null
                }
            ), 200

        return jsonify({"id": product_id, "name": product["name"], "price": product["price"]}), 200

    @app.route("/products/<product_id>", methods=["PATCH"])
    def update_product(product_id):
        if product_id not in products:
            return "", 404

        data = request.get_json() or {}
        if not isinstance(data, dict):
            return {"error": "Invalid input"}

        # PATCH with empty body corrupts internal state
        if not data.get("name") and not data.get("price"):
            products[product_id]["corrupted"] = True
        else:
            if "name" in data:
                products[product_id]["name"] = str(data["name"])
            if "price" in data:
                products[product_id]["price"] = float(data["price"])

        return "", 204

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            "--max-examples=10",
            "-c response_schema_conformance",
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=stateful",
        )
        == snapshot_cli
    )
