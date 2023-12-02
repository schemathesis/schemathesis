from __future__ import annotations
import base64
import os
import platform
import shutil
import textwrap
import time
from itertools import groupby
from queue import Queue
from typing import Any, Generator, cast

import click
from importlib import metadata

from ... import service
from ...code_samples import CodeSampleStyle
from ...constants import (
    DISCORD_LINK,
    FLAKY_FAILURE_MESSAGE,
    REPORT_SUGGESTION_ENV_VAR,
    SCHEMATHESIS_TEST_CASE_HEADER,
    SCHEMATHESIS_VERSION,
    FALSE_VALUES,
    ISSUE_TRACKER_URL,
    GITHUB_APP_LINK,
)
from ...exceptions import RuntimeErrorType, prepare_response_payload
from ...experimental import GLOBAL_EXPERIMENTS
from ...models import Status
from ...runner import events
from ...runner.events import InternalErrorType, SchemaErrorType
from ...runner.serialization import SerializedError, SerializedTestResult, deduplicate_failures, SerializedCheck
from ..context import ExecutionContext, FileReportContext, ServiceReportContext
from ..handlers import EventHandler

SPINNER_REPETITION_NUMBER = 10


def get_terminal_width() -> int:
    # Some CI/CD providers (e.g. CircleCI) return a (0, 0) terminal size so provide a default
    return shutil.get_terminal_size((80, 24)).columns


def display_section_name(title: str, separator: str = "=", **kwargs: Any) -> None:
    """Print section name with separators in terminal with the given title nicely centered."""
    message = f" {title} ".center(get_terminal_width(), separator)
    kwargs.setdefault("bold", True)
    click.secho(message, **kwargs)


def display_subsection(result: SerializedTestResult, color: str | None = "red") -> None:
    display_section_name(result.verbose_name, "_", fg=color)


def get_percentage(position: int, length: int) -> str:
    """Format completion percentage in square brackets."""
    percentage_message = f"{position * 100 // length}%".rjust(4)
    return f"[{percentage_message}]"


def display_execution_result(context: ExecutionContext, event: events.AfterExecution) -> None:
    """Display an appropriate symbol for the given event's execution result."""
    symbol, color = {
        Status.success: (".", "green"),
        Status.failure: ("F", "red"),
        Status.error: ("E", "red"),
        Status.skip: ("S", "yellow"),
    }[event.status]
    context.current_line_length += len(symbol)
    click.secho(symbol, nl=False, fg=color)


def display_percentage(context: ExecutionContext, event: events.AfterExecution) -> None:
    """Add the current progress in % to the right side of the current line."""
    operations_count = cast(int, context.operations_count)  # is already initialized via `Initialized` event
    current_percentage = get_percentage(context.operations_processed, operations_count)
    styled = click.style(current_percentage, fg="cyan")
    # Total length of the message, so it will fill to the right border of the terminal.
    # Padding is already taken into account in `context.current_line_length`
    length = max(get_terminal_width() - context.current_line_length + len(styled) - len(current_percentage), 1)
    template = f"{{:>{length}}}"
    click.echo(template.format(styled))


def display_summary(event: events.Finished) -> None:
    message, color = get_summary_output(event)
    display_section_name(message, fg=color)


def get_summary_message_parts(event: events.Finished) -> list[str]:
    parts = []
    passed = event.passed_count
    if passed:
        parts.append(f"{passed} passed")
    failed = event.failed_count
    if failed:
        parts.append(f"{failed} failed")
    errored = event.errored_count
    if errored:
        parts.append(f"{errored} errored")
    skipped = event.skipped_count
    if skipped:
        parts.append(f"{skipped} skipped")
    return parts


def get_summary_output(event: events.Finished) -> tuple[str, str]:
    parts = get_summary_message_parts(event)
    if not parts:
        message = "Empty test suite"
        color = "yellow"
    else:
        message = f'{", ".join(parts)} in {event.running_time:.2f}s'
        if event.has_failures or event.has_errors:
            color = "red"
        elif event.skipped_count > 0:
            color = "yellow"
        else:
            color = "green"
    return message, color


