import click
import hypothesis
import pytest
from requests import PreparedRequest

import schemathesis
import schemathesis.cli.context
from schemathesis import runner
from schemathesis.cli.output import default
from schemathesis.cli.output.default import display_internal_error
from schemathesis.core import NOT_SET
from schemathesis.core.transport import Response
from schemathesis.runner import Status
from schemathesis.runner.events import EngineFinished, InternalError
from schemathesis.runner.models import Check, Request, TestResult, TestResultSet
from schemathesis.schemas import APIOperation, OperationDefinition


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
    return APIOperation(
        "/success",
        "GET",
        definition=OperationDefinition({}, {}, ""),
        base_url="http://127.0.0.1:8080",
        schema=swagger_20,
    )


@pytest.fixture
def response():
    content = b'{"id": 5}'
    request = PreparedRequest()
    request.prepare("GET", "http://127.0.0.1")
    return Response(
        status_code=201,
        content=content,
        message="Created",
        encoding="utf-8",
        http_version="1.1",
        request=request,
        elapsed=1.0,
        headers={"Content-Type": ["application/json"]},
        verify=True,
    )


@pytest.fixture
def results_set(operation):
    statistic = TestResult(label=f"{operation.method} {operation.full_path}")
    return TestResultSet(seed=42, results=[statistic])


@pytest.fixture
def after_execution(results_set):
    return runner.events.AfterExecution(
        result=results_set.results[0], status=Status.SUCCESS, elapsed_time=1.0, correlation_id="foo"
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


def test_display_multiple_warnings(capsys, execution_context):
    results = TestResultSet(seed=42, results=[])
    results.add_warning("Foo")
    results.add_warning("Bar")
    event = EngineFinished(results=results, running_time=1.0)
    # When test results are displayed
    default.display_statistic(execution_context, event)
    lines = [click.unstyle(line) for line in capsys.readouterr().out.split("\n") if line]
    assert lines[2:5] == [
        "WARNINGS:",
        "  - Foo",
        "  - Bar",
    ]


@pytest.mark.parametrize(
    ("position", "length", "expected"), [(1, 100, "[  1%]"), (20, 100, "[ 20%]"), (100, 100, "[100%]")]
)
def test_get_percentage(position, length, expected):
    assert default.get_percentage(position, length) == expected


@pytest.mark.parametrize("body", [{}, {"foo": "bar"}, NOT_SET])
def test_display_single_failure(capsys, execution_context, operation, body, response):
    # Given a single test result with multiple successful & failed checks
    media_type = "application/json" if body is not NOT_SET else None
    success = Check(
        "not_a_server_error",
        Status.SUCCESS,
        Request(method="POST", uri="http://user:pass@127.0.0.1/path", body=None, body_size=None, headers={}),
        response,
        operation.Case(body=body, media_type=media_type),
    )
    failure = Check(
        "not_a_server_error",
        Status.FAILURE,
        Request(method="POST", uri="http://user:pass@127.0.0.1/path", body=None, body_size=None, headers={}),
        response,
        operation.Case(body=body, media_type=media_type),
    )
    test_statistic = TestResult(
        label=f"{operation.method} {operation.full_path}",
        checks=[
            success,
            success,
            success,
            failure,
            failure,
            Check(
                "different_check",
                Status.SUCCESS,
                Request(method="POST", uri="http://user:pass@127.0.0.1/path", body=None, body_size=None, headers={}),
                response,
                operation.Case(body=body, media_type=media_type),
            ),
        ],
    )
    # When this failure is displayed
    default.display_failures_for_single_test(execution_context, test_statistic)
    out = capsys.readouterr().out
    lines = out.split("\n")
    # Then the path is displayed as a subsection
    assert " GET /v1/success " in lines[0]


def test_after_execution_attributes(execution_context, after_execution):
    # When `handle_after_execution` is executed
    default.on_after_execution(execution_context, after_execution)
    # Then number of operations processed grows by 1
    assert execution_context.operations_processed == 1
    # And the line length grows by 1 symbol
    assert execution_context.current_line_length == 1

    default.on_after_execution(execution_context, after_execution)
    assert execution_context.operations_processed == 2
    assert execution_context.current_line_length == 2


def test_display_internal_error(capsys, execution_context):
    try:
        raise ZeroDivisionError("division by zero")
    except ZeroDivisionError as exc:
        event = InternalError(exception=exc)
        display_internal_error(execution_context, event)
        out = capsys.readouterr().out.strip()
        assert "Traceback (most recent call last):" in out
        assert "ZeroDivisionError: division by zero" in out
