from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time
import warnings
from collections.abc import Callable

import pytest
from flask import Flask, Response, jsonify
from urllib3.exceptions import InsecureRequestWarning


def _serve_schema(app_runner, schema: dict, routes: list[tuple[str, str, Callable]]) -> str:
    app = Flask(__name__)

    @app.route("/openapi.json")
    def get_schema():  # type: ignore[no-untyped-def]
        return jsonify(schema)

    for method, path, handler in routes:
        app.add_url_rule(path, f"{method}_{path}", handler, methods=[method])

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


def _unsupported_regex_schema(ctx) -> dict:  # type: ignore[no-untyped-def]
    return ctx.openapi.build_schema(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string", "pattern": "(?R)"},
                        }
                    ],
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
                    },
                }
            }
        }
    )


@pytest.mark.openapi_version("3.0")
def test_fuzz_schema_warning_visible_by_default(cli, app_runner, ctx):
    schema = _unsupported_regex_schema(ctx)

    def items():  # type: ignore[no-untyped-def]
        return jsonify({})

    schema_url = _serve_schema(app_runner, schema, [("GET", "/items", items)])
    # --max-time=0.05 gives a deterministic stop reason (time limit) because the string
    # parameter without a pattern constraint generates inputs indefinitely.
    result = cli.main("fuzz", schema_url, "--workers=1", "--max-time=0.05")

    assert result.exit_code == 1, result.output
    assert "Unsupported regex" in result.output


@pytest.mark.openapi_version("3.0")
def test_fuzz_schema_warning_hidden_with_warnings_off(cli, app_runner, ctx):
    schema = _unsupported_regex_schema(ctx)

    def items():  # type: ignore[no-untyped-def]
        return jsonify({})

    schema_url = _serve_schema(app_runner, schema, [("GET", "/items", items)])
    result = cli.main("fuzz", schema_url, "--workers=1", "--max-time=0.05", "--warnings=off")

    assert result.exit_code == 1, result.output
    assert "Unsupported regex" not in result.output


@pytest.mark.openapi_version("3.0")
def test_fuzz_final_line_counts_all_warning_kinds(cli, app_runner, ctx, snapshot_cli):
    schema = ctx.openapi.build_schema(
        {
            "/auth": {
                "get": {
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
                    }
                }
            },
            "/missing": {
                "get": {
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
                    }
                }
            },
        }
    )

    def auth():  # type: ignore[no-untyped-def]
        return Response(status=401)

    def missing():  # type: ignore[no-untyped-def]
        return Response(status=404)

    schema_url = _serve_schema(app_runner, schema, [("GET", "/auth", auth), ("GET", "/missing", missing)])
    assert cli.main("fuzz", schema_url, "--workers=1", "-c", "not_a_server_error") == snapshot_cli


@pytest.mark.openapi_version("3.0")
def test_fuzz_fail_fast_stop_reason(cli, app_runner, ctx, snapshot_cli):
    schema = ctx.openapi.build_schema(
        {
            "/boom": {
                "get": {
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
                    }
                }
            }
        }
    )

    def boom():  # type: ignore[no-untyped-def]
        return Response(status=500)

    schema_url = _serve_schema(app_runner, schema, [("GET", "/boom", boom)])
    assert cli.main("fuzz", schema_url, "--workers=1") == snapshot_cli


@pytest.mark.openapi_version("3.0")
def test_fuzz_max_failures_stop_reason(cli, app_runner, ctx, snapshot_cli):
    schema = ctx.openapi.build_schema(
        {
            "/boom": {
                "get": {
                    "parameters": [
                        {"name": "id", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 0}}
                    ],
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
                    },
                }
            }
        }
    )

    def boom():  # type: ignore[no-untyped-def]
        return Response(status=500)

    schema_url = _serve_schema(app_runner, schema, [("GET", "/boom", boom)])
    # Integer parameter prevents input exhaustion so the engine keeps generating
    # new scenarios. --continue-on-failure prevents stopping on the first failure;
    # --max-failures=2 verifies it stops after exactly 2 failure events.
    assert cli.main("fuzz", schema_url, "--workers=1", "--continue-on-failure", "--max-failures=2") == snapshot_cli


