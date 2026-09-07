import json
import os
import subprocess
import sys

import pytest
from flask import Response

import schemathesis
from schemathesis.cli.output import DEFAULT_INTERNAL_ERROR_MESSAGE


def test_lone_surrogate_in_check_message(cli, ctx):
    # `response.json()` decodes the server's ASCII `\uXXXX` escape into a real lone surrogate;
    # a check surfacing it must not crash the failure renderer when written to a UTF-8 terminal.
    app, _ = ctx.openapi.make_flask_app({"/echo": {"post": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/echo", methods=["POST"])
    def echo():
        return Response(json.dumps({"error": "rejected: \udc4b"}), status=400, content_type="application/json")

    with ctx.restore_checks():

        @schemathesis.check
        def surface_rejected_value(ctx, response, case):
            if response.status_code >= 400:
                raise AssertionError(f"server said: {response.json()['error']}")

        result = cli.run_openapi_app(app, "--checks=surface_rejected_value", "--max-examples=5")

    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    assert DEFAULT_INTERNAL_ERROR_MESSAGE not in result.stdout, result.stdout
    assert "server said: rejected:" in result.stdout, result.stdout


def test_lone_surrogate_in_engine_error(cli, ctx):
    # A check raising a non-`Failure` exception surfaces as an engine error; its message carries
    # the lone surrogate into the ERRORS renderer, which must not crash on a UTF-8 terminal.
    app, _ = ctx.openapi.make_flask_app({"/echo": {"post": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/echo", methods=["POST"])
    def echo():
        return Response(json.dumps({"error": "rejected: \udc4b"}), status=400, content_type="application/json")

    with ctx.restore_checks():

        @schemathesis.check
        def explode_on_value(ctx, response, case):
            if response.status_code >= 400:
                raise RuntimeError(f"boom: {response.json()['error']}")

        result = cli.run_openapi_app(app, "--checks=explode_on_value", "--max-examples=5")

    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    assert DEFAULT_INTERNAL_ERROR_MESSAGE not in result.stdout, result.stdout
    assert "boom:" in result.stdout, result.stdout


@pytest.mark.parametrize("value", ["ok", "쑅"], ids=["ascii", "non-ascii"])
def test_output_on_cp1252_stdout(ctx, app_runner, value):
    # A stdout that cannot represent the output is normal on Windows and when piping to a file.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/value": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"enum": [value]}}},
                    },
                    "responses": {"500": {"description": "Error"}},
                }
            }
        }
    )

    @app.route("/value", methods=["POST"])
    def get_value():
        return Response(json.dumps({"detail": value}, ensure_ascii=False), status=500, content_type="application/json")

    result = subprocess.run(
        [sys.executable, "-m", "schemathesis.cli", "run", app_runner.openapi_url(app), "--max-examples=1"],
        capture_output=True,
        text=True,
        encoding="cp1252",
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
    )

    output = result.stdout + result.stderr
    assert "UnicodeEncodeError" not in output, output
    assert DEFAULT_INTERNAL_ERROR_MESSAGE not in output, output
    assert result.returncode == 1, output