def display_hypothesis_output(hypothesis_output: list[str]) -> None:
    """Show falsifying examples from Hypothesis output if there are any."""
    if hypothesis_output:
        display_section_name("HYPOTHESIS OUTPUT")
        output = "\n".join(hypothesis_output)
        click.secho(output, fg="red")


def display_errors(context: ExecutionContext, event: events.Finished) -> None:
    """Display all errors in the test run."""
    if not event.has_errors:
        return

    display_section_name("ERRORS")
    should_display_full_traceback_message = False
    if context.workers_num > 1:
        # Events may come out of order when multiple workers are involved
        # Sort them to get a stable output
        results = sorted(context.results, key=lambda r: r.verbose_name)
    else:
        results = context.results
    for result in results:
        if not result.has_errors:
            continue
        should_display_full_traceback_message |= display_single_error(context, result)
    if event.generic_errors:
        display_generic_errors(context, event.generic_errors)
    if should_display_full_traceback_message and not context.show_trace:
        click.secho(
            "\nAdd this option to your command line parameters to see full tracebacks: --show-trace",
            fg="red",
        )
    click.secho(
        f"\nNeed more help?\n" f"    Join our Discord server: {DISCORD_LINK}",
        fg="red",
    )


def display_single_error(context: ExecutionContext, result: SerializedTestResult) -> bool:
    display_subsection(result)
    should_display_full_traceback_message = False
    for error in result.errors:
        should_display_full_traceback_message |= _display_error(context, error)
    return should_display_full_traceback_message


def display_generic_errors(context: ExecutionContext, errors: list[SerializedError]) -> None:
    for error in errors:
        display_section_name(error.title or "Generic error", "_", fg="red")
        _display_error(context, error)


def display_full_traceback_message(error: SerializedError) -> bool:
    # Some errors should not trigger the message that suggests to show full tracebacks to the user
    return not error.exception.startswith(
        (
            "DeadlineExceeded",
            "OperationSchemaError",
            "requests.exceptions",
            "SerializationNotPossible",
            "hypothesis.errors.FailedHealthCheck",
            "hypothesis.errors.InvalidArgument: Scalar ",
        )
    )


def bold(option: str) -> str:
    return click.style(option, bold=True)


DISABLE_SSL_SUGGESTION = f"Bypass SSL verification with {bold('`--request-tls-verify=false`')}."
DISABLE_SCHEMA_VALIDATION_SUGGESTION = (
    f"Bypass validation using {bold('`--validate-schema=false`')}. Caution: May cause unexpected errors."
)


def _format_health_check_suggestion(label: str) -> str:
    return f"Bypass this health check using {bold(f'`--hypothesis-suppress-health-check={label}`')}."


RUNTIME_ERROR_SUGGESTIONS = {
    RuntimeErrorType.CONNECTION_SSL: DISABLE_SSL_SUGGESTION,
    RuntimeErrorType.HYPOTHESIS_DEADLINE_EXCEEDED: (
        f"Adjust the deadline using {bold('`--hypothesis-deadline=MILLIS`')} or "
        f"disable with {bold('`--hypothesis-deadline=None`')}."
    ),
    RuntimeErrorType.HYPOTHESIS_UNSATISFIABLE: "Examine the schema for inconsistencies and consider simplifying it.",
    RuntimeErrorType.SCHEMA_BODY_IN_GET_REQUEST: DISABLE_SCHEMA_VALIDATION_SUGGESTION,
    RuntimeErrorType.SCHEMA_INVALID_REGULAR_EXPRESSION: "Ensure your regex is compatible with Python's syntax.\n"
    "For guidance, visit: https://docs.python.org/3/library/re.html",
    RuntimeErrorType.HYPOTHESIS_UNSUPPORTED_GRAPHQL_SCALAR: "Define a custom strategy for it.\n"
    "For guidance, visit: https://schemathesis.readthedocs.io/en/stable/graphql.html#custom-scalars",
    RuntimeErrorType.HYPOTHESIS_HEALTH_CHECK_DATA_TOO_LARGE: _format_health_check_suggestion("data_too_large"),
    RuntimeErrorType.HYPOTHESIS_HEALTH_CHECK_FILTER_TOO_MUCH: _format_health_check_suggestion("filter_too_much"),
    RuntimeErrorType.HYPOTHESIS_HEALTH_CHECK_TOO_SLOW: _format_health_check_suggestion("too_slow"),
    RuntimeErrorType.HYPOTHESIS_HEALTH_CHECK_LARGE_BASE_EXAMPLE: _format_health_check_suggestion("large_base_example"),
}


