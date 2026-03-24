from __future__ import annotations

import platform
from collections.abc import Iterable
from io import StringIO
from pathlib import Path
from types import TracebackType
from typing import IO, TYPE_CHECKING

from junit_xml import TestCase, TestSuite, to_xml_report_file

from schemathesis.core.failures import format_failures

if TYPE_CHECKING:
    from schemathesis.config import OutputConfig
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.engine.statistic import GroupedFailures


TextOutput = IO[str] | StringIO | Path


class JunitXmlWriter:
    """Accumulates test results and writes JUnit XML on close."""

    def __init__(self, output: TextOutput, config: OutputConfig | None = None) -> None:
        self._output = output
        self._config = config
        self._test_cases: dict[str, TestCase] = {}

    def record_scenario(
        self,
        label: str,
        elapsed_sec: float,
        failures: Iterable[GroupedFailures],
        skip_reason: str | None,
        config: OutputConfig,
    ) -> None:
        """Record a finished test scenario."""
        test_case = self._get_or_create(label)
        test_case.elapsed_sec += elapsed_sec
        failures = list(failures)
        if failures:
            messages = [
                format_failures(
                    case_id=f"{idx}. Test Case ID: {group.case_id}",
                    response=group.response,
                    failures=group.failures,
                    curl=group.code_sample,
                    config=config,
                )
                for idx, group in enumerate(failures, 1)
            ]
            test_case.add_failure_info(message="\n\n".join(messages))
        elif skip_reason is not None:
            test_case.add_skipped_info(output=skip_reason)

    def write(self, recorder: ScenarioRecorder, elapsed_sec: float = 0.0) -> None:
        """Write all interactions from a ScenarioRecorder as a JUnit test case."""
        from schemathesis.engine.statistic import GroupedFailures

        assert self._config is not None
        grouped = []
        for case_id, checks in recorder.checks.items():
            failed = [c.failure_info for c in checks if c.failure_info is not None]
            if not failed:
                continue
            interaction = recorder.interactions.get(case_id)
            grouped.append(
                GroupedFailures(
                    case_id=case_id,
                    code_sample=failed[0].code_sample,
                    failures=[f.failure for f in failed],
                    response=interaction.response if interaction is not None else None,
                )
            )
        self.record_scenario(
            label=recorder.label,
            elapsed_sec=elapsed_sec,
            failures=grouped,
            skip_reason=None,
            config=self._config,
        )

    def record_error(self, label: str, message: str) -> None:
        """Record a non-fatal error for a label."""
        self._get_or_create(label).add_error_info(output=message)

    def close(self) -> None:
        """Write the JUnit XML report and close the output."""
        test_suites = [TestSuite("schemathesis", test_cases=list(self._test_cases.values()), hostname=platform.node())]
        if isinstance(self._output, Path):
            with open(self._output, "w", encoding="utf-8") as fd:
                to_xml_report_file(file_descriptor=fd, test_suites=test_suites, prettyprint=True, encoding="utf-8")
        else:
            to_xml_report_file(
                file_descriptor=self._output, test_suites=test_suites, prettyprint=True, encoding="utf-8"
            )

    def _get_or_create(self, label: str) -> TestCase:
        return self._test_cases.setdefault(label, TestCase(label, elapsed_sec=0.0, allow_multiple_subelements=True))

    def __enter__(self) -> JunitXmlWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()
