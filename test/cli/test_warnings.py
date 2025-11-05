import pytest
from _pytest.main import ExitCode


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