def _display_error(context: ExecutionContext, error: SerializedError) -> bool:
    if error.title:
        if error.type == RuntimeErrorType.SCHEMA_GENERIC:
            click.secho("Schema Error", fg="red", bold=True)
        else:
            click.secho(error.title, fg="red", bold=True)
        click.echo()
        if error.message:
            click.echo(error.message)
    elif error.message:
        click.echo(error.message)
    else:
        click.echo(error.exception)
    if error.extras:
        extras = error.extras
    elif context.show_trace:
        extras = _split_traceback(error.exception_with_traceback)
    else:
        extras = []
    _display_extras(extras)
    suggestion = RUNTIME_ERROR_SUGGESTIONS.get(error.type)
    _maybe_display_tip(suggestion)
    return display_full_traceback_message(error)


def display_failures(context: ExecutionContext, event: events.Finished) -> None:
    """Display all failures in the test run."""
    if not event.has_failures:
        return
    relevant_results = [result for result in context.results if not result.is_errored]
    if not relevant_results:
        return
    display_section_name("FAILURES")
    for result in relevant_results:
        if not result.has_failures:
            continue
        display_failures_for_single_test(context, result)


TEST_CASE_ID_TITLE = "Test Case ID"


def display_failures_for_single_test(context: ExecutionContext, result: SerializedTestResult) -> None:
    """Display a failure for a single method / path."""
    from ...transports.responses import get_reason

    display_subsection(result)
    if result.is_flaky:
        click.secho(FLAKY_FAILURE_MESSAGE, fg="red")
        click.echo()
    for idx, (code_sample, group) in enumerate(group_by_case(result.checks, context.code_sample_style), 1):
        # Make server errors appear first in the list of checks
        checks = sorted(group, key=lambda c: c.name != "not_a_server_error")

        for check_idx, check in enumerate(checks):
            if check_idx == 0:
                click.secho(f"{idx}. {TEST_CASE_ID_TITLE}: {check.example.id}", bold=True)
            if check.context is not None:
                title = check.context.title
                if check.context.message:
                    message = check.context.message
                else:
                    message = None
            else:
                title = f"Custom check failed: `{check.name}`"
                message = check.message
            click.secho(f"\n- {title}", fg="red", bold=True)
            if message:
                message = textwrap.indent(message, prefix="    ")
                click.secho(f"\n{message}", fg="red")
            if check_idx + 1 == len(checks):
                if check.response is not None:
                    status_code = check.response.status_code
                    reason = get_reason(status_code)
                    response = bold(f"[{check.response.status_code}] {reason}")
                    click.echo(f"\n{response}:")

                    response_body = check.response.body
                    if check.response is not None and response_body is not None:
                        if not response_body:
                            click.echo("\n    <EMPTY>")
                        else:
                            encoding = check.response.encoding or "utf8"
                            try:
                                payload = base64.b64decode(response_body).decode(encoding)
                                payload = prepare_response_payload(payload)
                                payload = textwrap.indent(f"\n`{payload}`", prefix="    ")
                                click.echo(payload)
                            except UnicodeDecodeError:
                                click.echo("\n    <BINARY>")

        click.echo(
            f"\n{bold('Reproduce with')}: \n\n    {code_sample}\n",
        )


def group_by_case(
    checks: list[SerializedCheck], code_sample_style: CodeSampleStyle
) -> Generator[tuple[str, Generator[SerializedCheck, None, None]], None, None]:
    checks = deduplicate_failures(checks)
    checks = sorted(checks, key=lambda c: _by_unique_code_sample(c, code_sample_style))
    yield from groupby(checks, lambda c: _by_unique_code_sample(c, code_sample_style))


