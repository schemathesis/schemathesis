from __future__ import annotations

import base64
import platform
import textwrap
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from junit_xml import TestCase, TestSuite, to_xml_report_file

from ..models import Status
from ..runner import events
from ..runner.serialization import SerializedCheck, SerializedError
from ..exceptions import prepare_response_payload, RuntimeErrorType
from .handlers import EventHandler
from .reporting import group_by_case, TEST_CASE_ID_TITLE, split_traceback, get_runtime_error_suggestion

if TYPE_CHECKING:
    from click.utils import LazyFile

    from .context import ExecutionContext


@dataclass
class JunitXMLHandler(EventHandler):
    file_handle: LazyFile
    test_cases: list = field(default_factory=list)

    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        if isinstance(event, events.AfterExecution):
            test_case = TestCase(
                f"{event.result.method} {event.result.path}",
                elapsed_sec=event.elapsed_time,
                allow_multiple_subelements=True,
            )
            if event.status == Status.failure:
                for idx, (code_sample, group) in enumerate(
                    group_by_case(event.result.checks, context.code_sample_style), 1
                ):
                    checks = sorted(group, key=lambda c: c.name != "not_a_server_error")
                    test_case.add_failure_info(message=build_failure_message(idx, code_sample, checks))
            elif event.status == Status.error:
                test_case.add_error_info(message=build_error_message(context, event.result.errors[-1]))
            elif event.status == Status.skip:
                test_case.add_skipped_info(message=event.result.skip_reason)
            self.test_cases.append(test_case)
        if isinstance(event, events.Finished):
            test_suites = [TestSuite("schemathesis", test_cases=self.test_cases, hostname=platform.node())]
            to_xml_report_file(file_descriptor=self.file_handle, test_suites=test_suites, prettyprint=True)


def build_failure_message(idx: int, code_sample: str, checks: list[SerializedCheck]) -> str:
    from ..transports.responses import get_reason

    message = ""
    for check_idx, check in enumerate(checks):
        if check_idx == 0:
            message += f"{idx}. {TEST_CASE_ID_TITLE}: {check.example.id}\n"
        message += f"\n- {check.title}\n"
        formatted_message = check.formatted_message
        if formatted_message:
            message += f"\n{formatted_message}\n"
        if check_idx + 1 == len(checks):
            if check.response is not None:
                status_code = check.response.status_code
                reason = get_reason(status_code)
                message += f"\n[{check.response.status_code}] {reason}:\n"
                response_body = check.response.body
                if response_body is not None:
                    if not response_body:
                        message += "\n    <EMPTY>\n"
                    else:
                        encoding = check.response.encoding or "utf8"
                        try:
                            payload = base64.b64decode(response_body).decode(encoding)
                            payload = prepare_response_payload(payload)
                            payload = textwrap.indent(f"\n`{payload}`\n", prefix="    ")
                            message += payload
                        except UnicodeDecodeError:
                            message += "\n    <BINARY>\n"

    message += f"\nReproduce with: \n\n    {code_sample}"
    return message


def build_error_message(context: ExecutionContext, error: SerializedError) -> str:
    message = ""
    if error.title:
        if error.type == RuntimeErrorType.SCHEMA_GENERIC:
            message = "Schema Error\n"
        else:
            message = f"{error.title}\n"
        if error.message:
            message += f"\n{error.message}\n"
    elif error.message:
        message = error.message
    else:
        message = error.exception
    if error.extras:
        extras = error.extras
    elif context.show_trace and error.type.has_useful_traceback:
        extras = split_traceback(error.exception_with_traceback)
    else:
        extras = []
    if extras:
        message += "\n"
    for extra in extras:
        message += f"    {extra}\n"
    suggestion = get_runtime_error_suggestion(error.type, bold=str)
    if suggestion is not None:
        message += f"\nTip: {suggestion}"
    return message
