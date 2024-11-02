import pytest
from _pytest.main import ExitCode


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_before_call(ctx, cli, schema_url):
    # When the `before_call` hook is registered
    module = ctx.write_pymodule(
        """
note = print  # To avoid linting error

@schemathesis.hook
def before_call(context, case, **kwargs):
    note("\\nBefore!")
    case.query = {"q": "42"}
        """
    )
    result = cli.main("run", schema_url, hooks=module)
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then it should be called before each `case.call`
    assert "Before!" in result.stdout.splitlines()


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_after_call(ctx, cli, schema_url, snapshot_cli):
    # When the `after_call` hook is registered
    # And it modifies the response and making it incorrect
    module = ctx.write_pymodule(
        """
import requests

@schemathesis.hook
def after_call(context, case, response):
    data = b'{"wrong": 42}'
    if isinstance(response, requests.Response):
        response._content = data
    else:
        response.set_data(data)
        """
    )
    # Then the tests should fail
    assert cli.main("run", schema_url, "-c", "all", hooks=module) == snapshot_cli