@pytest.mark.openapi_version("3.0")
def test_fuzz_non_fatal_invalid_operations_are_reported_and_valid_ops_continue(
    cli, app_runner, ctx, snapshot_cli
):
    schema = ctx.openapi.build_schema(
        {
            "/ok": {
                "get": {
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
                    }
                }
            },
            "/csv": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"text/csv": {"schema": {"type": "string"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    ok_calls = 0

    def ok():  # type: ignore[no-untyped-def]
        nonlocal ok_calls
        ok_calls += 1
        return jsonify({})

    def csv():  # type: ignore[no-untyped-def]
        return Response(status=200)

    schema_url = _serve_schema(app_runner, schema, [("GET", "/ok", ok), ("POST", "/csv", csv)])
    # GET /ok has no parameters so input exhausts after one case; POST /csv produces a
    # non-fatal serialization error and is skipped. --continue-on-failure lets the engine
    # process both operations instead of stopping on the serialization error.
    result = cli.main("fuzz", schema_url, "--workers=1", "--continue-on-failure")

    # Behavioral: the valid operation was actually exercised despite the sibling error.
    assert ok_calls > 0
    assert result == snapshot_cli


@pytest.mark.openapi_version("3.0")
def test_fuzz_time_limit_stop_reason(cli, app_runner, ctx):
    schema = ctx.openapi.build_schema(
        {
            "/ok": {
                "get": {
                    "parameters": [
                        {"name": "id", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 0}}
                    ],
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
                    },
                }
            }
        }
    )

    def ok():  # type: ignore[no-untyped-def]
        return jsonify({})

    schema_url = _serve_schema(app_runner, schema, [("GET", "/ok", ok)])
    # An integer parameter prevents input exhaustion; the time limit is the only stop condition.
    result = cli.main("fuzz", schema_url, "--workers=1", "--max-time=0.05")

    assert result.exit_code == 1, result.output
    assert "Stopped: time limit reached" in result.output


@pytest.mark.openapi_version("3.0")
def test_fuzz_input_exhaustion_stop_reason(cli, app_runner, ctx, snapshot_cli):
    schema = ctx.openapi.build_schema(
        {
            "/ok": {
                "get": {
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
                    }
                }
            }
        }
    )

    def ok():  # type: ignore[no-untyped-def]
        return jsonify({})

    schema_url = _serve_schema(app_runner, schema, [("GET", "/ok", ok)])
    assert cli.main("fuzz", schema_url, "--workers=1") == snapshot_cli



@pytest.mark.openapi_version("3.0")
def test_fuzz_restores_signal_handlers_on_no_valid_operations(cli, app_runner, ctx):
    schema = ctx.openapi.build_schema(
        {
            "/csv": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"text/csv": {"schema": {"type": "string"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    def csv():  # type: ignore[no-untyped-def]
        return Response(status=200)

    schema_url = _serve_schema(app_runner, schema, [("POST", "/csv", csv)])

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    result = cli.main("fuzz", schema_url, "--workers=1")

    assert result.exit_code == 1, result.output
    assert signal.getsignal(signal.SIGINT) is previous_sigint
    assert signal.getsignal(signal.SIGTERM) is previous_sigterm


def test_fuzz_tls_warning_filter_is_scoped_to_execution(cli, schema_url):
    def count_tls_ignores() -> int:
        total = 0
        for action, _, category, _, _ in warnings.filters:
            if action == "ignore" and issubclass(category, InsecureRequestWarning):
                total += 1
        return total

    before = count_tls_ignores()
    result = cli.main("fuzz", schema_url, "--include-method=DELETE", "--workers=1")
    after = count_tls_ignores()

    assert result.exit_code == 0, result.output
    assert after == before


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_fuzz_custom_handler_can_set_exit_code_to_failure(ctx, cli, schema_url):
    module = ctx.write_pymodule(
        r"""
from schemathesis import cli, engine

@cli.handler()
class ExitCodeHandler(cli.EventHandler):
    def handle_event(self, ctx, event) -> None:
        if isinstance(event, engine.events.EngineFinished):
            ctx.exit_code = 1
"""
    )

    result = cli.main(
        "fuzz",
        schema_url,
        "--workers=1",
        "--include-method=DELETE",
        hooks=module,
    )
    assert result.exit_code == 1, result.output
    # Confirm the run completed normally so the handler had a chance to fire.
    assert "SUMMARY" in result.output


@pytest.mark.openapi_version("3.0")
def test_fuzz_ctrl_c_returns_130_and_reports_interrupted(app_runner, ctx):
    schema = ctx.openapi.build_schema(
        {
            "/ok": {
                "get": {
                    "parameters": [
                        {"name": "id", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 0}}
                    ],
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
                    },
                }
            }
        }
    )

    def ok():  # type: ignore[no-untyped-def]
        return jsonify({})

    schema_url = _serve_schema(app_runner, schema, [("GET", "/ok", ok)])
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    process = subprocess.Popen(
        # --mode=positive ensures the engine actually enters the fuzzing loop with
        # generated requests rather than exhausting immediately, giving SIGINT time to arrive.
        [sys.executable, "-m", "schemathesis.cli", "fuzz", schema_url, "--workers=1", "--mode=positive"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    try:
        # Signal handlers are installed before any output is produced, so we can
        # interrupt reliably as soon as any output appears (the banner line).
        assert process.stdout is not None
        accumulated = ""
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            ready, _, _ = select.select([process.stdout], [], [], 0.05)
            if ready:
                chunk = process.stdout.read(256)
                if chunk:
                    accumulated += chunk
                    break  # any output means signal handlers are active
            if process.poll() is not None:
                break
        process.send_signal(signal.SIGINT)
        remaining, _ = process.communicate(timeout=10)
        output = accumulated + remaining
    finally:
        if process.poll() is None:
            process.kill()

    assert process.returncode == 130, output
    assert "Stopped: interrupted" in output
