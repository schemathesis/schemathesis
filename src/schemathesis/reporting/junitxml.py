from __future__ import annotations

import platform
from collections.abc import Iterable
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from types import TracebackType
from typing import IO, TYPE_CHECKING
from xml.etree import ElementTree

from schemathesis.core.failures import format_failures

if TYPE_CHECKING:
    from typing_extensions import Self

    from schemathesis.config import OutputConfig
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.engine.statistic import GroupedFailures


TextOutput = IO[str] | StringIO | Path


@dataclass
class _TestCase:
    name: str
    elapsed_sec: float = 0.0
    failures: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class JunitXmlWriter:
    """Accumulates test results and writes JUnit XML on close."""

    def __init__(self, output: TextOutput, config: OutputConfig | None = None) -> None:
        self._output = output
        self._config = config
        self._test_cases: dict[str, _TestCase] = {}

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
                    case_id=f"{idx}. Test Case ID: {group.case_id}" if group.case_id is not None else None,
                    response=group.response,
                    failures=group.failures,
                    curl=group.code_sample,
                    config=config,
                )
                for idx, group in enumerate(failures, 1)
            ]
            test_case.failures.append("\n\n".join(messages))
        elif skip_reason is not None:
            test_case.skipped.append(skip_reason)

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
        self._get_or_create(label).errors.append(message)

    def close(self) -> None:
        """Write the JUnit XML report and close the output."""
        document = _render(list(self._test_cases.values()))
        if isinstance(self._output, Path):
            with open(self._output, "w", encoding="utf-8") as fd:
                fd.write(document)
        else:
            self._output.write(document)

    def _get_or_create(self, label: str) -> _TestCase:
        return self._test_cases.setdefault(label, _TestCase(name=label))

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


def _render(test_cases: list[_TestCase]) -> str:
    total = len(test_cases)
    failures = sum(1 for case in test_cases if case.failures)
    errors = sum(1 for case in test_cases if case.errors)
    skipped = sum(1 for case in test_cases if case.skipped)
    time = f"{sum(case.elapsed_sec for case in test_cases):.6f}"
    counts = {"errors": str(errors), "failures": str(failures), "skipped": str(skipped), "tests": str(total)}

    suites = ElementTree.Element("testsuites", {**counts, "time": time})
    suite = ElementTree.SubElement(
        suites, "testsuite", {"name": "schemathesis", "hostname": platform.node(), **counts, "time": time}
    )
    for case in test_cases:
        element = ElementTree.SubElement(suite, "testcase", {"name": case.name, "time": f"{case.elapsed_sec:.6f}"})
        for message in case.failures:
            ElementTree.SubElement(element, "failure", {"type": "failure"}).text = message
        for message in case.errors:
            ElementTree.SubElement(element, "error", {"type": "error"}).text = message
        for message in case.skipped:
            ElementTree.SubElement(element, "skipped", {"type": "skipped"}).text = message

    ElementTree.indent(suites)
    body = ElementTree.tostring(suites, encoding="unicode")
    return f'<?xml version="1.0" encoding="utf-8"?>\n{body}'
