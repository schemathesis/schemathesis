import re

import pytest
from flask import jsonify, request

from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.resources import PoolDraw, PoolPick
from test.coverage.helpers import body_operation, iter_cases, make_request_body

# Consumer responses declare `name` as required; the planted-bug handlers return it as null for known ids.
PLANTED_BUG_RESPONSES = {
    "200": {
        "description": "OK",
        "content": {
            "application/json": {
                "schema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
            }
        },
    },
    "404": {"description": "Not found"},
}


def _path_parameter(name, schema=None):
    return {"name": name, "in": "path", "required": True, "schema": schema or {"type": "string"}}


def _id_body(name, schema):
    return make_request_body({"type": "object", "required": [name], "properties": {name: schema}})


def _producer_consumer_paths(resource, id_type):
    # `POST /<resource>s` returns an `id`; `GET /<resource>s/{<resource>Id}` takes it back as a path parameter.
    name = resource.capitalize()
    return {
        f"/{resource}s": {
            "post": {
                "operationId": f"create{name}",
                "responses": {
                    "201": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"id": {"type": id_type}},
                                    "required": ["id"],
                                }
                            }
                        }
                    }
                },
            }
        },
        f"/{resource}s/{{{resource}Id}}": {
            "get": {
                "operationId": f"get{name}",
                "parameters": [_path_parameter(f"{resource}Id", {"type": id_type})],
                "responses": {"200": {"description": "OK"}},
            }
        },
    }


def _json_string_field(name):
    data = request.get_json(silent=True)
    value = data.get(name) if isinstance(data, dict) else None
    return value if isinstance(value, str) else None


