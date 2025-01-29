from __future__ import annotations

import platform
from dataclasses import dataclass, field
from typing import Iterable

from click.utils import LazyFile
from junit_xml import TestCase, TestSuite, to_xml_report_file

from schemathesis.cli.commands.run.context import ExecutionContext, GroupedFailures
from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.core.failures import format_failures
from schemathesis.engine import Status, events


@dataclass
class JunitXMLHandler(EventHandler):
    file_handle: LazyFile
    test_cases: dict = field(default_factory=dict)

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.ScenarioFinished):
            label = event.recorder.label
            test_case = self.get_or_create_test_case(label)
            test_case.elapsed_sec += event.elapsed_time
            if event.status == Status.FAILURE:
                add_failure(test_case, ctx.statistic.failures[label].values(), ctx)
            elif event.status == Status.SKIP and event.skip_reason is not None:
                test_case.add_skipped_info(output=event.skip_reason)
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


def add_failure(test_case: TestCase, checks: Iterable[GroupedFailures], context: ExecutionContext) -> None:
    messages = [
        format_failures(
            case_id=f"{idx}. Test Case ID: {group.case_id}",
            response=group.response,
            failures=group.failures,
            curl=group.code_sample,
            config=context.output_config,
        )
        for idx, group in enumerate(checks, 1)
    ]
    test_case.add_failure_info(message="\n\n".join(messages))
