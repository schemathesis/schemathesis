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
            return {"failure": True}
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
