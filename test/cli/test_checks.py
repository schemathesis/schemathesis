import pytest
from _pytest.main import ExitCode

import schemathesis
from schemathesis.checks import CHECKS


@pytest.fixture
def new_check():
    @schemathesis.check
    def check_function(ctx, response, case):
        pass

    yield check_function

    CHECKS.unregister(check_function.__name__)


def test_register_returns_a_value(new_check):
    # When a function is registered via the `schemathesis.check` decorator
    # Then this function should be available for further usage
    # See #721
    assert new_check is not None


def test_negative_data_rejection(ctx, cli, openapi3_base_url):
    schema_path = ctx.openapi.write_schema(
        {
            "/success": {
                "get": {
                    "parameters": [{"name": "key", "in": "query", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    result = cli.run(
        str(schema_path),
        f"--base-url={openapi3_base_url}",
        "--checks",
        "negative_data_rejection",
        "--generation-mode",
        "negative",
        "--generation-max-examples=5",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED


@pytest.fixture
def schema(ctx):
    return ctx.openapi.build_schema(
        {
            "/test": {
                "get": {
                    "responses": {
                        "200": {"description": "Successful response"},
                        "400": {"description": "Bad request"},
                    }
                }
            }
        }
    )


@pytest.mark.parametrize(
    "args",
    [
        [],  # Default case
        ["--experimental-positive-data-acceptance-allowed-statuses=404"],
        ["--experimental-positive-data-acceptance-allowed-statuses=405"],
        ["--experimental-positive-data-acceptance-allowed-statuses=2xx,404"],
        ["--experimental-positive-data-acceptance-allowed-statuses=200"],
        ["--experimental-positive-data-acceptance-allowed-statuses=200,404"],
        ["--experimental-positive-data-acceptance-allowed-statuses=2xx"],
        ["--experimental-positive-data-acceptance-allowed-statuses=4xx"],
        # Invalid status code
        ["--experimental-positive-data-acceptance-allowed-statuses=200,600"],
        # Invalid wildcard
        ["--experimental-positive-data-acceptance-allowed-statuses=xxx"],
        ["--experimental-positive-data-acceptance-allowed-statuses=200,201,400,401"],
    ],
)
def test_positive_data_acceptance(ctx, cli, snapshot_cli, schema, openapi3_base_url, args):
    schema_path = ctx.makefile(schema)
    assert (
        cli.run(
            str(schema_path),
            f"--base-url={openapi3_base_url}",
            "--generation-max-examples=5",
            "--experimental=positive-data-acceptance",
            *args,
        )
        == snapshot_cli
    )


def test_positive_data_acceptance_with_env_vars(ctx, cli, snapshot_cli, schema, openapi3_base_url, monkeypatch):
    schema_path = ctx.makefile(schema)
    monkeypatch.setenv("SCHEMATHESIS_EXPERIMENTAL_POSITIVE_DATA_ACCEPTANCE", "true")
    monkeypatch.setenv("SCHEMATHESIS_EXPERIMENTAL_POSITIVE_DATA_ACCEPTANCE_ALLOWED_STATUSES", "403")
    assert (
        cli.run(
            str(schema_path),
            f"--base-url={openapi3_base_url}",
            "--generation-max-examples=5",
        )
        == snapshot_cli
    )
