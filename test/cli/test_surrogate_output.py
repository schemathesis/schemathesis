import json

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
