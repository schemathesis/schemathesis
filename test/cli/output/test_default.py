import io
import os

import click
import hypothesis
import pytest
import requests
from hypothesis.reporting import report
from urllib3 import HTTPResponse

import schemathesis
import schemathesis.cli.context
from schemathesis import models, runner, utils
from schemathesis.cli.output import default
from schemathesis.cli.output.default import display_internal_error
from schemathesis.constants import NOT_SET
from schemathesis.generation import DataGenerationMethod
from schemathesis.models import OperationDefinition
from schemathesis.runner.events import Finished, InternalError
from schemathesis.runner.serialization import SerializedTestResult

from ...utils import strip_style_win32


@pytest.fixture(autouse=True)
def click_context():
    """Add terminal colors to the output in tests."""
    with click.Context(schemathesis.cli.run, color=True):
        yield


@pytest.fixture()
def execution_context():
    return schemathesis.cli.context.ExecutionContext(hypothesis.settings(), [], operations_count=1)


@pytest.fixture
def operation(swagger_20):
    return models.APIOperation(
        "/success",
        "GET",
        definition=OperationDefinition({}, {}, ""),
        base_url="http://127.0.0.1:8080",
        schema=swagger_20,
    )


@pytest.fixture
def response():
    response = requests.Response()
    response._content = b'{"id": 5}'
    response.status_code = 201
    response.headers["Content-Type"] = "application/json"
    response.raw = HTTPResponse(
        body=io.BytesIO(response._content), status=response.status_code, headers=response.headers
    )
    response.request = requests.PreparedRequest()
    response.request.prepare(method="POST", url="http://example.com", headers={"Content-Type": "application/json"})
    return response


@pytest.fixture()
def results_set(operation):
    statistic = models.TestResult(
        operation.method,
        operation.full_path,
        data_generation_method=[DataGenerationMethod.default()],
        verbose_name=f"{operation.method} {operation.full_path}",
    )
    return models.TestResultSet(seed=42, results=[statistic])


@pytest.fixture()
def after_execution(results_set, operation, swagger_20):
    return runner.events.AfterExecution.from_result(
        result=results_set.results[0],
        status=models.Status.success,
        hypothesis_output=[],
        elapsed_time=1.0,
        operation=operation,
        data_generation_method=[DataGenerationMethod.positive],
        correlation_id="foo",
    )


def test_get_terminal_width():
    assert default.get_terminal_width() >= 80


@pytest.mark.parametrize(
    "title,separator,printed,expected",
    [
        ("TEST", "-", "data in section", "------- TEST -------"),
        ("TEST", "*", "data in section", "******* TEST *******"),
    ],
)
def test_display_section_name(capsys, title, separator, printed, expected):
    # When section name is displayed
    default.display_section_name(title, separator=separator)
    out = capsys.readouterr().out.strip()
    terminal_width = default.get_terminal_width()
    # It should fit into the terminal width
    assert len(click.unstyle(out)) == terminal_width
    # And the section name should be bold
    assert strip_style_win32(click.style(click.unstyle(out), bold=True)) == out
    assert expected in out


@pytest.mark.parametrize("verbosity", (0, 1))
def test_handle_initialized(capsys, mocker, execution_context, results_set, swagger_20, verbosity):
    execution_context.verbosity = verbosity
    # Given Initialized event
    event = runner.events.Initialized.from_schema(schema=swagger_20, seed=42)
    # When this even is handled
    default.handle_initialized(execution_context, event)
    default.handle_before_probing(execution_context, mocker.Mock(auto_spec=True))
    default.handle_after_probing(execution_context, mocker.Mock(probes=None))
    out = capsys.readouterr().out
    lines = out.split("\n")
    # Then initial title is displayed
    assert " Schemathesis test session starts " in lines[0]
    if verbosity == 1:
        # And platform information is there
        assert lines[1].startswith("platform")
        # And current directory
        assert f"rootdir: {os.getcwd()}" in lines
    # And number of collected operations
    assert strip_style_win32(click.style("Collected API operations: 1", bold=True)) in lines
    # And the output has an empty line in the end
    assert out.endswith("\n")


