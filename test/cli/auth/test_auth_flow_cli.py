import pytest
from flask import jsonify

from test.engine.auth._helpers import (
    auth_flow_paths,
    auth_flow_security_schemes,
    build_auth_flask_app,
)


def _build_auth_flow_failure_app(make_flask_app, *, handler_kind: str):
    app, _ = make_flask_app(
        auth_flow_paths(include_protected=False),
        components={"securitySchemes": auth_flow_security_schemes()},
    )

    @app.post("/register")
    def register():
        return jsonify({"ok": True})

    if handler_kind == "login_401":

        @app.post("/login")
        def login():
            return jsonify({"error": "always rejects"}), 401
    elif handler_kind == "extract_no_token":

        @app.post("/login")
        def login():
            return jsonify({"unrelated": "no-token-here"})
    else:
        raise ValueError(f"unknown handler_kind: {handler_kind}")

    return app


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_auth_flow_bootstrap_section(ctx, app_runner, cli, snapshot_cli):
    app, _, _ = build_auth_flask_app(ctx.openapi.make_flask_app)
    port = app_runner.run_flask_app(app)
    schema_url = f"http://127.0.0.1:{port}/openapi.json"
    assert cli.run(schema_url, "--max-examples=10", "--seed=42") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_auth_flow_register_failure(ctx, app_runner, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        auth_flow_paths(
            register_responses={"200": {"description": "OK"}, "400": {"description": "Bad"}},
            include_protected=False,
        ),
        components={"securitySchemes": auth_flow_security_schemes()},
    )

    @app.post("/register")
    def register():
        return jsonify({"error": "always bad"}), 400

    port = app_runner.run_flask_app(app)
    schema_url = f"http://127.0.0.1:{port}/openapi.json"
    assert cli.run(schema_url, "--max-examples=5", "--seed=42") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_auth_flow_skipped_with_static_auth(ctx, app_runner, cli, snapshot_cli):
    app, _, _ = build_auth_flask_app(ctx.openapi.make_flask_app)
    port = app_runner.run_flask_app(app)
    schema_url = f"http://127.0.0.1:{port}/openapi.json"
    assert (
        cli.run(
            schema_url,
            "--max-examples=5",
            "--seed=42",
            config={"auth": {"openapi": {"BearerAuth": {"bearer": "the-valid-token"}}}},
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize(
    "handler_kind",
    ["login_401", "extract_no_token"],
    ids=["login", "extract"],
)
def test_auth_flow_failure(handler_kind, ctx, app_runner, cli, snapshot_cli):
    app = _build_auth_flow_failure_app(ctx.openapi.make_flask_app, handler_kind=handler_kind)
    port = app_runner.run_flask_app(app)
    schema_url = f"http://127.0.0.1:{port}/openapi.json"
    assert cli.run(schema_url, "--max-examples=5", "--seed=42") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_auth_flow_mint_failure(ctx, app_runner, cli, snapshot_cli):
    # Contradictory minLength/maxLength on password forces a mint failure.
    app, _ = ctx.openapi.make_flask_app(
        auth_flow_paths(
            password_schema={"type": "string", "minLength": 1000, "maxLength": 0},
            include_protected=False,
        ),
        components={"securitySchemes": auth_flow_security_schemes()},
    )

    port = app_runner.run_flask_app(app)
    schema_url = f"http://127.0.0.1:{port}/openapi.json"
    assert cli.run(schema_url, "--max-examples=5", "--seed=42") == snapshot_cli
