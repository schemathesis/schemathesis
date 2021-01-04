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
