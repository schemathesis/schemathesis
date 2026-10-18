from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.failures import group_failures, reproducible_by_identity
from schemathesis.core.output import format_response_payload
from schemathesis.reporting.html.model import Command, FailingCase, FailureEntry, split_label

if TYPE_CHECKING:
    from schemathesis.config import ChecksConfig, OutputConfig
    from schemathesis.engine.statistic import GroupedFailures


def failing_case(group: GroupedFailures, *, config: OutputConfig, replay: str | None) -> FailingCase:
    response = group.response
    return FailingCase(
        case_id=group.case_id,
        failures=[
            FailureEntry(
                code=failures[0].code,
                title=title,
                severity=failures[0].severity,
                messages=[failure.message for failure in failures],
            )
            for title, failures in group_failures(group.failures)
        ],
        status_code=response.status_code if response is not None else None,
        body=format_response_payload(response, config=config) if response is not None else None,
        commands=_commands(group),
        replay=replay,
        auth_identity=group.auth_identity if reproducible_by_identity(group.failures) else None,
    )


def failure_titles(cases: list[FailingCase]) -> list[str]:
    """Distinct failure titles, most severe first."""
    severities = {failure.title: failure.severity for case in cases for failure in case.failures}
    return sorted(severities, key=lambda title: (severities[title], title))


def _commands(group: GroupedFailures) -> list[Command]:
    if group.steps:
        commands = []
        for step in group.steps:
            method, path = request_name(step.operation, step.method, step.path)
            commands.append(Command(curl=step.curl, method=method, path=path, failed=step.case_id == group.case_id))
        return commands
    if group.code_sample is None:
        return []
    return [Command(curl=group.code_sample, method=None, path=None, failed=False)]


def request_name(operation: str, method: str, path: str) -> tuple[str | None, str]:
    # Without a method in the label (GraphQL), every request goes to the same URL, so the step names the field.
    if split_label(operation)[0] is None:
        return None, operation
    return method, path


def enabled_checks(config: ChecksConfig) -> list[str]:
    from schemathesis.checks import CHECKS, max_response_time

    names = [check.__name__ for check in CHECKS.get_all() if config.get_by_name(name=check.__name__).enabled]
    if config.max_response_time.enabled:
        names.append(max_response_time.__name__)
    return sorted(names)
