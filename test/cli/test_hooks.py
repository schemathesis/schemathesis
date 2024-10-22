import pytest
import requests
import werkzeug
from _pytest.main import ExitCode


@pytest.mark.operations("success")
def test_custom_cli_handlers(ctx, cli, schema_url):
    # When `after_init_cli_run_handlers` redefines handlers
    module = ctx.write_pymodule(
        """
import click
from schemathesis.cli.handlers import EventHandler
from schemathesis.runner import events

class SimpleHandler(EventHandler):

    def handle_event(self, context, event):
        if isinstance(event, events.Finished):
            click.echo("Done!")

@schemathesis.hook
def after_init_cli_run_handlers(
    context,
    handlers,
    execution_context
):
    handlers[:] = [SimpleHandler()]
"""
    )

    result = cli.main("run", schema_url, hooks=module)

    # Then CLI should run successfully
    assert result.exit_code == ExitCode.OK, result.stdout
    # And the output should contain only the input from the new handler
    assert result.stdout.strip() == "Done!"


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_before_call(ctx, cli, cli_args):
    # When the `before_call` hook is registered
    module = ctx.write_pymodule(
        """
note = print  # To avoid linting error

@schemathesis.hook
def before_call(context, case):
    note("\\nBefore!")
    case.query = {"q": "42"}
        """
    )
    result = cli.main("run", *cli_args, hooks=module)
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then it should be called before each `case.call`
    assert "Before!" in result.stdout.splitlines()


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_after_call(ctx, cli, cli_args, snapshot_cli):
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
    assert cli.main("run", *cli_args, "-c", "all", hooks=module) == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_process_call_kwargs(ctx, cli, cli_args, mocker, app_type):
    # When the `process_call_kwargs` hook is registered
    # And it modifies `kwargs` by adding a new key there
    module = ctx.write_pymodule(
        """
import requests

@schemathesis.hook
def process_call_kwargs(context, case, kwargs):
    if case.app is not None:
        kwargs["follow_redirects"] = False
    else:
        kwargs["allow_redirects"] = False
        """
    )
    if app_type == "real":
        spy = mocker.spy(requests.Session, "request")
    else:
        spy = mocker.spy(werkzeug.Client, "open")
    result = cli.main("run", *cli_args, hooks=module)
    assert result.exit_code == ExitCode.OK, result.stdout
    if app_type == "real":
        assert spy.call_args[1]["allow_redirects"] is False
    else:
        assert spy.call_args[1]["follow_redirects"] is False
