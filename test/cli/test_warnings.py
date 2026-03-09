import pytest
from _pytest.main import ExitCode
from flask import Response


def _serve_schema(ctx, app_runner, schema: dict, routes):
    app = ctx.openapi.make_flask_app_from_schema(schema)

    for method, path, handler in routes:
        app.add_url_rule(path, f"{method}_{path}", handler, methods=[method])

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/openapi.json"


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("basic")
def test_missing_auth_warning_with_fail_on_true(cli, schema_url, tmp_path, monkeypatch):
    # Given a config file that fails on all warnings
    config_file = tmp_path / "schemathesis.toml"
    config_file.write_text("""
[warnings]
fail-on = true
""")
    monkeypatch.chdir(tmp_path)

    # When running tests without proper auth (will trigger missing_auth warning)
    result = cli.run_and_assert(schema_url, exit_code=ExitCode.TESTS_FAILED)
    # And warnings should be displayed
    assert "WARNINGS" in result.stdout
    assert "Authentication failed" in result.stdout


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("basic")
def test_missing_auth_warning_with_fail_on_specific(cli, schema_url, tmp_path, monkeypatch):
    # Given a config file that fails only on missing_auth warnings
    config_file = tmp_path / "schemathesis.toml"
    config_file.write_text("""
[warnings]
fail-on = ["missing_auth"]
""")
    monkeypatch.chdir(tmp_path)

    # When running tests without proper auth (will trigger missing_auth warning)
    result = cli.run_and_assert(schema_url, exit_code=ExitCode.TESTS_FAILED)
    # And warnings should be displayed
    assert "WARNINGS" in result.stdout
    assert "Authentication failed" in result.stdout


def test_missing_deserializer_warning_displayed(cli, ctx, openapi3_base_url):
    # Given a schema with a custom media type that has no deserializer
    schema_path = ctx.openapi.write_schema(
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/msgpack": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                        }
                    }
                }
            }
        }
    )

    result = cli.run(str(schema_path), f"--url={openapi3_base_url}", "--max-examples=1")

    # Then the warning should be displayed in both summary and detailed sections
    assert "⚠️ Schema validation skipped: 1 operation cannot validate responses" in result.stdout
    assert "WARNINGS" in result.stdout
    assert (
        "Schema validation skipped: 1 operation cannot validate responses due to missing deserializers" in result.stdout
    )
    assert "GET /users" in result.stdout
    assert "Cannot validate response 200: no deserializer registered for application/msgpack" in result.stdout
    assert "💡 Register a deserializer with @schemathesis.deserializer() to enable validation" in result.stdout


def test_missing_deserializer_warning_with_fail_on(cli, ctx, openapi3_base_url, tmp_path, monkeypatch):
    # Given a schema with a custom media type and config that fails on missing deserializer
    schema_path = ctx.openapi.write_schema(
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/msgpack": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                        }
                    }
                }
            }
        }
    )

    config_file = tmp_path / "schemathesis.toml"
    config_file.write_text("""
[warnings]
fail-on = ["missing_deserializer"]
""")
    monkeypatch.chdir(tmp_path)

    result = cli.run_and_assert(
        str(schema_path), f"--url={openapi3_base_url}", "--max-examples=1", exit_code=ExitCode.TESTS_FAILED
    )

    # Then the warning should be displayed and test should fail
    assert "WARNINGS" in result.stdout
    assert "Schema validation skipped" in result.stdout


def test_warnings_off_via_cli(cli, ctx, openapi3_base_url):
    # When --warnings=off is used, warnings should not be displayed
    schema_path = ctx.openapi.write_schema(
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/msgpack": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                        }
                    }
                }
            }
        }
    )

    result = cli.run(str(schema_path), f"--url={openapi3_base_url}", "--warnings=off", "--max-examples=1")

    # Then no warnings should be displayed
    assert "WARNINGS" not in result.stdout
    assert "Schema validation skipped" not in result.stdout


def test_warnings_specific_type_via_cli(cli, ctx, openapi3_base_url):
    # When --warnings=missing_deserializer is used, only that warning type is shown
    schema_path = ctx.openapi.write_schema(
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/msgpack": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                        }
                    }
                }
            }
        }
    )

    result = cli.run(
        str(schema_path), f"--url={openapi3_base_url}", "--warnings=missing_deserializer", "--max-examples=1"
    )

    # Then the specified warning should be displayed
    assert "WARNINGS" in result.stdout
    assert "Schema validation skipped" in result.stdout


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("basic")
def test_warnings_multiple_types_via_cli(cli, schema_url):
    # When --warnings with comma-separated values is used
    result = cli.run(schema_url, "--warnings=missing_auth,missing_test_data", "--max-examples=1")

    # Then warnings can still be triggered for specified types
    # (This just validates the flag is parsed correctly - actual warnings depend on test conditions)
    assert result.exit_code in (ExitCode.OK, ExitCode.TESTS_FAILED)


@pytest.mark.openapi_version("3.0")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_final_line_counts_all_warning_kinds_in_run(cli, app_runner, ctx, snapshot_cli):
    # Regression test: the footer "N warnings in Xs" should count warning *kinds*, not just missing_auth operations
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

    schema_url = _serve_schema(ctx, app_runner, schema, [("GET", "/auth", auth), ("GET", "/missing", missing)])
    assert cli.run(schema_url, "--checks=not_a_server_error", "--max-examples=1") == snapshot_cli
