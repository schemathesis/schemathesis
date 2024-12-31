from __future__ import annotations

import platform
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from junit_xml import TestCase, TestSuite, to_xml_report_file

from schemathesis.core.failures import format_failures
from schemathesis.runner import Status
from schemathesis.runner.models import Check, group_failures_by_code_sample
from schemathesis.runner.phases import PhaseName
from schemathesis.runner.phases.stateful import StatefulTestingPayload

from ..runner import events
from .handlers import EventHandler

if TYPE_CHECKING:
    from click.utils import LazyFile

    from .context import ExecutionContext


@dataclass
class JunitXMLHandler(EventHandler):
    file_handle: LazyFile
    test_cases: dict = field(default_factory=dict)

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        # TODO: store cases on each step
        if isinstance(event, (events.AfterExecution, events.PhaseFinished)):
            if isinstance(event, events.PhaseFinished):
                if event.phase.name == PhaseName.STATEFUL_TESTING and event.status != Status.SKIP:
                    # TODO: Skipped event - looks inconsistent with AfterExecution
                    assert isinstance(event.payload, StatefulTestingPayload)
                    result = event.payload.result
                    elapsed_time = event.payload.elapsed_time
                    skip_reason = None
                else:
                    return
            else:
                result = event.result
                elapsed_time = event.elapsed_time
                skip_reason = event.skip_reason
            test_case = self.get_or_create_test_case(result.label)
            test_case.elapsed_sec += elapsed_time
            if event.status == Status.FAILURE:
                _add_failure(test_case, result.checks, ctx)
            elif event.status == Status.SKIP:
                test_case.add_skipped_info(output=skip_reason)
        elif isinstance(event, events.NonFatalError):
            test_case = self.get_or_create_test_case(event.label)
            test_case.add_error_info(output=event.info.format())
        elif isinstance(event, events.EngineFinished):
            test_suites = [
                TestSuite("schemathesis", test_cases=list(self.test_cases.values()), hostname=platform.node())
            ]
            to_xml_report_file(file_descriptor=self.file_handle, test_suites=test_suites, prettyprint=True)

    def get_or_create_test_case(self, label: str) -> TestCase:
        return self.test_cases.setdefault(label, TestCase(label, elapsed_sec=0.0, allow_multiple_subelements=True))


def _add_failure(test_case: TestCase, checks: list[Check], context: ExecutionContext) -> None:
    messages = []
    for idx, (code, group) in enumerate(group_failures_by_code_sample(checks), 1):
        checks = sorted(group, key=lambda c: c.name != "not_a_server_error")
        messages.append(build_failure_message(context, idx, code, checks))
    test_case.add_failure_info(message="\n\n".join(messages))


def build_failure_message(context: ExecutionContext, idx: int, code_sample: str, checks: list[Check]) -> str:
    check = checks[0]
    return format_failures(
        case_id=f"{idx}. Test Case ID: {check.case.id}",
        response=check.response,
        failures=[check.failure for check in checks if check.failure is not None],
        curl=code_sample,
        config=context.output_config,
    )
