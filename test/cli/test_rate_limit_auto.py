from __future__ import annotations

import pytest
from flask import jsonify, make_response

from schemathesis.core.rate_limit import RATE_LIMIT_AUTO_MAX_RETRIES


def _users_app(ctx, handler):
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    app.add_url_rule("/users", "users", handler, methods=["GET"])
    return app


def _rate_limited(retry_after="0"):
    resp = make_response("", 429)
    resp.headers["Retry-After"] = retry_after
    return resp


_FUZZING_ARGS = ("--rate-limit=auto", "--phases=fuzzing", "--max-examples=1", "--checks=not_a_server_error")


# A persistent 429 is retried until the retry budget runs out; without `Retry-After` it is the final response.
@pytest.mark.parametrize(
    ("headers", "expected_calls"),
    [({"Retry-After": "0"}, RATE_LIMIT_AUTO_MAX_RETRIES + 1), ({}, 1)],
    ids=["exhausts-retries-on-persistent-429", "no-retry-without-retry-after"],
)
def test_auto_retries_persistent_429(ctx, cli, headers, expected_calls):
    calls = {"n": 0}

    def users():
        calls["n"] += 1
        return make_response("", 429, headers)

    result = cli.run_openapi_app(_users_app(ctx, users), *_FUZZING_ARGS)
    assert (result.exit_code, calls["n"]) == (0, expected_calls), result.stdout


def test_auto_reports_long_wait(ctx, cli, monkeypatch):
    # Drop the report threshold so a zero-second wait still surfaces, avoiding a real sleep.
    monkeypatch.setattr("schemathesis.engine._rate_limit_retry.RATE_LIMIT_AUTO_REPORT_THRESHOLD", 0.0)
    calls = {"n": 0}

    def users():
        calls["n"] += 1
        if calls["n"] == 1:
            return _rate_limited()
        return jsonify([])

    result = cli.run_openapi_app(_users_app(ctx, users), *_FUZZING_ARGS)
    assert result.exit_code == 0, result.stdout
    # One case: the 429 is retried once and the retry returns 200.
    assert calls["n"] == 2, calls["n"]
    assert "Rate limited" in result.stdout


def _stateful_429_app(ctx, get_handler):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "integer"}},
                                        "required": ["id"],
                                    }
                                }
                            },
                            "links": {
                                "GetUser": {"operationId": "getUser", "parameters": {"userId": "$response.body#/id"}}
                            },
                        }
                    },
                }
            },
            "/users/{userId}": {
                "parameters": [{"in": "path", "name": "userId", "required": True, "schema": {"type": "integer"}}],
                "get": {
                    "operationId": "getUser",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "integer"}},
                                        "required": ["id"],
                                    }
                                }
                            },
                        }
                    },
                },
            },
        }
    )

    @app.route("/users", methods=["POST"])
    def create_user():
        return jsonify({"id": 1}), 201

    app.add_url_rule("/users/<int:user_id>", "get_user", get_handler, methods=["GET"])
    return app


_STATEFUL_ARGS = (
    "--rate-limit=auto",
    "--phases=stateful",
    "--max-examples=15",
    "-c status_code_conformance",
    "-c response_schema_conformance",
)


def test_stateful_retried_and_final_both_fail_checks(ctx, cli):
    calls = {"n": 0}

    def get_user(user_id):
        calls["n"] += 1
        if calls["n"] == 1:
            # Undocumented status -> fails `status_code_conformance`.
            return _rate_limited()
        # Documented status but missing required `id` -> fails `response_schema_conformance`.
        return jsonify({}), 200

    result = cli.run_openapi_app(_stateful_429_app(ctx, get_user), *_STATEFUL_ARGS)
    # The retried 429 and the final response fail different checks; both failures are reported together.
    assert result.exit_code == 1, result.stdout
    assert "Undocumented status code" in result.stdout
    assert "Received: 429" in result.stdout
    assert "Response violates schema" in result.stdout


def test_stateful_retried_failure_surfaces_when_final_passes(ctx, cli):
    calls = {"n": 0}

    def get_user(user_id):
        calls["n"] += 1
        if calls["n"] == 1:
            response = _rate_limited()
            response.set_data("Slow down")
            return response
        return jsonify({"id": user_id, "note": "final response"}), 200

    result = cli.run_openapi_app(_stateful_429_app(ctx, get_user), *_STATEFUL_ARGS)
    # The first 429 fails the check; the retry returns a valid 200, yet the failure is still reported with the 429.
    assert result.exit_code == 1, result.stdout
    assert "Undocumented status code" in result.stdout
    assert "Received: 429" in result.stdout
    assert "[429] Too Many Requests:\n\n    `Slow down`" in result.stdout
    assert "final response" not in result.stdout


@pytest.mark.parametrize(
    "args",
    [("run", "--phases=fuzzing", "--max-examples=1"), ("fuzz", "--max-time=10")],
    ids=["unit", "fuzz"],
)
def test_retried_failure_surfaces_when_final_passes(ctx, cli, app_runner, args):
    calls = {"n": 0}

    def users():
        calls["n"] += 1
        if calls["n"] == 1:
            return _rate_limited()
        return jsonify([])

    command, *rest = args
    result = cli.main(
        command,
        app_runner.openapi_url(_users_app(ctx, users)),
        "--rate-limit=auto",
        "--checks=status_code_conformance",
        *rest,
    )
    assert result.exit_code == 1, result.stdout
    assert "Undocumented status code" in result.stdout
    assert "Received: 429" in result.stdout
