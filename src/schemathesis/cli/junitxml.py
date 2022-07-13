import platform
from typing import List

import attr
from click.utils import LazyFile
from junit_xml import TestCase, TestSuite, to_xml_report_file

from ..models import Status
from ..runner import events
from ..runner.serialization import deduplicate_failures
from .handlers import EventHandler, ExecutionContext


@attr.s(slots=True)  # pragma: no mutate
class JunitXMLHandler(EventHandler):
    file_handle: LazyFile = attr.ib()  # pragma: no mutate
    test_cases: List = attr.ib(factory=list)  # pragma: no mutate

    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        if isinstance(event, events.AfterExecution):
            test_case = TestCase(
                f"{event.result.method} {event.result.path}",
                elapsed_sec=event.elapsed_time,
                allow_multiple_subelements=True,
            )
            if event.status == Status.failure:
                checks = deduplicate_failures(event.result.checks)
                for idx, check in enumerate(checks, 1):
                    # `check.message` is always not empty for events with `failure` status
                    test_case.add_failure_info(message=f"{idx}. {check.message}")
            if event.status == Status.error:
                test_case.add_error_info(
                    message=event.result.errors[-1].exception, output=event.result.errors[-1].exception_with_traceback
                )
            self.test_cases.append(test_case)
        if isinstance(event, events.Finished):
            test_suites = [TestSuite("schemathesis", test_cases=self.test_cases, hostname=platform.node())]
            to_xml_report_file(file_descriptor=self.file_handle, test_suites=test_suites, prettyprint=True)