def test_display_statistic(capsys, swagger_20, execution_context, operation, response):
    # Given multiple successful & failed checks in a single test
    success = models.Check(
        "not_a_server_error", models.Status.success, response, 0, models.Case(operation, generation_time=0.0)
    )
    failure = models.Check(
        "not_a_server_error", models.Status.failure, response, 0, models.Case(operation, generation_time=0.0)
    )
    single_test_statistic = models.TestResult(
        method=operation.method,
        path=operation.full_path,
        verbose_name=f"{operation.method} {operation.full_path}",
        data_generation_method=[DataGenerationMethod.default()],
        checks=[
            success,
            success,
            success,
            failure,
            failure,
            models.Check(
                "different_check", models.Status.success, response, 0, models.Case(operation, generation_time=0.0)
            ),
        ],
    )
    results = models.TestResultSet(seed=42, results=[single_test_statistic])
    event = Finished.from_results(results, running_time=1.0)
    # When test results are displayed
    default.display_statistic(execution_context, event)

    lines = [line for line in capsys.readouterr().out.split("\n") if line]
    failed = strip_style_win32(click.style("FAILED", bold=True, fg="red"))
    passed = strip_style_win32(click.style("PASSED", bold=True, fg="green"))
    # Then all check results should be properly displayed with relevant colors
    assert lines[2:4] == [
        f"    not_a_server_error                    3 / 5 passed          {failed} ",
        f"    different_check                       1 / 1 passed          {passed} ",
    ]


def test_display_multiple_warnings(capsys, swagger_20, execution_context, operation, response):
    results = models.TestResultSet(seed=42, results=[])
    results.add_warning("Foo")
    results.add_warning("Bar")
    event = Finished.from_results(results, running_time=1.0)
    # When test results are displayed
    default.display_statistic(execution_context, event)
    lines = [click.unstyle(line) for line in capsys.readouterr().out.split("\n") if line]
    assert lines[2:5] == [
        "WARNINGS:",
        "  - Foo",
        "  - Bar",
    ]


def test_display_statistic_empty(capsys, execution_context, results_set):
    default.display_statistic(execution_context, Finished.from_results(results_set, running_time=1.23))
    assert capsys.readouterr().out.split("\n")[2] == strip_style_win32(
        click.style("No checks were performed.", bold=True)
    )


def test_display_statistic_junitxml(capsys, execution_context, results_set):
    xml_path = "/tmp/junit.xml"
    execution_context.junit_xml_file = xml_path
    default.display_statistic(execution_context, Finished.from_results(results_set, running_time=1.23))
    assert capsys.readouterr().out.split("\n")[4] == strip_style_win32(
        click.style("JUnit XML file", bold=True) + click.style(f": {xml_path}")
    )


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
    assert default.get_percentage(position, length) == expected


@pytest.mark.parametrize("current_line_length", (0, 20))
@pytest.mark.parametrize("operations_processed, percentage", ((0, "[  0%]"), (1, "[100%]")))
def test_display_percentage(
    capsys, execution_context, after_execution, swagger_20, current_line_length, operations_processed, percentage
):
    execution_context.current_line_length = current_line_length
    execution_context.operations_processed = operations_processed
    # When percentage is displayed
    default.display_percentage(execution_context, after_execution)
    out = capsys.readouterr().out
    # Then the whole line fits precisely to the terminal width. Note `-1` is padding, that is calculated in a
    # different place when the line is printed
    assert len(click.unstyle(out)) + current_line_length - 1 == default.get_terminal_width()
    # And the percentage displayed as expected in cyan color
    assert out.strip() == strip_style_win32(click.style(percentage, fg="cyan"))


def test_display_hypothesis_output(capsys):
    # When Hypothesis output is displayed
    default.display_hypothesis_output(["foo", "bar"])
    lines = capsys.readouterr().out.split("\n")
    # Then the relevant section title is displayed
    assert " HYPOTHESIS OUTPUT" in lines[0]
    # And the output is displayed as separate lines in red color
    assert " ".join(lines[1:3]) == strip_style_win32(click.style("foo bar", fg="red"))