def _by_unique_code_sample(check: SerializedCheck, code_sample_style: CodeSampleStyle) -> str:
    request_body = base64.b64decode(check.example.body).decode() if check.example.body is not None else None
    return code_sample_style.generate(
        method=check.example.method,
        url=check.example.url,
        body=request_body,
        headers=check.example.headers,
        verify=check.example.verify,
        extra_headers=check.example.extra_headers,
    )


def display_application_logs(context: ExecutionContext, event: events.Finished) -> None:
    """Print logs captured during the application run."""
    if not event.has_logs:
        return
    display_section_name("APPLICATION LOGS")
    for result in context.results:
        if not result.has_logs:
            continue
        display_single_log(result)


def display_single_log(result: SerializedTestResult) -> None:
    display_subsection(result, None)
    click.echo("\n\n".join(result.logs))


def display_statistic(context: ExecutionContext, event: events.Finished) -> None:
    """Format and print statistic collected by :obj:`models.TestResult`."""
    display_section_name("SUMMARY")
    click.echo()
    total = event.total
    if event.is_empty or not total:
        click.secho("No checks were performed.", bold=True)

    if total:
        display_checks_statistics(total)

    if context.cassette_path:
        click.echo()
        category = click.style("Network log", bold=True)
        click.secho(f"{category}: {context.cassette_path}")

    if context.junit_xml_file:
        click.echo()
        category = click.style("JUnit XML file", bold=True)
        click.secho(f"{category}: {context.junit_xml_file}")

    if event.warnings:
        click.echo()
        if len(event.warnings) == 1:
            title = click.style("WARNING:", bold=True, fg="yellow")
            warning = click.style(event.warnings[0], fg="yellow")
            click.secho(f"{title} {warning}")
        else:
            click.secho("WARNINGS:", bold=True, fg="yellow")
            for warning in event.warnings:
                click.secho(f"  - {warning}", fg="yellow")

    if len(GLOBAL_EXPERIMENTS.enabled) > 0:
        click.secho("\nExperimental Features:", bold=True)
        for experiment in sorted(GLOBAL_EXPERIMENTS.enabled, key=lambda e: e.name):
            click.secho(f"  - {experiment.verbose_name}: {experiment.description}")
            click.secho(f"    Feedback: {experiment.discussion_url}")
        click.echo()
        click.echo(
            "Your feedback is crucial for experimental features. "
            "Please visit the provided URL(s) to share your thoughts."
        )

    if event.failed_count > 0:
        click.echo(
            f"\n{bold('Note')}: Use the '{SCHEMATHESIS_TEST_CASE_HEADER}' header to correlate test case ids "
            "from failure messages with server logs for debugging."
        )
        if context.seed is not None:
            seed_option = f"`--hypothesis-seed={context.seed}`"
            click.secho(f"\n{bold('Note')}: To replicate these test failures, rerun with {bold(seed_option)}")

    if context.report is not None and not context.is_interrupted:
        if isinstance(context.report, FileReportContext):
            click.echo()
            display_report_metadata(context.report.queue.get())
            click.secho(f"Report is saved to {context.report.filename}", bold=True)
        elif isinstance(context.report, ServiceReportContext):
            click.echo()
            handle_service_integration(context.report)
    else:
        env_var = os.getenv(REPORT_SUGGESTION_ENV_VAR)
        if env_var is not None and env_var.lower() in FALSE_VALUES:
            return
        click.echo(
            f"\n{bold('Tip')}: Use the {bold('`--report`')} CLI option to visualize test results via Schemathesis.io.\n"
            "We run additional conformance checks on reports from public repos."
        )
        if service.ci.detect() == service.ci.CIProvider.GITHUB:
            click.echo(
                "Optionally, for reporting results as PR comments, install the Schemathesis GitHub App:\n\n"
                f"    {GITHUB_APP_LINK}"
            )


