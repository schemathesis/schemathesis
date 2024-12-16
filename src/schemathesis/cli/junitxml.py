from __future__ import annotations

import platform
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from junit_xml import TestCase, TestSuite, to_xml_report_file

from schemathesis.core.failures import format_failures
from schemathesis.runner.models import group_failures_by_code_sample

from ..runner import events
from ..runner.models import Check, Status
from .handlers import EventHandler

if TYPE_CHECKING:
    from click.utils import LazyFile

    from .context import ExecutionContext


@dataclass
class JunitXMLHandler(EventHandler):
    file_handle: LazyFile
    test_cases: list = field(default_factory=list)

    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        if isinstance(event, (events.AfterExecution, events.AfterStatefulExecution)):
            event_: events.AfterExecution | events.AfterStatefulExecution = event
            name = event_.result.verbose_name
            test_case = TestCase(name, elapsed_sec=event_.elapsed_time, allow_multiple_subelements=True)
            if event_.status == Status.FAILURE:
                _add_failure(test_case, event_.result.checks, context)
            elif event_.status == Status.ERROR:
                test_case.add_error_info(message=event_.result.errors[-1].format())
            elif event_.status == Status.SKIP:
                test_case.add_skipped_info(message=event_.result.skip_reason)
            self.test_cases.append(test_case)
        elif isinstance(event, events.Finished):
            test_suites = [TestSuite("schemathesis", test_cases=self.test_cases, hostname=platform.node())]
            to_xml_report_file(file_descriptor=self.file_handle, test_suites=test_suites, prettyprint=True)


def _add_failure(test_case: TestCase, checks: list[Check], context: ExecutionContext) -> None:
    for idx, (code, group) in enumerate(group_failures_by_code_sample(checks), 1):
        checks = sorted(group, key=lambda c: c.name != "not_a_server_error")
        test_case.add_failure_info(message=build_failure_message(context, idx, code, checks))


def build_failure_message(context: ExecutionContext, idx: int, code_sample: str, checks: list[Check]) -> str:
    check = checks[0]
    return format_failures(
        case_id=f"{idx}. Test Case ID: {check.case.id}",
        response=check.response,
        failures=[check.failure for check in checks if check.failure is not None],
        curl=code_sample,
        config=context.output_config,
    )
