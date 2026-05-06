from __future__ import annotations

import base64
import json

import pytest
from flask import jsonify, request

from schemathesis.resources.descriptors import Cardinality, ResourceDescriptor
from schemathesis.resources.repository import ResourceRepository
from schemathesis.specs.openapi.auth_jwt import seed_pool_from_headers
from schemathesis.specs.openapi.extra_data_source import ParameterRequirement


def _make_jwt(payload: dict) -> str:
    def encode(part: dict) -> str:
        raw = json.dumps(part, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{encode({'alg': 'HS256', 'typ': 'JWT'})}.{encode(payload)}.signature-not-verified"


def _new_repository(resource_names: list[str]) -> ResourceRepository:
    descriptors = [
        ResourceDescriptor(name, f"GET /{name.lower()}", "200", "", Cardinality.ONE) for name in resource_names
    ]
    return ResourceRepository(descriptors=descriptors)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_jwt_sub_seeds_pool_for_username_path_param(cli, snapshot_cli, ctx):
    # Without seeding, schemathesis can't generate the bearer's username, so the bug stays hidden.
    user_schema = {
        "type": "object",
        "properties": {"username": {"type": "string"}, "name": {"type": "string"}},
        "required": ["username", "name"],
    }
    paths = {
        "/users": {
            "post": {
                "operationId": "createUser",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"username": {"type": "string"}, "name": {"type": "string"}},
                                "required": ["username", "name"],
                            }
                        }
                    },
                },
                "responses": {
                    "201": {"description": "Created", "content": {"application/json": {"schema": user_schema}}}
                },
            }
        },
        "/users/{username}": {
            "get": {
                "operationId": "getUser",
                "parameters": [
                    {"name": "username", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {
                    "200": {"description": "OK", "content": {"application/json": {"schema": user_schema}}},
                    "404": {"description": "Not found"},
                },
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/users", methods=["POST"])
    def create_user():
        body = request.get_json() or {}
        return jsonify({"username": body.get("username", ""), "name": body.get("name", "")}), 201

    @app.route("/users/<username>", methods=["GET"])
    def get_user(username):
        if username != "alice":
            return "", 404
        # Bug: required `name` is null for the bearer's user.
        return jsonify({"username": "alice", "name": None}), 200

    token = _make_jwt({"sub": "alice"})

    assert (
        cli.run_openapi_app(
            app,
            f"--header=Authorization: Bearer {token}",
            "--phases=fuzzing",
            "--max-examples=50",
            "-c response_schema_conformance",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_basic_auth_username_seeds_pool_for_username_path_param(cli, snapshot_cli, ctx):
    # Basic-auth username is the same identifier the API trusts; without seeding the runner
    # never generates "alice", so the bug at GET /users/alice stays hidden.
    user_schema = {
        "type": "object",
        "properties": {"username": {"type": "string"}, "name": {"type": "string"}},
        "required": ["username", "name"],
    }
    paths = {
        "/users": {
            "post": {
                "operationId": "createUser",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"username": {"type": "string"}, "name": {"type": "string"}},
                                "required": ["username", "name"],
                            }
                        }
                    },
                },
                "responses": {
                    "201": {"description": "Created", "content": {"application/json": {"schema": user_schema}}}
                },
            }
        },
        "/users/{username}": {
            "get": {
                "operationId": "getUser",
                "parameters": [
                    {"name": "username", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {
                    "200": {"description": "OK", "content": {"application/json": {"schema": user_schema}}},
                    "404": {"description": "Not found"},
                },
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/users", methods=["POST"])
    def create_user():
        body = request.get_json() or {}
        return jsonify({"username": body.get("username", ""), "name": body.get("name", "")}), 201

    @app.route("/users/<username>", methods=["GET"])
    def get_user(username):
        if username != "alice":
            return "", 404
        # Bug: required `name` is null for the basic-auth user.
        return jsonify({"username": "alice", "name": None}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--auth=alice:wonderland",
            "--phases=fuzzing",
            "--max-examples=50",
            "-c response_schema_conformance",
            "--mode=positive",
        )
        == snapshot_cli
    )


def test_seed_only_lands_in_buckets_with_matching_queried_field():
    # Cross-resource contamination guard: `sub` must not seed an `Order` whose only queried field is `order_id`.
    repo = _new_repository(["User", "Order"])
    headers = {"Authorization": f"Bearer {_make_jwt({'sub': 'alice'})}"}
    requirements = [
        ParameterRequirement(resource_name="User", resource_field="username"),
        ParameterRequirement(resource_name="Order", resource_field="order_id"),
    ]

    seed_pool_from_headers(repo, headers, requirements)

    assert [i.data for i in repo.iter_instances("User")] == [{"username": "alice"}]
    assert [i.data for i in repo.iter_instances("Order")] == []


@pytest.mark.parametrize(
    "token",
    [
        "opaque-token-no-dots",
        "two.parts",
        "header.!!!not-base64!!!.sig",
        "header." + base64.urlsafe_b64encode(b"xx").rstrip(b"=").decode() + ".sig",
        "header." + base64.urlsafe_b64encode(b'"a string not a dict"').rstrip(b"=").decode() + ".sig",
    ],
    ids=["opaque", "two-parts", "non-base64-payload", "non-json-payload", "non-dict-payload"],
)
def test_seed_silently_skips_non_jwt_bearer(token):
    repo = _new_repository(["User"])

    seed_pool_from_headers(
        repo,
        {"Authorization": f"Bearer {token}"},
        [ParameterRequirement(resource_name="User", resource_field="username")],
    )

    assert [i.data for i in repo.iter_instances("User")] == []


def test_seed_skips_jwt_without_recognized_claims():
    # JWT with only registered claims (`iat`, `exp`) but no identifier claims -> no seeding.
    repo = _new_repository(["User"])
    token = _make_jwt({"iat": 1700000000, "exp": 1800000000, "iss": "https://issuer.example"})

    seed_pool_from_headers(
        repo,
        {"Authorization": f"Bearer {token}"},
        [ParameterRequirement(resource_name="User", resource_field="username")],
    )

    assert [i.data for i in repo.iter_instances("User")] == []
