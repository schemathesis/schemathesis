import io
import os
import sys

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
from schemathesis.constants import SCHEMATHESIS_TEST_CASE_HEADER, DataGenerationMethod
from schemathesis.runner.events import Finished, InternalError
from schemathesis.runner.serialization import SerializedTestResult
from schemathesis.utils import NOT_SET

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
    return models.APIOperation("/success", "GET", definition={}, base_url="http://127.0.0.1:8080", schema=swagger_20)


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
    return models.TestResultSet([statistic])


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
def test_handle_initialized(capsys, execution_context, results_set, swagger_20, verbosity):
    execution_context.verbosity = verbosity
    # Given Initialized event
    event = runner.events.Initialized.from_schema(schema=swagger_20)
    # When this even is handled
    default.handle_initialized(execution_context, event)
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
    assert out.endswith("\n\n")


def test_display_statistic(capsys, swagger_20, execution_context, operation, response):
    # Given multiple successful & failed checks in a single test
    success = models.Check("not_a_server_error", models.Status.success, response, 0, models.Case(operation))
    failure = models.Check("not_a_server_error", models.Status.failure, response, 0, models.Case(operation))
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
            models.Check("different_check", models.Status.success, response, 0, models.Case(operation)),
        ],
    )
    results = models.TestResultSet([single_test_statistic])
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
    results = models.TestResultSet([])
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
    default.display_statistic(execution_context, results_set)
    assert capsys.readouterr().out.split("\n")[2] == strip_style_win32(
        click.style("No checks were performed.", bold=True)
    )


def test_display_statistic_junitxml(capsys, execution_context, results_set):
    xml_path = "/tmp/junit.xml"
    execution_context.junit_xml_file = xml_path
    default.display_statistic(execution_context, results_set)
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
    success = models.Check("not_a_server_error", models.Status.success, response, 0, models.Case(operation, body=body))
    failure = models.Check("not_a_server_error", models.Status.failure, response, 0, models.Case(operation, body=body))
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
            models.Check("different_check", models.Status.success, response, 0, models.Case(operation, body=body)),
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
def test_display_single_error(capsys, swagger_20, operation, execution_context, show_errors_tracebacks):
    # Given exception is multiline
    exception = None
    try:
        exec("some invalid code")
    except SyntaxError as exc:
        exception = exc

    result = models.TestResult(
        operation.method,
        operation.path,
        verbose_name=f"{operation.method} {operation.full_path}",
        data_generation_method=[DataGenerationMethod.default()],
    )
    result.add_error(exception)
    # When the related test result is displayed
    execution_context.show_errors_tracebacks = show_errors_tracebacks
    default.display_single_error(execution_context, SerializedTestResult.from_test_result(result))
    lines = capsys.readouterr().out.strip().split("\n")
    # Then it should be correctly formatted and displayed in red color
    if sys.version_info >= (3, 10):
        expected = '  File "<string>", line 1\n    some invalid code\n         ^^^^^^^\nSyntaxError: invalid syntax\n'
    elif sys.version_info <= (3, 8):
        expected = '  File "<string>", line 1\n    some invalid code\n               ^\nSyntaxError: invalid syntax\n'
    else:
        expected = '  File "<string>", line 1\n    some invalid code\n         ^\nSyntaxError: invalid syntax\n'
    if show_errors_tracebacks:
        lines = click.unstyle("\n".join(lines)).split("\n")
        assert lines[1] == "Traceback (most recent call last):"
        # There is a path on the next line, it is simpler to not check it since it doesn't give much value
        # But presence of traceback itself is checked
        expected = f'    exec("some invalid code")\n{expected}'
        assert "\n".join(lines[3:8]) == expected.strip("\n")
    else:
        assert "\n".join(lines[1:6]) == strip_style_win32(click.style(expected, fg="red")).rstrip("\n")


@pytest.mark.parametrize("verbosity", (0, 1))
def test_display_failures(swagger_20, capsys, execution_context, results_set, verbosity, response, mock_case_id):
    execution_context.verbosity = verbosity
    # Given two test results - success and failure
    operation = models.APIOperation("/api/failure", "GET", {}, base_url="http://127.0.0.1:8080", schema=swagger_20)
    failure = models.TestResult(
        operation.method,
        operation.full_path,
        verbose_name=f"{operation.method} {operation.full_path}",
        data_generation_method=[DataGenerationMethod.default()],
    )
    failure.add_failure("test", models.Case(operation), response, 0, "Message", None)
    execution_context.results.append(SerializedTestResult.from_test_result(failure))
    results_set.append(failure)
    event = Finished.from_results(results_set, 1.0)
    # When the failures are displayed
    default.display_failures(execution_context, event)
    out = capsys.readouterr().out.strip()
    # Then section title is displayed
    assert " FAILURES " in out
    # And operation with a failure is displayed as a subsection
    assert " GET /v1/api/failure " in out
    assert "Message" in out
    assert "Run this cURL command to reproduce this failure:" in out
    headers = f"-H '{SCHEMATHESIS_TEST_CASE_HEADER}: {mock_case_id.hex}'"
    assert f"curl -X GET {headers} http://127.0.0.1:8080/api/failure" in out


@pytest.mark.parametrize("show_errors_tracebacks", (True, False))
def test_display_errors(swagger_20, capsys, results_set, execution_context, show_errors_tracebacks):
    # Given two test results - success and error
    operation = models.APIOperation("/api/error", "GET", {}, swagger_20)
    error = models.TestResult(
        operation.method,
        operation.full_path,
        verbose_name=f"{operation.method} {operation.full_path}",
        data_generation_method=[DataGenerationMethod.default()],
        seed=123,
    )
    error.add_error(ConnectionError("Connection refused!"), models.Case(operation, query={"a": 1}))
    results_set.append(error)
    execution_context.results.append(SerializedTestResult.from_test_result(error))
    event = Finished.from_results(results_set, 1.0)
    # When the errors are displayed
    execution_context.show_errors_tracebacks = show_errors_tracebacks
    default.display_errors(execution_context, event)
    out = capsys.readouterr().out.strip()
    # Then section title is displayed
    assert " ERRORS " in out
    help_message_exists = (
        "Add this option to your command line parameters to see full tracebacks: --show-errors-tracebacks" in out
    )
    # And help message is displayed only if tracebacks are not shown
    assert help_message_exists is not show_errors_tracebacks
    # And operation with an error is displayed as a subsection
    assert " GET /v1/api/error " in out
    # And the error itself is displayed
    assert "ConnectionError: Connection refused!" in out
    assert "Or add this option to your command line parameters: --hypothesis-seed=123" in out


@pytest.mark.parametrize("show_errors_tracebacks", (True, False))
def test_display_internal_error(capsys, execution_context, show_errors_tracebacks):
    execution_context.show_errors_tracebacks = show_errors_tracebacks
    try:
        1 / 0
    except ArithmeticError as exc:
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


@pytest.mark.parametrize(
    "value, expected",
    (
        ("message", "message"),
        (
            """Details:

'apikey' is a required property

Failed validating 'required' in schema:
    {'description': 'Response body format for service ID V1 REST requests',""",
            """Details:

'apikey' is a required property

Failed validating 'required' in schema""",
        ),
    ),
)
def test_reduce_schema_error(value, expected):
    assert default.reduce_schema_error(value) == expected
