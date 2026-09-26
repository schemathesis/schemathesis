from __future__ import annotations

import platform
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from types import TracebackType
from typing import IO, TYPE_CHECKING
from xml.etree import ElementTree

from schemathesis.core.failures import format_failures
from schemathesis.engine import Status
from schemathesis.reporting.recorders import grouped_failures_from_recorder

if TYPE_CHECKING:
    from typing_extensions import Self

    from schemathesis.config import OutputConfig
    from schemathesis.engine.recorder import RecordedScenario
    from schemathesis.engine.statistic import GroupedFailures


TextOutput = IO[str] | StringIO | Path

# Characters XML 1.0 forbids even as character references.
_XML_ILLEGAL_CHARACTERS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


@dataclass(slots=True)
class _RecordedOutcome:
    text: str
    message: str | None = None


@dataclass(slots=True)
class _TestCase:
    name: str
    elapsed_sec: float = 0.0
    failures: list[_RecordedOutcome] = field(default_factory=list)
    errors: list[_RecordedOutcome] = field(default_factory=list)
    skipped: list[_RecordedOutcome] = field(default_factory=list)


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
            test_case.failures.append(_RecordedOutcome("\n\n".join(messages)))
        elif skip_reason is not None:
            test_case.skipped.append(_RecordedOutcome(skip_reason))

    def write(
        self,
        recorder: RecordedScenario,
        elapsed_sec: float = 0.0,
        *,
        status: Status | None = None,
        message: str | None = None,
    ) -> None:
        """Write all interactions from a ScenarioRecorder as a JUnit test case."""
        assert self._config is not None
        failures = grouped_failures_from_recorder(recorder)
        self.record_scenario(
            label=recorder.label,
            elapsed_sec=elapsed_sec,
            failures=failures,
            skip_reason=None,
            config=self._config,
        )
        if failures or status is None or status == Status.SUCCESS:
            return
        outcome = _RecordedOutcome(text=message or "", message=message)
        test_case = self._get_or_create(recorder.label)
        if status == Status.FAILURE:
            test_case.failures.append(outcome)
        elif status == Status.ERROR:
            test_case.errors.append(outcome)
        elif status == Status.SKIP:
            test_case.skipped.append(outcome)

    def record_error(self, label: str, message: str) -> None:
        """Record a non-fatal error for a label."""
        self._get_or_create(label).errors.append(_RecordedOutcome(message))

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
        _append_outcomes(element, "failure", case.failures)
        _append_outcomes(element, "error", case.errors)
        _append_outcomes(element, "skipped", case.skipped)

    ElementTree.indent(suites)
    body = ElementTree.tostring(suites, encoding="unicode")
    return f'<?xml version="1.0" encoding="utf-8"?>\n{body}'


def _append_outcomes(element: ElementTree.Element, kind: str, outcomes: list[_RecordedOutcome]) -> None:
    for outcome in outcomes:
        attributes = {"type": kind}
        if outcome.message is not None:
            attributes["message"] = _escape_illegal_characters(outcome.message)
        ElementTree.SubElement(element, kind, attributes).text = _escape_illegal_characters(outcome.text)


def _escape_illegal_characters(text: str) -> str:
    return _XML_ILLEGAL_CHARACTERS.sub(lambda match: ascii(match.group())[1:-1], text)
