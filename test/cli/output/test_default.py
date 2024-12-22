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


@pytest.fixture(autouse=True)
def click_context():
    """Add terminal colors to the output in tests."""
    with click.Context(schemathesis.cli.run, color=True):
        yield


@pytest.fixture
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


@pytest.fixture
def results_set(operation):
    statistic = models.TestResult(
        operation.method,
        operation.full_path,
        data_generation_method=[DataGenerationMethod.default()],
        verbose_name=f"{operation.method} {operation.full_path}",
    )
    return models.TestResultSet(seed=42, results=[statistic])


@pytest.fixture
def after_execution(results_set, operation):
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
    ("title", "separator", "expected"),
    [
        ("TEST", "-", "------- TEST -------"),
        ("TEST", "*", "******* TEST *******"),
    ],
)
def test_display_section_name(capsys, title, separator, expected):
    # When section name is displayed
    default.display_section_name(title, separator=separator)
    out = capsys.readouterr().out.strip()
    terminal_width = default.get_terminal_width()
    # It should fit into the terminal width
    assert len(click.unstyle(out)) == terminal_width
    # And the section name should be bold
    assert expected in out


@pytest.mark.parametrize("verbosity", [0, 1])
def test_handle_initialized(capsys, mocker, execution_context, swagger_20, verbosity):
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
    assert "Collected API operations: 1" in out
    # And the output has an empty line in the end
    assert out.endswith("\n")


def test_display_multiple_warnings(capsys, execution_context):
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


def test_capture_hypothesis_output():
    # When Hypothesis output us captured
    with utils.capture_hypothesis_output() as hypothesis_output:
        value = "Some text"
        report(value)
        report(value)
    # Then all calls to internal Hypothesis reporting will put its output to a list
    assert hypothesis_output == [value, value]


@pytest.mark.parametrize(
    ("position", "length", "expected"), [(1, 100, "[  1%]"), (20, 100, "[ 20%]"), (100, 100, "[100%]")]
)
def test_get_percentage(position, length, expected):
    assert default.get_percentage(position, length) == expected


@pytest.mark.parametrize("body", [{}, {"foo": "bar"}, NOT_SET])
def test_display_single_failure(capsys, execution_context, operation, body, response):
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


@pytest.mark.parametrize("show_errors_tracebacks", [True, False])
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