def _planted_bug_lookup(registry, key):
    if key not in registry:
        return "", 404
    # Planted bug: required `name` is null for entries that exist
    return jsonify({"name": None}), 200


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_consumes_path_keyed_pool(cli, snapshot_cli, ctx):
    paths = {
        "/widgets/{widgetId}": {
            "post": {
                "operationId": "createWidget",
                "parameters": [_path_parameter("widgetId", {"type": "string", "format": "uuid"})],
                "responses": {"201": {"description": "Created"}},
            },
            "get": {
                "operationId": "getWidget",
                "parameters": [_path_parameter("widgetId", {"type": "string", "format": "uuid"})],
                "responses": PLANTED_BUG_RESPONSES,
            },
        }
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    widgets: set[str] = set()

    @app.route("/widgets/<widget_id>", methods=["POST"])
    def create_widget(widget_id):
        widgets.add(widget_id)
        return "", 201

    @app.route("/widgets/<widget_id>", methods=["GET"])
    def get_widget(widget_id):
        return _planted_bug_lookup(widgets, widget_id)

    assert cli.run_openapi_app(app, "--phases=coverage", "-c response_schema_conformance") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_param_mutation_preserves_nested_overlay_siblings(cli, ctx, snapshot_cli):
    # Nested overlay must keep generator-produced siblings (`note`) when the pool seeds a foreign-key leaf (`location_id`).
    paths = {
        "/locations": {
            "post": {
                "operationId": "createLocation",
                "responses": {
                    "201": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["id"],
                                    "properties": {"id": {"type": "integer"}},
                                }
                            }
                        }
                    }
                },
            }
        },
        "/departments": {
            "post": {
                "operationId": "createDepartment",
                "parameters": [
                    {
                        "name": "X-Required-Header",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "requestBody": make_request_body(
                    {
                        "type": "object",
                        "required": ["shipping"],
                        "properties": {
                            "shipping": {
                                "type": "object",
                                "required": ["note"],
                                "properties": {
                                    "location_id": {"type": "integer"},
                                    "note": {"type": "string"},
                                },
                            }
                        },
                    }
                ),
                "responses": {"201": {"description": "OK"}},
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/locations", methods=["POST"])
    def locations():
        return jsonify({"id": 42}), 201

    @app.route("/departments", methods=["POST"])
    def departments():
        body = request.get_json(silent=True)
        shipping = body.get("shipping") if isinstance(body, dict) else None
        if not isinstance(shipping, dict) or not isinstance(shipping.get("note"), str):
            return ("", 422)
        return ("", 201)

    assert cli.run_openapi_app(app, "--phases=coverage", "--continue-on-failure") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_consumes_body_field_keyed_pool(cli, snapshot_cli, ctx):
    paths = {
        "/sessions": {
            "post": {
                "operationId": "createSession",
                "requestBody": _id_body("sessionId", {"type": "string", "format": "uuid"}),
                "responses": {"201": {"description": "Created"}},
            }
        },
        "/sessions/{sessionId}/events": {
            "post": {
                "operationId": "createEvent",
                "parameters": [_path_parameter("sessionId", {"type": "string", "format": "uuid"})],
                "requestBody": make_request_body(
                    {
                        "type": "object",
                        "required": ["sessionId", "kind"],
                        "properties": {
                            "sessionId": {"type": "string", "format": "uuid"},
                            "kind": {"type": "string"},
                        },
                    }
                ),
                "responses": PLANTED_BUG_RESPONSES,
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    sessions: set[str] = set()

    @app.route("/sessions", methods=["POST"])
    def create_session():
        session_id = _json_string_field("sessionId")
        if session_id is None:
            return "", 400
        sessions.add(session_id)
        return "", 201

    @app.route("/sessions/<session_id>/events", methods=["POST"])
    def create_event(session_id):
        return _planted_bug_lookup(sessions, session_id)

    assert cli.run_openapi_app(app, "--phases=coverage", "-c response_schema_conformance") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_correlates_nested_resource_pool_picks(cli, snapshot_cli, ctx):
    # Independent picks return (U2, R1) but R1's parent is U1; only correlation matches the planted pair.
    paths = {
        "/products": {
            "post": {
                "operationId": "createProduct",
                "requestBody": _id_body(
                    "productId", {"type": "string", "examples": ["alpha-product-7af3", "bravo-product-9c11"]}
                ),
                "responses": {"201": {"description": "Created"}},
            }
        },
        "/products/{productId}/reviews": {
            "post": {
                "operationId": "createReview",
                "parameters": [_path_parameter("productId")],
                "requestBody": _id_body("reviewId", {"type": "string", "examples": ["alpha-review-1234"]}),
                "responses": {"201": {"description": "Created"}},
            }
        },
        "/products/{productId}/reviews/{reviewId}": {
            "get": {
                "operationId": "getReview",
                "parameters": [_path_parameter("productId"), _path_parameter("reviewId")],
                "responses": PLANTED_BUG_RESPONSES,
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    products: set[str] = set()
    reviews: set[tuple[str, str]] = set()

    @app.route("/products", methods=["POST"])
    def create_product():
        product_id = _json_string_field("productId")
        if product_id is None:
            return "", 400
        products.add(product_id)
        return "", 201

    @app.route("/products/<product_id>/reviews", methods=["POST"])
    def create_review(product_id):
        if product_id not in products:
            return "", 404
        review_id = _json_string_field("reviewId")
        if review_id is None:
            return "", 400
        reviews.add((product_id, review_id))
        return "", 201

    @app.route("/products/<product_id>/reviews/<review_id>", methods=["GET"])
    def get_review(product_id, review_id):
        return _planted_bug_lookup(reviews, (product_id, review_id))

    assert cli.run_openapi_app(app, "--phases=coverage", "-c response_schema_conformance") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_negative_does_not_pollute_pool_with_invalid_values(cli, snapshot_cli, ctx):
    # A permissive endpoint's negative mutations must not seed the pool with values a strict endpoint would later reject.
    paths = {
        "/payments": {
            "post": {
                "operationId": "createPayment",
                "requestBody": _id_body("customerId", {"type": "string"}),
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad request"}},
            }
        },
        "/audit": {
            "post": {
                "operationId": "createAuditEntry",
                "requestBody": _id_body("customerId", {"type": "string"}),
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/customers/{customerId}": {
            "get": {
                "operationId": "getCustomer",
                "parameters": [_path_parameter("customerId")],
                "responses": {"200": {"description": "OK"}},
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/payments", methods=["POST"])
    def payments():
        if _json_string_field("customerId") is None:
            return "", 400
        return "", 200

    @app.route("/audit", methods=["POST"])
    def audit():
        return "", 200

    @app.route("/customers/<customer_id>", methods=["GET"])
    def get_customer(customer_id):
        return "", 200

    assert cli.run_openapi_app(app, "--phases=coverage", "-c positive_data_acceptance") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_pool_overlay_respects_stricter_destination_constraints(cli, snapshot_cli, ctx):
    # A loose endpoint contributes a value valid only for itself; a stricter consumer must not adopt it.
    paths = {
        "/clients": {
            "post": {
                "operationId": "createClient",
                "requestBody": _id_body("clientId", {"type": "string"}),
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/identity-providers": {
            "put": {
                "operationId": "putIdentityProvider",
                "requestBody": _id_body("clientId", {"type": "string", "minLength": 1}),
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad request"}},
            }
        },
        "/clients/{clientId}": {
            "get": {
                "operationId": "getClient",
                "parameters": [_path_parameter("clientId")],
                "responses": {"200": {"description": "OK"}},
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/clients", methods=["POST"])
    def create_client():
        return "", 200

    @app.route("/identity-providers", methods=["PUT"])
    def put_identity_provider():
        if not _json_string_field("clientId"):
            return "", 400
        return "", 200

    @app.route("/clients/<client_id>", methods=["GET"])
    def get_client(client_id):
        return "", 200

    assert cli.run_openapi_app(app, "--phases=coverage", "-c positive_data_acceptance") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_pool_overlay_respects_destination_format(cli, snapshot_cli, ctx):
    # A producer with no `format` constraint must not contribute values that violate a consumer's `format: uuid`.
    # The producer caps `txnId` length at 5 — no value can satisfy uuid (36 chars), so any pool injection fails.
    paths = {
        "/a-create": {
            "post": {
                "operationId": "createSession",
                "requestBody": _id_body("txnId", {"type": "string", "maxLength": 5}),
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/z-confirm": {
            "post": {
                "operationId": "confirmAuth",
                "requestBody": _id_body("txnId", {"type": "string", "format": "uuid"}),
                "responses": {"200": {"description": "OK"}, "400": {"description": "Bad request"}},
            }
        },
        "/sessions/{txnId}": {
            "get": {
                "operationId": "getSession",
                "parameters": [_path_parameter("txnId")],
                "responses": {"200": {"description": "OK"}},
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/a-create", methods=["POST"])
    def create_session():
        return "", 200

    @app.route("/z-confirm", methods=["POST"])
    def confirm_auth():
        txn_id = _json_string_field("txnId")
        if txn_id is None or not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", txn_id
        ):
            return "", 400
        return "", 200

    @app.route("/sessions/<txn_id>", methods=["GET"])
    def get_session(txn_id):
        return "", 200

    assert cli.run_openapi_app(app, "--phases=coverage", "-c positive_data_acceptance") == snapshot_cli


def test_coverage_pool_overlay_dict_value_with_undeclared_keys(ctx):
    # Pool object value for "address" contains "country", absent from the property schema.
    operation = body_operation(
        ctx,
        {
            "type": "object",
            "properties": {"address": {"type": "object", "properties": {"city": {"type": "string"}}}},
        },
    )

    class _FakeDataSource:
        def pick_correlated_values(self, *, operation):
            return PoolPick(values={(ParameterLocation.BODY, "address"): {"city": "London", "country": "UK"}})

    iter_cases(operation, GenerationMode.POSITIVE, extra_data_source=_FakeDataSource())


def test_coverage_pool_draws_multi_slot_correlated(ctx):
    # Both path params draw from the post-creating operation: its `id` and the `userId` captured as context.
    user_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }
    post_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "userId": {"type": "string"}},
        "required": ["id", "userId"],
    }
    schema = ctx.openapi.load_schema(
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
                    "parameters": [_path_parameter("userId")],
                    "responses": {"201": {"content": {"application/json": {"schema": post_schema}}}},
                }
            },
            "/users/{userId}/posts/{postId}": {
                "get": {
                    "operationId": "getPost",
                    "parameters": [_path_parameter("userId"), _path_parameter("postId")],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()
    data_source.repository.record_response(
        operation="POST /users/{userId}/posts",
        status_code=201,
        payload={"id": "post-7", "userId": "user-1"},
        context={"userId": "user-1"},
    )

    consumer = schema["/users/{userId}/posts/{postId}"]["GET"]
    cases = iter_cases(consumer, GenerationMode.POSITIVE, extra_data_source=data_source)
    assert cases
    expected_source = "POST /users/{userId}/posts"
    assert {d.parameter_name: d for d in cases[0].meta.pool_draws} == {
        "userId": PoolDraw(
            location=ParameterLocation.PATH.value,
            parameter_name="userId",
            resource_name="User",
            resource_field="id",
            source_operation=expected_source,
            source_status=201,
        ),
        "postId": PoolDraw(
            location=ParameterLocation.PATH.value,
            parameter_name="postId",
            resource_name="Post",
            resource_field="id",
            source_operation=expected_source,
            source_status=201,
        ),
    }


def test_coverage_attaches_pool_draws_to_consumer_cases(ctx):
    # POST captures `id`; coverage cases for GET /albums/{id} carry pool-draw provenance back to POST.
    user_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    schema = ctx.openapi.load_schema(
        {
            "/albums": {
                "post": {
                    "operationId": "createAlbum",
                    "responses": {
                        "201": {"description": "Created", "content": {"application/json": {"schema": user_schema}}}
                    },
                }
            },
            "/albums/{albumId}": {
                "get": {
                    "operationId": "getAlbum",
                    "parameters": [_path_parameter("albumId")],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    data_source = schema.create_extra_data_source()
    data_source.repository.record_response(
        operation="POST /albums", status_code=201, payload={"id": "alb-42", "name": "First"}
    )

    consumer = schema["/albums/{albumId}"]["GET"]
    cases = iter_cases(consumer, GenerationMode.POSITIVE, extra_data_source=data_source)
    assert cases, "expected at least one coverage case for the consumer operation"
    assert cases[0].meta.pool_draws == (
        PoolDraw(
            location=ParameterLocation.PATH.value,
            parameter_name="albumId",
            resource_name="Album",
            resource_field="id",
            source_operation="POST /albums",
            source_status=201,
        ),
    )


def test_pool_inventory_respects_operation_filters(ctx):
    # Filtered-out operations must not count as missing producers/consumers in the inventory.
    schema = ctx.openapi.load_schema(
        {**_producer_consumer_paths("item", "string"), **_producer_consumer_paths("widget", "string")}
    )
    full_inventory = schema._measure_statistic().resource_pool
    assert set(full_inventory.producer_labels) == {"POST /items", "POST /widgets"}
    assert set(full_inventory.consumer_labels) == {"GET /items/{itemId}", "GET /widgets/{widgetId}"}

    filtered = schema.include(path_regex="/items")._measure_statistic().resource_pool
    assert filtered.producer_labels == ["POST /items"]
    assert filtered.consumer_labels == ["GET /items/{itemId}"]
    assert filtered.resources == 1


def test_coverage_pool_draws_survive_numeric_id_serialization(ctx):
    # The pooled integer id reaches the wire as `"42"` but the draw still attributes to the integer producer.
    schema = ctx.openapi.load_schema(_producer_consumer_paths("item", "integer"))
    data_source = schema.create_extra_data_source()
    data_source.repository.record_response(operation="POST /items", status_code=201, payload={"id": 42})

    consumer = schema["/items/{itemId}"]["GET"]
    cases = iter_cases(consumer, GenerationMode.POSITIVE, extra_data_source=data_source)
    assert cases
    assert cases[0].meta.pool_draws == (
        PoolDraw(
            location=ParameterLocation.PATH.value,
            parameter_name="itemId",
            resource_name="Item",
            resource_field="id",
            source_operation="POST /items",
            source_status=201,
        ),
    )
