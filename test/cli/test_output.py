import os
import sys

import click
import hypothesis
import pytest
from hypothesis.reporting import report

import schemathesis
from schemathesis import models, runner, utils
from schemathesis.cli import output


@pytest.fixture(autouse=True)
def click_context():
    """Add terminal colors to the output in tests."""
    with click.Context(schemathesis.cli.run, color=True):
        yield


@pytest.fixture()
def execution_context():
    return runner.events.ExecutionContext([])


@pytest.fixture()
def results_set():
    statistic = models.TestResult("/success", "GET")
    return models.TestResultSet([statistic])


@pytest.fixture()
def after_execution(results_set, swagger_20):
    endpoint = models.Endpoint("/success", "GET")
    return runner.events.AfterExecution(
        results=results_set, schema=swagger_20, endpoint=endpoint, status=models.Status.success
    )


@pytest.mark.parametrize(
    "title,separator,printed,expected",
    [
        ("TEST", "-", "data in section", "------- TEST -------"),
        ("TEST", "*", "data in section", "******* TEST *******"),
    ],
)
def test_display_section_name(capsys, title, separator, printed, expected):
    # When section name is displayed
    output.display_section_name(title, separator=separator)
    out = capsys.readouterr().out.strip()
    terminal_width = output.get_terminal_width()
    # It should fit into the terminal width
    assert len(click.unstyle(out)) == terminal_width
    # And the section name should be bold
    assert click.style(click.unstyle(out), bold=True) == out
    assert expected in out


def test_handle_initialized(capsys, execution_context, results_set, swagger_20):
    # Given Initialized event
    event = runner.events.Initialized(
        results=results_set, schema=swagger_20, checks=(), hypothesis_settings=hypothesis.settings()
    )
    # When this even is handled
    output.handle_initialized(execution_context, event)
    out = capsys.readouterr().out
    lines = out.split("\n")
    # Then initial title is displayed
    assert " Schemathesis test session starts " in lines[0]
    # And platform information is there
    assert lines[1].startswith("platform")
    # And current directory
    assert f"rootdir: {os.getcwd()}" in lines
    # And number of collected endpoints
    assert click.style("collected endpoints: 1", bold=True) in lines
    # And the output has an empty line in the end
    assert out.endswith("\n")


def test_display_statistic(capsys):
    # Given multiple successful & failed checks in a single test
    success = models.Check("not_a_server_error", models.Status.success)
    failure = models.Check("not_a_server_error", models.Status.failure)
    single_test_statistic = models.TestResult(
        "/success",
        "GET",
        [success, success, success, failure, failure, models.Check("different_check", models.Status.success)],
    )
    results = models.TestResultSet([single_test_statistic])
    # When test results are displayed
    output.display_statistic(results)

    lines = [line for line in capsys.readouterr().out.split("\n") if line]
    failed = click.style("FAILED", bold=True, fg="red")
    not_a_server_error = click.style("not_a_server_error", bold=True)
    different_check = click.style("different_check", bold=True)
    passed = click.style("PASSED", bold=True, fg="green")
    # Then all check results should be properly displayed with relevant colors
    assert lines[1:3] == [
        f"{not_a_server_error}            3 / 5 passed          {failed} ",
        f"{different_check}               1 / 1 passed          {passed} ",
    ]


def test_display_statistic_empty(capsys, results_set):
    output.display_statistic(results_set)
    assert capsys.readouterr().out.split("\n")[2] == click.style("No checks were performed.", bold=True)


def test_capture_hypothesis_output():
    # When Hypothesis output us captured
    with utils.capture_hypothesis_output() as hypothesis_output:
        value = "Some text"
        report(value)
        report(value)
    # Then all calls to internal Hypothesis reporting will put its output to a list
    assert hypothesis_output == [value, value]


@pytest.mark.parametrize("position, length, expected", ((1, 100, "[  1%]"), (20, 100, "[ 20%]"), (100, 100, "[100%]")))
def test_get_percentage(position, length, expected):
    assert output.get_percentage(position, length) == expected


@pytest.mark.parametrize("current_line_length", (0, 20))
@pytest.mark.parametrize("endpoints_processed, percentage", ((0, "[  0%]"), (1, "[100%]")))
def test_display_percentage(
    capsys, execution_context, after_execution, swagger_20, current_line_length, endpoints_processed, percentage
):
    execution_context.current_line_length = current_line_length
    execution_context.endpoints_processed = endpoints_processed
    # When percentage is displayed
    output.display_percentage(execution_context, after_execution)
    out = capsys.readouterr().out
    # Then the whole line fits precisely to the terminal width
    assert len(click.unstyle(out)) + current_line_length == output.get_terminal_width()
    # And the percentage displayed as expected in cyan color
    assert out.strip() == click.style(percentage, fg="cyan")


def test_display_hypothesis_output(capsys):
    # When Hypothesis output is displayed
    output.display_hypothesis_output(["foo", "bar"])
    lines = capsys.readouterr().out.split("\n")
    # Then the relevant section title is displayed
    assert " HYPOTHESIS OUTPUT" in lines[0]
    # And the output is displayed as separate lines in red color
    assert " ".join(lines[1:3]) == click.style("foo bar", fg="red")