@pytest.mark.parametrize("body", ({}, {"foo": "bar"}, NOT_SET))
def test_display_single_failure(capsys, swagger_20, execution_context, operation, body, response):
    # Given a single test result with multiple successful & failed checks
    media_type = "application/json" if body is not NOT_SET else None
    success = models.Check(
        "not_a_server_error",
        models.Status.success,
        response,
        0,
        models.Case(operation, generation_time=0.0, body=body, media_type=media_type),
    )
    failure = models.Check(
        "not_a_server_error",
        models.Status.failure,
        response,
        0,
        models.Case(operation, generation_time=0.0, body=body, media_type=media_type),
    )
    test_statistic = models.TestResult(
        method=operation.method,
        path=operation.full_path,
        data_generation_method=[DataGenerationMethod.default()],
        verbose_name=f"{operation.method} {operation.full_path}",
        checks=[
            success,
            success,
            success,
            failure,
            failure,
            models.Check(
                "different_check",
                models.Status.success,
                response,
                0,
                models.Case(
                    operation,
                    generation_time=0.0,
                    body=body,
                    media_type=media_type,
                ),
            ),
        ],
    )
    # When this failure is displayed
    default.display_failures_for_single_test(execution_context, SerializedTestResult.from_test_result(test_statistic))
    out = capsys.readouterr().out
    lines = out.split("\n")
    # Then the path is displayed as a subsection
    assert " GET /v1/success " in lines[0]


@pytest.mark.parametrize(
    "status, expected_symbol, color",
    ((models.Status.success, ".", "green"), (models.Status.failure, "F", "red"), (models.Status.error, "E", "red")),
)
def test_handle_after_execution(capsys, execution_context, after_execution, status, expected_symbol, color):
    # Given AfterExecution even with certain status
    after_execution.status = status
    # When this event is handled
    default.handle_after_execution(execution_context, after_execution)

    assert after_execution.current_operation == "GET /v1/success"

    lines = capsys.readouterr().out.strip().split("\n")
    symbol, percentage = lines[0].split()
    # Then the symbol corresponding to the status is displayed with a proper color
    assert strip_style_win32(click.style(expected_symbol, fg=color)) == symbol
    # And percentage is displayed in cyan color
    assert strip_style_win32(click.style("[100%]", fg="cyan")) == percentage


def test_after_execution_attributes(execution_context, after_execution):
    # When `handle_after_execution` is executed
    default.handle_after_execution(execution_context, after_execution)
    # Then number of operations processed grows by 1
    assert execution_context.operations_processed == 1
    # And the line length grows by 1 symbol
    assert execution_context.current_line_length == 1

    default.handle_after_execution(execution_context, after_execution)
    assert execution_context.operations_processed == 2
    assert execution_context.current_line_length == 2


@pytest.mark.parametrize("show_errors_tracebacks", (True, False))
def test_display_internal_error(capsys, execution_context, show_errors_tracebacks):
    execution_context.show_trace = show_errors_tracebacks
    try:
        raise ZeroDivisionError("division by zero")
    except ZeroDivisionError as exc:
        event = InternalError.from_exc(exc)
        display_internal_error(execution_context, event)
        out = capsys.readouterr().out.strip()
        assert ("Traceback (most recent call last):" in out) is show_errors_tracebacks
        assert "ZeroDivisionError: division by zero" in out


def test_display_summary(capsys, results_set, swagger_20):
    # Given the Finished event
    event = runner.events.Finished.from_results(results=results_set, running_time=1.257)
    # When `display_summary` is called
    default.display_summary(event)
    out = capsys.readouterr().out.strip()
    # Then number of total tests & total running time should be displayed
    assert "=== 1 passed in 1.26s ===" in out
    # And it should be in green & bold style
    assert strip_style_win32(click.style(click.unstyle(out), fg="green", bold=True)) == out