def handle_service_integration(context: ServiceReportContext) -> None:
    """If Schemathesis.io integration is enabled, wait for the handler & print the resulting status."""
    event = context.queue.get()
    title = click.style("Upload", bold=True)
    if isinstance(event, service.Metadata):
        display_report_metadata(event)
        click.secho(f"Uploading reports to {context.service_base_url} ...", bold=True)
        event = wait_for_report_handler(context.queue, title)
    color = {
        service.Completed: "green",
        service.Error: "red",
        service.Failed: "red",
        service.Timeout: "red",
    }[event.__class__]
    status = click.style(event.status, fg=color, bold=True)
    click.echo(f"{title}: {status}\r", nl=False)
    click.echo()
    if isinstance(event, service.Error):
        click.echo()
        display_service_error(event)
    if isinstance(event, service.Failed):
        click.echo()
        click.echo(event.detail)
    if isinstance(event, service.Completed):
        click.echo()
        click.echo(event.message)
        click.echo()
        click.echo(event.next_url)


def display_report_metadata(meta: service.Metadata) -> None:
    if meta.ci_environment is not None:
        click.secho(f"{meta.ci_environment.verbose_name} detected:", bold=True)
        for key, value in meta.ci_environment.as_env().items():
            if value is not None:
                click.secho(f"  -> {key}: {value}")
        click.echo()
    click.secho(f"Compressed report size: {meta.size / 1024.:,.0f} KB", bold=True)


def display_service_error(event: service.Error) -> None:
    """Show information about an error during communication with Schemathesis.io."""
    from requests import RequestException, HTTPError, Response

    if isinstance(event.exception, HTTPError):
        response = cast(Response, event.exception.response)
        status_code = response.status_code
        click.secho(f"Schemathesis.io responded with HTTP {status_code}", fg="red")
        if 500 <= status_code <= 599:
            # Server error, should be resolved soon
            click.secho(
                "It is likely that we are already notified about the issue and working on a fix\n"
                "Please, try again in 30 minutes",
                fg="red",
            )
        elif status_code == 401:
            # Likely an invalid token
            click.secho(
                "Please, check that you use the proper CLI access token\n"
                "See https://schemathesis.readthedocs.io/en/stable/service.html for more details",
                fg="red",
            )
        else:
            # Other client-side errors are likely caused by a bug on the CLI side
            ask_to_report(event)
    elif isinstance(event.exception, RequestException):
        ask_to_report(event, report_to_issues=False)
    else:
        ask_to_report(event)


SERVICE_ERROR_MESSAGE = "An error happened during uploading reports to Schemathesis.io"


def ask_to_report(event: service.Error, report_to_issues: bool = True, extra: str = "") -> None:
    from requests import RequestException

    # Likely an internal Schemathesis error
    traceback = event.get_message(True)
    if isinstance(event.exception, RequestException) and event.exception.response is not None:
        response = f"Response: {event.exception.response.text}\n"
    else:
        response = ""
    if report_to_issues:
        ask = f"Please, consider reporting the traceback below it to our issue tracker:\n\n  {ISSUE_TRACKER_URL}\n"
    else:
        ask = ""
    click.secho(
        f"{SERVICE_ERROR_MESSAGE}:\n{extra}{ask}{response}\n{traceback.strip()}",
        fg="red",
    )


def wait_for_report_handler(queue: Queue, title: str, timeout: float = service.WORKER_FINISH_TIMEOUT) -> service.Event:
    """Wait for the Schemathesis.io handler to finish its job."""
    start = time.monotonic()
    spinner = create_spinner(SPINNER_REPETITION_NUMBER)
    # The testing process is done, and we need to wait for the Schemathesis.io handler to finish
    # It might still have some data to send
    while queue.empty():
        if time.monotonic() - start >= timeout:
            return service.Timeout()
        click.echo(f"{title}: {next(spinner)}\r", nl=False)
        time.sleep(service.WORKER_CHECK_PERIOD)
    return queue.get()


def create_spinner(repetitions: int) -> Generator[str, None, None]:
    """A simple spinner that yields its individual characters."""
    assert repetitions > 0, "The number of repetitions should be greater than zero"
    while True:
        for ch in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏":
            # Skip branch coverage, as it is not possible because of the assertion above
            for _ in range(repetitions):  # pragma: no branch
                yield ch


