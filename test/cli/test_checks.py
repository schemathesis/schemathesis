import sys

import pytest
from _pytest.main import ExitCode

import schemathesis
from schemathesis.cli import reset_checks


@pytest.fixture
def new_check():
    @schemathesis.check
    def check_function(ctx, response, case):
        pass

    yield check_function

    reset_checks()


def test_register_returns_a_value(new_check):
    # When a function is registered via the `schemathesis.check` decorator
    # Then this function should be available for further usage
    # See #721
    assert new_check is not None


@pytest.mark.parametrize(
    ("exclude_checks", "expected_exit_code", "expected_result"),
    [
        ("not_a_server_error", ExitCode.TESTS_FAILED, "1 passed, 1 failed in"),
        ("not_a_server_error,status_code_conformance", ExitCode.OK, "2 passed in"),
    ],
)
def test_exclude_checks(ctx, cli, exclude_checks, expected_exit_code, expected_result):
    module = ctx.write_pymodule(
        """
from fastapi import FastAPI
from fastapi import HTTPException

app = FastAPI()

@app.get("/api/success")
async def success():
    return {"success": True}

@app.get("/api/failure")
async def failure():
    raise HTTPException(status_code=500)
"""
    )
    result = cli.run(
        "/openapi.json",
        "--checks",
        "all",
        "--exclude-checks",
        exclude_checks,
        "--app",
        f"{module}:app",
        "--force-schema-version=30",
    )

    for check in exclude_checks.split(","):
        assert check not in result.stdout

    assert result.exit_code == expected_exit_code, result.stdout

    assert expected_result in result.stdout


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
        "-D",
        "negative",
        "--hypothesis-max-examples=5",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED


@pytest.mark.skipif(sys.version_info < (3, 9), reason="typing.Annotated is not available in Python 3.8")
@pytest.mark.snapshot(replace_statistic=True)
def test_deduplication_on_sanitized_header(ctx, cli, snapshot_cli):
    # See GH-2294
    module = ctx.write_pymodule(
        """
from typing import Annotated

from fastapi import FastAPI, HTTPException, Header

app = FastAPI()

@app.get("/users")
def get_users(x_token: Annotated[str, Header()]):
    if x_token:
        raise HTTPException(status_code=500, detail="Internal server error")
    raise HTTPException(status_code=400, detail="Bad header")
        """
    )
    assert (
        cli.run(
            "/openapi.json",
            "--checks",
            "all",
            "--app",
            f"{module}:app",
            "--force-schema-version=30",
        )
        == snapshot_cli
    )


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
            "--hypothesis-max-examples=5",
            "--experimental=positive_data_acceptance",
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
            "--hypothesis-max-examples=5",
        )
        == snapshot_cli
    )
