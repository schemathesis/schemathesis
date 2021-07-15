from test.apps.openapi.schema import OpenAPIVersion

import pytest
from _pytest.main import ExitCode

import schemathesis


@pytest.fixture(autouse=True)
def unregister_hooks():
    yield
    schemathesis.hooks.unregister_all()


@pytest.mark.operations("success")
def test_custom_cli_handlers(testdir, cli, schema_url, app):
    # When `after_init_cli_run_handlers` redefines handlers
    module = testdir.make_importable_pyfile(
        hook="""
    import click
    import schemathesis
    from schemathesis.cli.handlers import EventHandler
    from schemathesis.runner import events

    class SimpleHandler(EventHandler):

        def handle_event(self, context, event):
            if isinstance(event, events.Finished):
                click.echo("Done!")

    @schemathesis.hooks.register
    def after_init_cli_run_handlers(
        context,
        handlers,
        execution_context
    ):
        handlers[:] = [SimpleHandler()]
    """
    )

    result = cli.main("--pre-run", module.purebasename, "run", schema_url)

    # Then CLI should run successfully
    assert result.exit_code == ExitCode.OK, result.stdout
    # And the output should contain only the input from the new handler
    assert result.stdout.strip() == "Done!"


@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
@pytest.mark.operations("success")
def test_before_call(testdir, cli, cli_args):
    # When the `before_call` hook is registered
    module = testdir.make_importable_pyfile(
        hook="""
import schemathesis

note = print  # To avoid linting error

@schemathesis.hooks.register
def before_call(context, case):
    note("\\nBefore!")
    case.query = {"q": "42"}
        """
    )
    result = cli.main("--pre-run", module.purebasename, "run", *cli_args)
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then it should be called before each `case.call`
    assert "Before!" in result.stdout.splitlines()


@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
@pytest.mark.operations("success")
def test_after_call(testdir, cli, cli_args):
    # When the `after_call` hook is registered
    # And it modifies the response and making it incorrect
    module = testdir.make_importable_pyfile(
        hook="""
import schemathesis
import requests

@schemathesis.hooks.register
def after_call(context, case, response):
    data = b'{"wrong": 42}'
    if isinstance(response, requests.Response):
        response._content = data
    else:
        response.set_data(data)
        """
    )
    result = cli.main("--pre-run", module.purebasename, "run", *cli_args, "-c", "all")
    # Then the tests should fail
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert 'Response payload: `{"wrong": 42}`' in result.stdout.splitlines()
