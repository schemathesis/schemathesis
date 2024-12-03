from __future__ import annotations

import http.client
import platform
import textwrap
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from junit_xml import TestCase, TestSuite, to_xml_report_file

from schemathesis.core.output import prepare_response_payload
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
            if event_.status == Status.failure:
                _add_failure(test_case, event_.result.checks, context)
            elif event_.status == Status.error:
                test_case.add_error_info(message=event_.result.errors[-1].format())
            elif event_.status == Status.skip:
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
    message = ""
    for check_idx, check in enumerate(checks):
        if check_idx == 0:
            message += f"{idx}. Test Case ID: {check.case.id}\n"
        assert check.failure is not None
        message += f"\n- {check.failure.title}\n"
        formatted_message = textwrap.indent(check.failure.message, prefix="    ")
        if formatted_message:
            message += f"\n{formatted_message}\n"
        if check_idx + 1 == len(checks):
            status_code = check.response.status_code
            reason = http.client.responses.get(status_code, "Unknown")
            message += f"\n[{check.response.status_code}] {reason}:\n"
            if check.response.body is not None:
                if not check.response.body:
                    message += "\n    <EMPTY>\n"
                else:
                    encoding = check.response.encoding or "utf8"
                    try:
                        # Checked that is not None
                        body = cast(bytes, check.response.body)
                        payload = body.decode(encoding)
                        payload = prepare_response_payload(payload, config=context.output_config)
                        payload = textwrap.indent(f"\n`{payload}`\n", prefix="    ")
                        message += payload
                    except UnicodeDecodeError:
                        message += "\n    <BINARY>\n"

    message += f"\nReproduce with: \n\n    {code_sample}"
    return message