def display_checks_statistics(total: dict[str, dict[str | Status, int]]) -> None:
    padding = 20
    col1_len = max(map(len, total.keys())) + padding
    col2_len = len(str(max(total.values(), key=lambda v: v["total"])["total"])) * 2 + padding
    col3_len = padding
    click.secho("Performed checks:", bold=True)
    template = f"    {{:{col1_len}}}{{:{col2_len}}}{{:{col3_len}}}"
    for check_name, results in total.items():
        display_check_result(check_name, results, template)


def display_check_result(check_name: str, results: dict[str | Status, int], template: str) -> None:
    """Show results of single check execution."""
    if Status.failure in results:
        verdict = "FAILED"
        color = "red"
    else:
        verdict = "PASSED"
        color = "green"
    success = results.get(Status.success, 0)
    total = results.get("total", 0)
    click.echo(template.format(check_name, f"{success} / {total} passed", click.style(verdict, fg=color, bold=True)))


VERIFY_URL_SUGGESTION = "Verify that the URL points directly to the Open API schema"


SCHEMA_ERROR_SUGGESTIONS = {
    # SSL-specific connection issue
    SchemaErrorType.CONNECTION_SSL: DISABLE_SSL_SUGGESTION,
    # Other connection problems
    SchemaErrorType.CONNECTION_OTHER: f"Use {bold('`--wait-for-schema=NUM`')} to wait up to NUM seconds for schema availability.",
    # Response issues
    SchemaErrorType.UNEXPECTED_CONTENT_TYPE: VERIFY_URL_SUGGESTION,
    SchemaErrorType.HTTP_FORBIDDEN: "Verify your API keys or authentication headers.",
    SchemaErrorType.HTTP_NOT_FOUND: VERIFY_URL_SUGGESTION,
    # OpenAPI specification issues
    SchemaErrorType.OPEN_API_UNSPECIFIED_VERSION: f"Include the version in the schema or manually set it with {bold('`--force-schema-version`')}.",
    SchemaErrorType.OPEN_API_UNSUPPORTED_VERSION: f"Proceed with {bold('`--force-schema-version`')}. Caution: May not be fully supported.",
    SchemaErrorType.OPEN_API_INVALID_SCHEMA: DISABLE_SCHEMA_VALIDATION_SUGGESTION,
    # YAML specific issues
    SchemaErrorType.YAML_NUMERIC_STATUS_CODES: "Convert numeric status codes to strings.",
    SchemaErrorType.YAML_NON_STRING_KEYS: "Convert non-string keys to strings.",
    # Unclassified
    SchemaErrorType.UNCLASSIFIED: f"If you suspect this is a Schemathesis issue and the schema is valid, please report it and include the schema if you can:\n\n  {ISSUE_TRACKER_URL}",
}


def should_skip_suggestion(context: ExecutionContext, event: events.InternalError) -> bool:
    return event.subtype == SchemaErrorType.CONNECTION_OTHER and context.wait_for_schema is not None


def _split_traceback(traceback: str) -> list[str]:
    return [entry for entry in traceback.splitlines() if entry]


def _display_extras(extras: list[str]) -> None:
    if extras:
        click.echo()
    for extra in extras:
        click.secho(f"    {extra}")


def _maybe_display_tip(suggestion: str | None) -> None:
    # Display suggestion if any
    if suggestion is not None:
        click.secho(f"\n{click.style('Tip:', bold=True, fg='green')} {suggestion}")


def display_internal_error(context: ExecutionContext, event: events.InternalError) -> None:
    click.secho(event.title, fg="red", bold=True)
    click.echo()
    click.secho(event.message)
    if event.type == InternalErrorType.SCHEMA:
        extras = event.extras
    elif context.show_trace:
        extras = _split_traceback(event.exception_with_traceback)
    else:
        extras = [event.exception]
    _display_extras(extras)
    if not should_skip_suggestion(context, event):
        if event.type == InternalErrorType.SCHEMA and isinstance(event.subtype, SchemaErrorType):
            suggestion = SCHEMA_ERROR_SUGGESTIONS.get(event.subtype)
        elif context.show_trace:
            suggestion = (
                f"Please consider reporting the traceback above to our issue tracker:\n\n  {ISSUE_TRACKER_URL}."
            )
        else:
            suggestion = f"To see full tracebacks, add {bold('`--show-trace`')} to your CLI options"
        _maybe_display_tip(suggestion)


