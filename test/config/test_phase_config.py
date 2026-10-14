import pytest
from _pytest.main import ExitCode
from flask import jsonify, request


def test_disable_all_and_enable_one(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success_and_failure()
    assert (
        cli.run(
            api.schema_url,
            "--checks=not_a_server_error",
            config={
                "phases": {"enabled": False, "fuzzing": {"enabled": True}},
            },
        )
        == snapshot_cli
    )


@pytest.mark.parametrize("phase", ["coverage", "fuzzing"])
def test_phase_level_check_disabled(ctx, cli, phase):
    api = ctx.openapi.apps.failure()
    result = cli.run(
        api.schema_url,
        f"--phases={phase}",
        "--max-examples=1",
        config={"phases": {phase: {"checks": {"not_a_server_error": {"enabled": False}}}}},
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    assert [request.path for request in api.requests if request.path.startswith("/api/")]


def test_phase_level_generation_mode(ctx, cli, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "id", "in": "query", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    received = []

    @app.route("/items")
    def items():
        received.append(request.args.get("id"))
        return jsonify({})

    result = cli.run_openapi_app(
        app,
        "--phases=coverage",
        config={"phases": {"coverage": {"generation": {"mode": "positive"}}}},
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    assert received
    assert [value for value in received if value is None or not value.lstrip("-").isdigit()] == []