@pytest.mark.parametrize("body", ({}, {"foo": "bar"}, None))
def test_display_single_failure(capsys, body):
    # Given a single test result with multiple successful & failed checks
    success = models.Check("not_a_server_error", models.Status.success)
    failure = models.Check(
        "not_a_server_error",
        models.Status.failure,
        models.Case("/success", "GET", base_url="http://example.com", body=body),
    )
    test_statistic = models.TestResult(
        "/success",
        "GET",
        [success, success, success, failure, failure, models.Check("different_check", models.Status.success)],
    )
    # When this failure is displayed
    output.display_single_failure(test_statistic)
    out = capsys.readouterr().out
    lines = out.split("\n")
    # Then the endpoint name is displayed as a subsection
    assert " GET: /success " in lines[0]
    # And check name is displayed in red
    assert lines[1] == click.style("Check           : not_a_server_error", fg="red")
    # And body should be displayed if it is not None
    if body is None:
        assert "Body" not in out
    else:
        assert click.style(f"Body            : {body}", fg="red") in lines
    # And empty parameters are not present in the output
    assert "Path parameters" not in out
    # And not needed attributes are not displayed
    assert "Path" not in out
    assert "Method" not in out
    assert "Base url" not in out


@pytest.mark.parametrize(
    "status, expected_symbol, color",
    ((models.Status.success, ".", "green"), (models.Status.failure, "F", "red"), (models.Status.error, "E", "red")),
)
def test_handle_after_execution(capsys, execution_context, after_execution, status, expected_symbol, color):
    # Given AfterExecution even with certain status
    after_execution.status = status
    # When this event is handled
    output.handle_after_execution(execution_context, after_execution)

    lines = capsys.readouterr().out.strip().split("\n")
    symbol, percentage = lines[0].split()
    # Then the symbol corresponding to the status is displayed with a proper color
    assert click.style(expected_symbol, fg=color) == symbol
    # And percentage is displayed in cyan color
    assert click.style("[100%]", fg="cyan") == percentage


def test_after_execution_attributes(execution_context, after_execution):
    # When `handle_after_execution` is executed
    output.handle_after_execution(execution_context, after_execution)
    # Then number of endpoints processed grows by 1
    assert execution_context.endpoints_processed == 1
    # And the line length grows by 1 symbol
    assert execution_context.current_line_length == 1

    output.handle_after_execution(execution_context, after_execution)
    assert execution_context.endpoints_processed == 2
    assert execution_context.current_line_length == 2


def test_display_single_error(capsys):
    # Given exception is multiline
    exception = None
    try:
        exec("some invalid code")
    except SyntaxError as exc:
        exception = exc

    result = models.TestResult("/success", "GET")
    result.add_error(exception)
    # When the related test result is displayed
    output.display_single_error(result)
    lines = capsys.readouterr().out.strip().split("\n")
    # Then it should be correctly formatted and displayed in red color
    if sys.version_info <= (3, 8):
        expected = '  File "<string>", line 1\n    some invalid code\n               ^\nSyntaxError: invalid syntax\n'
    else:
        expected = '  File "<string>", line 1\n    some invalid code\n         ^\nSyntaxError: invalid syntax\n'
    assert "\n".join(lines[1:6]) == click.style(expected, fg="red")


def test_display_failures(capsys, results_set):
    # Given two test results - success and failure
    failure = models.TestResult("/api/failure", "GET")
    failure.add_failure("test", models.Case("/api/failure", "GET", base_url="http://127.0.0.1:8080"))
    results_set.append(failure)
    # When the failures are displayed
    output.display_failures(results_set)
    out = capsys.readouterr().out.strip()
    # Then section title is displayed
    assert " FAILURES " in out
    # And endpoint with a failure is displayed as a subsection
    assert " GET: /api/failure " in out
    # And check name is displayed
    assert "Check           : test" in out
    assert "Run this Python code to reproduce this failure: " in out
    assert "requests.get('http://127.0.0.1:8080/api/failure')" in out


def test_display_errors(capsys, results_set):
    # Given two test results - success and error
    error = models.TestResult("/api/error", "GET")
    error.add_error(
        ConnectionError("Connection refused!"),
        models.Case("/api/error", "GET", base_url="http://127.0.0.1:8080", query={"a": 1}),
    )
    results_set.append(error)
    # When the errors are displayed
    output.display_errors(results_set)
    out = capsys.readouterr().out.strip()
    # Then section title is displayed
    assert " ERRORS " in out
    # And endpoint with an error is displayed as a subsection
    assert " GET: /api/error " in out
    # And the error itself is displayed
    assert "ConnectionError: Connection refused!" in out
    # And the example is displayed
    assert "Query           : {'a': 1}" in out


@pytest.mark.parametrize(
    "attribute, expected",
    ((models.Case.__attrs_attrs__[0], "Path"), (models.Case.__attrs_attrs__[3], "Path parameters")),
)
def test_make_verbose_name(attribute, expected):
    assert output.make_verbose_name(attribute) == expected