def handle_initialized(context: ExecutionContext, event: events.Initialized) -> None:
    """Display information about the test session."""
    context.operations_count = cast(int, event.operations_count)  # INVARIANT: should not be `None`
    context.seed = event.seed
    display_section_name("Schemathesis test session starts")
    if context.verbosity > 0:
        versions = (
            f"platform {platform.system()} -- "
            f"Python {platform.python_version()}, "
            f"schemathesis-{SCHEMATHESIS_VERSION}, "
            f"hypothesis-{metadata.version('hypothesis')}, "
            f"hypothesis_jsonschema-{metadata.version('hypothesis_jsonschema')}, "
            f"jsonschema-{metadata.version('jsonschema')}"
        )
        click.echo(versions)
        click.echo(f"rootdir: {os.getcwd()}")
        click.echo(f"Hypothesis: {context.hypothesis_settings.show_changed()}")
    if event.location is not None:
        click.secho(f"Schema location: {event.location}", bold=True)
    click.secho(f"Base URL: {event.base_url}", bold=True)
    click.secho(f"Specification version: {event.specification_name}", bold=True)
    if context.seed is not None:
        click.secho(f"Random seed: {context.seed}", bold=True)
    click.secho(f"Workers: {context.workers_num}", bold=True)
    if context.rate_limit is not None:
        click.secho(f"Rate limit: {context.rate_limit}", bold=True)
    click.secho(f"Collected API operations: {context.operations_count}", bold=True)
    links_count = cast(int, event.links_count)
    click.secho(f"Collected API links: {links_count}", bold=True)
    if isinstance(context.report, ServiceReportContext):
        click.secho("Report to Schemathesis.io: ENABLED", bold=True)
    if context.operations_count >= 1:
        click.echo()


TRUNCATION_PLACEHOLDER = "[...]"


def handle_before_execution(context: ExecutionContext, event: events.BeforeExecution) -> None:
    """Display what method / path will be tested next."""
    # We should display execution result + percentage in the end. For example:
    max_length = get_terminal_width() - len(" . [XXX%]") - len(TRUNCATION_PLACEHOLDER)
    message = event.verbose_name
    if event.recursion_level > 0:
        message = f"{'    ' * event.recursion_level}-> {message}"
        # This value is not `None` - the value is set in runtime before this line
        context.operations_count += 1  # type: ignore

    message = message[:max_length] + (message[max_length:] and "[...]") + " "
    context.current_line_length = len(message)
    click.echo(message, nl=False)


def handle_after_execution(context: ExecutionContext, event: events.AfterExecution) -> None:
    """Display the execution result + current progress at the same line with the method / path names."""
    context.operations_processed += 1
    context.results.append(event.result)
    display_execution_result(context, event)
    display_percentage(context, event)


def handle_finished(context: ExecutionContext, event: events.Finished) -> None:
    """Show the outcome of the whole testing session."""
    click.echo()
    display_hypothesis_output(context.hypothesis_output)
    display_errors(context, event)
    display_failures(context, event)
    display_application_logs(context, event)
    display_statistic(context, event)
    click.echo()
    display_summary(event)


def handle_interrupted(context: ExecutionContext, event: events.Interrupted) -> None:
    click.echo()
    context.is_interrupted = True
    display_section_name("KeyboardInterrupt", "!", bold=False)


def handle_internal_error(context: ExecutionContext, event: events.InternalError) -> None:
    display_internal_error(context, event)
    raise click.Abort


class DefaultOutputStyleHandler(EventHandler):
    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        """Choose and execute a proper handler for the given event."""
        if isinstance(event, events.Initialized):
            handle_initialized(context, event)
        if isinstance(event, events.BeforeExecution):
            handle_before_execution(context, event)
        if isinstance(event, events.AfterExecution):
            context.hypothesis_output.extend(event.hypothesis_output)
            handle_after_execution(context, event)
        if isinstance(event, events.Finished):
            handle_finished(context, event)
        if isinstance(event, events.Interrupted):
            handle_interrupted(context, event)
        if isinstance(event, events.InternalError):
            handle_internal_error(context, event)
