import pytest
from _pytest.main import ExitCode
from hypothesis import Phase, given, settings
from hypothesis import strategies as st
from hypothesis.control import current_build_context

import schemathesis
from schemathesis.generation.metrics import METRICS, MetricCollector, maximize


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
def test_custom_metric(ctx, cli, new_metric):
    api = ctx.openapi.apps.success()
    # When hooks are passed to the CLI call
    # And it contains registering a new metric
    result = cli.main("run", "--generation-maximize", "new_metric", api.schema_url, hooks=new_metric)
    # Then the test run should be successful
    assert result.exit_code == ExitCode.OK, result.stdout
    # And the specified metric is called
    assert "NEW METRIC IS CALLED" in result.stdout


@pytest.mark.usefixtures("new_metric")
def test_custom_metric_graphql(ctx, cli, new_metric):
    # When hooks are passed to the CLI call
    # And it contains registering a new metric
    api = ctx.graphql.apps.books()
    result = cli.main(
        "run",
        "--generation-maximize",
        "new_metric",
        api.schema_url,
        "--suppress-health-check=too_slow,filter_too_much",
        "--max-examples=1",
        "--mode=positive",
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

    yield new_metric

    METRICS.unregister("new_metric")


def test_register_returns_a_value(metric_function):
    # When a function is registered via the `schemathesis.metric` decorator
    # Then this function should be available for further usage
    # See #721
    assert metric_function is not None


def _observed_targets(fn):
    observed = []

    @given(st.integers())
    @settings(max_examples=1, database=None, phases=[Phase.generate])
    def run(_):
        fn()
        observed.append(dict(current_build_context().data.target_observations))

    run()
    return observed[0]


def _collect(metrics, case, response):
    collector = MetricCollector(metrics=metrics)
    collector.store(case, response)
    collector.maximize()


def test_maximize_targets_success_and_configured_metrics(metric_function, case_factory, response_factory):
    case = case_factory()
    response = response_factory.requests(status_code=200)
    success = f"{case.operation.label}:success_rate"

    assert _observed_targets(lambda: maximize([], case=case, response=response)) == {success: 1.0}
    assert _observed_targets(lambda: maximize([metric_function], case=case, response=response)) == {
        success: 1.0,
        "new_metric": 0.5,
    }


def test_metric_collector_targets_only_configured_metrics(metric_function, case_factory, response_factory):
    case = case_factory()
    response = response_factory.requests(status_code=200)

    assert _observed_targets(lambda: _collect([], case, response)) == {}
    assert _observed_targets(lambda: _collect([metric_function], case, response)) == {"new_metric": 0.5}
