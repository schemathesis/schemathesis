import pytest
from _pytest.main import ExitCode

import schemathesis
from schemathesis.generation.metrics import METRICS


@pytest.fixture
def new_metric(ctx, cli):
    module = ctx.write_pymodule(
        """
import click

@schemathesis.metric
def new_metric(ctx) -> float:
    click.echo("NEW METRIC IS CALLED")
    assert ctx.case.meta.generation.mode is not None, "Empty generation mode"
    return float(len(ctx.response.content))
"""
    )
    yield module
    METRICS.unregister("new_metric")
    # To verify that "new_metric" is unregistered
    assert "new_metric" not in cli.run("--help").stdout


@pytest.mark.usefixtures("new_metric")
@pytest.mark.operations("success")
def test_custom_metric(cli, new_metric, openapi3_schema_url):
    # When hooks are passed to the CLI call
    # And it contains registering a new metric
    result = cli.main("run", "--generation-maximize", "new_metric", openapi3_schema_url, hooks=new_metric)
    # Then the test run should be successful
    assert result.exit_code == ExitCode.OK, result.stdout
    # And the specified metric is called
    assert "NEW METRIC IS CALLED" in result.stdout


@pytest.mark.usefixtures("new_metric")
@pytest.mark.operations("success")
def test_custom_metric_graphql(cli, new_metric, graphql_url):
    # When hooks are passed to the CLI call
    # And it contains registering a new metric
    result = cli.main(
        "run",
        "--generation-maximize",
        "new_metric",
        graphql_url,
        "--suppress-health-check=too_slow,filter_too_much",
        "--max-examples=1",
        hooks=new_metric,
    )
    # Then the test run should be successful
    assert result.exit_code == ExitCode.OK, result.stdout
    # And the specified metric is called
    assert "NEW METRIC IS CALLED" in result.stdout


@pytest.fixture
def metric_function():
    @schemathesis.metric
    def new_metric(context):
        return 0.5

    yield metric_function

    METRICS.unregister("new_metric")


def test_register_returns_a_value(metric_function):
    # When a function is registered via the `schemathesis.metric` decorator
    # Then this function should be available for further usage
    # See #721
    assert metric_function is not None
