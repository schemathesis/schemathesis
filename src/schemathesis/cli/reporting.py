from __future__ import annotations

from itertools import groupby
from typing import Callable, Generator, Iterator

import click

from ..code_samples import CodeSampleStyle
from ..exceptions import RuntimeErrorType
from ..runner.serialization import SerializedCheck, deduplicate_failures

TEST_CASE_ID_TITLE = "Test Case ID"


def group_by_case(
    checks: list[SerializedCheck], code_sample_style: CodeSampleStyle
) -> Generator[tuple[str, Iterator[SerializedCheck]], None, None]:
    checks = deduplicate_failures(checks)
    checks = sorted(checks, key=lambda c: _by_unique_key(c, code_sample_style))
    for (sample, _, _), gen in groupby(checks, lambda c: _by_unique_key(c, code_sample_style)):
        yield (sample, gen)


def _by_unique_key(check: SerializedCheck, code_sample_style: CodeSampleStyle) -> tuple[str, int, str]:
    return (
        code_sample_style.generate(
            method=check.example.method,
            url=check.example.url,
            body=check.example.deserialize_body(),
            headers=check.example.headers,
            verify=check.example.verify,
            extra_headers=check.example.extra_headers,
        ),
        0 if not check.response else check.response.status_code,
        "SCHEMATHESIS-INTERNAL-NO-RESPONSE"
        if not check.response
        else check.response.body or "SCHEMATHESIS-INTERNAL-EMPTY-BODY",
    )


def split_traceback(traceback: str) -> list[str]:
    return [entry for entry in traceback.splitlines() if entry]


def bold(option: str) -> str:
    return click.style(option, bold=True)


def get_runtime_error_suggestion(error_type: RuntimeErrorType, bold: Callable[[str], str] = bold) -> str | None:
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
        RuntimeErrorType.HYPOTHESIS_HEALTH_CHECK_LARGE_BASE_EXAMPLE: _format_health_check_suggestion(
            "large_base_example"
        ),
    }
    return RUNTIME_ERROR_SUGGESTIONS.get(error_type)
