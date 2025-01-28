import pytest
from _pytest.main import ExitCode

import schemathesis
from schemathesis.generation.targets import TARGETS


@pytest.fixture
def new_target(ctx, cli):
    module = ctx.write_pymodule(
        """
import click

@schemathesis.target
def new_target(context) -> float:
    click.echo("NEW TARGET IS CALLED")
    assert context.case.meta.generation.mode is not None, "Empty generation mode"
    return float(len(context.response.content))
"""
    )
    yield module
    TARGETS.unregister("new_target")
    # To verify that "new_target" is unregistered
    assert "new_target" not in cli.run("--help").stdout


@pytest.mark.usefixtures("new_target")
@pytest.mark.operations("success")
def test_custom_target(cli, new_target, openapi3_schema_url):
    # When hooks are passed to the CLI call
    # And it contains registering a new target
    result = cli.main("run", "--generation-maximize", "new_target", openapi3_schema_url, hooks=new_target)
    # Then the test run should be successful
    assert result.exit_code == ExitCode.OK, result.stdout
    # And the specified target is called
    assert "NEW TARGET IS CALLED" in result.stdout


@pytest.mark.usefixtures("new_target")
@pytest.mark.operations("success")
def test_custom_target_graphql(cli, new_target, graphql_url):
    # When hooks are passed to the CLI call
    # And it contains registering a new target
    result = cli.main(
        "run",
        "--generation-maximize",
        "new_target",
        graphql_url,
        "--suppress-health-check=too_slow,filter_too_much",
        "--max-examples=1",
        hooks=new_target,
    )
    # Then the test run should be successful
    assert result.exit_code == ExitCode.OK, result.stdout
    # And the specified target is called
    assert "NEW TARGET IS CALLED" in result.stdout


@pytest.fixture
def target_function():
    @schemathesis.target
    def new_target(context):
        return 0.5

    yield target_function

    TARGETS.unregister("new_target")


def test_register_returns_a_value(target_function):
    # When a function is registered via the `schemathesis.target` decorator
    # Then this function should be available for further usage
    # See #721
    assert target_function is not None
