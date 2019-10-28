import click
import pytest
from hypothesis.reporting import report

import schemathesis
from schemathesis import runner, utils
from schemathesis.cli import output
from schemathesis.models import Endpoint, StatsCollector


@pytest.fixture(autouse=True)
def click_context():
    """Add terminal colors to the output in tests."""
    with click.Context(schemathesis.cli.run, color=True):
        yield


@pytest.mark.parametrize(
    "title,separator,printed,expected",
    [
        ("TEST", "-", "data in section", "------- TEST -------"),
        ("TEST", "*", "data in section", "******* TEST *******"),
    ],
)
def test_display_section_name(capsys, title, separator, printed, expected):
    output.display_section_name(title, separator=separator)
    out = click.unstyle(capsys.readouterr().out.strip())
    assert len(out) == output.get_terminal_width()
    assert expected in out


def test_display_statistic(capsys):
    output.display_statistic(
        runner.StatsCollector(
            {
                "not_a_server_error": {"total": 5, "ok": 3, "error": 2},
                "different_check": {"total": 1, "ok": 1, "error": 0},
            }
        )
    )

    lines = [line for line in capsys.readouterr().out.split("\n") if line]
    failed = click.style("FAILED", bold=True, fg="red")
    not_a_server_error = click.style("not_a_server_error", bold=True)
    different_check = click.style("different_check", bold=True)
    passed = click.style("PASSED", bold=True, fg="green")
    assert lines[1:3] == [
        f"{not_a_server_error}            3 / 5 passed          {failed} ",
        f"{different_check}               1 / 1 passed          {passed} ",
    ]


def test_display_statistic_empty(capsys):
    output.display_statistic(runner.StatsCollector({}))
    assert capsys.readouterr().out.split("\n")[2] == click.style("No checks were performed.", bold=True)


def test_capture_hypothesis_output():
    with utils.capture_hypothesis_output() as hypothesis_output:
        value = "Some text"
        report(value)
        report(value)
    assert hypothesis_output == [value, value]


@pytest.mark.parametrize("position, length, expected", ((1, 100, "[  1%]"), (20, 100, "[ 20%]"), (100, 100, "[100%]")))
def test_get_percentage(position, length, expected):
    assert output.get_percentage(position, length) == expected


@pytest.mark.parametrize("endpoints_processed, percentage", ((0, "[  0%]"), (1, "[100%]")))
def test_display_percentage(capsys, swagger_20, endpoints_processed, percentage):
    statistic = StatsCollector()
    context = runner.events.ExecutionContext([])
    context.endpoints_processed = endpoints_processed
    endpoint = Endpoint("/success", "GET")
    event = runner.events.AfterExecution(
        statistic=statistic, schema=swagger_20, endpoint=endpoint, result=runner.events.ExecutionResult.success
    )
    output.display_percentage(context, event)
    out = capsys.readouterr().out.strip()
    assert out == click.style(percentage, fg="cyan")


def test_display_hypothesis_output(capsys):
    output.display_hypothesis_output(["foo", "bar"])
    lines = capsys.readouterr().out.split("\n")
    assert " ".join(lines[1:3]) == click.style("foo bar", fg="red")
