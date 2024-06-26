import sys

import pytest
from _pytest.main import ExitCode

import schemathesis
from schemathesis.cli import reset_checks


@pytest.fixture
def new_check():
    @schemathesis.check
    def check_function(response, case):
        pass

    yield check_function

    reset_checks()


def test_register_returns_a_value(new_check):
    # When a function is registered via the `schemathesis.check` decorator
    # Then this function should be available for further usage
    # See #721
    assert new_check is not None


@pytest.mark.parametrize(
    "exclude_checks,expected_exit_code,expected_result",
    [
        ("not_a_server_error", ExitCode.TESTS_FAILED, "1 passed, 1 failed in"),
        ("not_a_server_error,status_code_conformance", ExitCode.OK, "2 passed in"),
    ],
)
def test_exclude_checks(
    testdir,
    cli,
    exclude_checks,
    expected_exit_code,
    expected_result,
):
    module = testdir.make_importable_pyfile(
        location="""
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
        f"{module.purebasename}:app",
        "--force-schema-version=30",
    )

    for check in exclude_checks.split(","):
        assert check not in result.stdout

    assert result.exit_code == expected_exit_code, result.stdout

    assert expected_result in result.stdout


def test_negative_data_rejection(testdir, cli, empty_open_api_3_schema, openapi3_base_url):
    empty_open_api_3_schema["paths"] = {
        "/success": {
            "get": {
                "parameters": [{"name": "key", "in": "query", "required": True, "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    result = cli.run(
        str(schema_file),
        f"--base-url={openapi3_base_url}",
        "--checks",
        "negative_data_rejection",
        "-D",
        "negative",
        "--hypothesis-max-examples=5",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED
    assert "Negative data was not rejected as expected by the API" in result.stdout


@pytest.mark.skipif(sys.version_info < (3, 9), reason="typing.Annotated is not available in Python 3.8")
@pytest.mark.snapshot(replace_statistic=True)
def test_deduplication_on_sanitized_header(testdir, cli, snapshot_cli):
    # See GH-2294
    module = testdir.make_importable_pyfile(
        location="""
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
            f"{module.purebasename}:app",
            "--force-schema-version=30",
        )
        == snapshot_cli
    )
