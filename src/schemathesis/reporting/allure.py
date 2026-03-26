from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from hashlib import md5
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import uuid4

from allure_commons import hookimpl
from allure_commons.logger import AllureFileLogger
from allure_commons.model2 import Attachment, Label, Link, StatusDetails, TestResult, TestStepResult

from schemathesis.core.failures import format_failures
from schemathesis.engine import Status

if TYPE_CHECKING:
    from schemathesis.config import OutputConfig
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.engine.statistic import GroupedFailures


@runtime_checkable
class _HasMimeType(Protocol):
    """Protocol for allure AttachmentType enum values that carry a mime_type."""

    mime_type: str


_STATUS_PRIORITY = {"skipped": 0, "passed": 1, "failed": 2, "broken": 3}
_SEVERITY_PRIORITY = {"minor": 0, "normal": 1, "critical": 2, "blocker": 3}
_SEVERITY_MAP = {
    "CRITICAL": "blocker",
    "HIGH": "critical",
    "MEDIUM": "normal",
    "LOW": "minor",
}


def _merge_status(current: str | None, new: str) -> str:
    if current is None:
        return new
    return new if _STATUS_PRIORITY.get(new, 0) > _STATUS_PRIORITY.get(current, 0) else current


def _to_allure_status(status: Status) -> str:
    return {
        Status.SUCCESS: "passed",
        Status.FAILURE: "failed",
        Status.ERROR: "broken",
        Status.SKIP: "skipped",
    }[status]


def _grouped_failures_from_recorder(recorder: ScenarioRecorder) -> list[GroupedFailures]:
    from schemathesis.engine.statistic import GroupedFailures

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
    return grouped


class AllureWriter:
    """Accumulates per-operation TestResult objects and writes Allure JSON files on close()."""

    __slots__ = (
        "_api_title",
        "_attachment_bodies",
        "_config",
        "_elapsed",
        "_failures",
        "_logger",
        "_output_dir",
        "_results",
        "_seen_curls",
        "_skip_reasons",
    )

    def __init__(
        self, output_dir: str | Path, config: OutputConfig | None = None, api_title: str | None = None
    ) -> None:
        self._output_dir = Path(output_dir)
        self._config = config
        self._api_title = api_title
        self._results: dict[str, TestResult] = {}
        self._elapsed: dict[str, float] = {}
        self._failures: dict[str, list[GroupedFailures]] = {}
        self._seen_curls: dict[str, set[str]] = {}
        self._skip_reasons: dict[str, str] = {}
        self._attachment_bodies: dict[str, bytes] = {}
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._logger = AllureFileLogger(str(self._output_dir))

    def _get_or_create(self, label: str) -> TestResult:
        if label not in self._results:
            now_ms = int(time.time() * 1000)
            label_hash = md5(label.encode(), usedforsecurity=False).hexdigest()
            labels: list[Label] = [
                Label(name="story", value=label),
                Label(name="framework", value="schemathesis"),
                Label(name="layer", value="API"),
            ]
            if self._api_title is not None:
                labels.append(Label(name="epic", value=self._api_title))
            result = TestResult(
                uuid=str(uuid4()),
                name=label,
                fullName=label,
                testCaseId=label_hash,
                historyId=label_hash,
                start=now_ms,
                stop=now_ms,
                status=None,
                statusDetails=StatusDetails(),
                labels=labels,
                parameters=[],
                attachments=[],
            )
            self._results[label] = result
            self._elapsed[label] = 0.0
        return self._results[label]

    def record_scenario(
        self,
        label: str,
        elapsed_sec: float,
        status: Status,
        failures: Iterable[GroupedFailures],
        skip_reason: str | None,
        tags: list[str] | None = None,
    ) -> None:
        result = self._get_or_create(label)

        # Set feature labels once (first call wins); skip entirely when no tags
        if not any(lbl.name == "feature" for lbl in result.labels) and tags:
            result.labels.extend(Label(name="feature", value=tag) for tag in tags)

        self._elapsed[label] += elapsed_sec
        result.stop = result.start + int(self._elapsed[label] * 1000)
        result.status = _merge_status(result.status, _to_allure_status(status))

        worst_severity: str | None = None

        for group in failures:
            seen = self._seen_curls.setdefault(label, set())
            if group.code_sample in seen:
                continue
            seen.add(group.code_sample)
            self._failures.setdefault(label, []).append(group)

            for failure in group.failures:
                allure_sev = _SEVERITY_MAP.get(failure.severity.name, "normal")
                if worst_severity is None or _SEVERITY_PRIORITY.get(allure_sev, 0) > _SEVERITY_PRIORITY.get(
                    worst_severity, 0
                ):
                    worst_severity = allure_sev

        if skip_reason is not None and label not in self._skip_reasons:
            self._skip_reasons[label] = skip_reason

        if worst_severity is not None:
            result.labels = [lbl for lbl in result.labels if lbl.name != "severity"]
            result.labels.append(Label(name="severity", value=worst_severity))

    def write(self, recorder: ScenarioRecorder, elapsed_sec: float = 0.0, tags: list[str] | None = None) -> None:
        assert self._config is not None

        grouped = _grouped_failures_from_recorder(recorder)
        status = Status.FAILURE if grouped else Status.SUCCESS
        self.record_scenario(
            label=recorder.label,
            elapsed_sec=elapsed_sec,
            status=status,
            failures=grouped,
            skip_reason=None,
            tags=tags,
        )

    def record_error(self, label: str, message: str) -> None:
        result = self._get_or_create(label)
        result.status = "broken"
        result.statusDetails = StatusDetails(message=message)

    def accumulate_attachment(self, label: str, name: str, body: bytes, attachment_type: _HasMimeType | None) -> None:
        mime = attachment_type.mime_type if isinstance(attachment_type, _HasMimeType) else "text/plain"
        filename = f"{uuid4()}-attachment"
        self._attachment_bodies[filename] = body
        self._get_or_create(label).attachments.append(Attachment(name=name, source=filename, type=mime))

    def accumulate_link(self, label: str, url: str, link_type: str, name: str) -> None:
        self._get_or_create(label).links.append(Link(type=link_type, url=url, name=name))

    def accumulate_title(self, label: str, title: str) -> None:
        self._get_or_create(label).name = title

    def accumulate_description(self, label: str, description: str) -> None:
        self._get_or_create(label).description = description

    def close(self) -> None:

        assert self._config is not None

        for label, result in sorted(self._results.items()):
            groups = self._failures.get(label)
            if groups:
                for group in groups:
                    step = TestStepResult(
                        name=f"Test Case: {group.case_id}",
                        status="failed",
                        statusDetails=StatusDetails(
                            message=format_failures(
                                case_id=None,
                                response=group.response,
                                failures=group.failures,
                                curl=group.code_sample,
                                config=self._config,
                            ).lstrip()
                        ),
                        start=result.start,
                        stop=result.stop,
                    )
                    result.steps.append(step)
            elif label in self._skip_reasons:
                result.statusDetails = StatusDetails(message=self._skip_reasons[label])

            for attachment in result.attachments:
                body = self._attachment_bodies.get(attachment.source)
                if body is not None:
                    self._logger.report_attached_data(file_name=attachment.source, body=body)
            self._logger.report_result(result=result)


class _AllureCallBuffer:
    """Captures dynamic allure API calls in xdist workers for cross-process transport.

    Implements the same accumulate_* interface as AllureWriter so it can be
    passed directly to _AllureHookForwarder as a writer substitute.
    """

    __slots__ = ("_calls",)

    def __init__(self) -> None:
        self._calls: list[dict] = []

    def accumulate_attachment(
        self, label: str, name: str, body: bytes | str, attachment_type: _HasMimeType | None
    ) -> None:
        mime = attachment_type.mime_type if isinstance(attachment_type, _HasMimeType) else "text/plain"
        encoded = body.encode() if isinstance(body, str) else body
        self._calls.append({"type": "attach", "label": label, "name": name, "body": encoded, "mime": mime})

    def accumulate_link(self, label: str, url: str, link_type: str, name: str) -> None:
        self._calls.append({"type": "link", "label": label, "url": url, "link_type": link_type, "name": name})

    def accumulate_title(self, label: str, title: str) -> None:
        self._calls.append({"type": "title", "label": label, "title": title})

    def accumulate_description(self, label: str, description: str) -> None:
        self._calls.append({"type": "description", "label": label, "description": description})

    def to_list(self) -> list[dict]:
        return self._calls


class _AllureHookForwarder:
    """Per-test-item pluggy hookimpl that routes dynamic allure API calls to AllureWriter instances.

    Created fresh per pytest item, registered before the test body runs, unregistered in teardown.
    The label is bound at construction time from item.operation_label — no context vars needed.
    """

    __slots__ = ("_label", "_writers")

    def __init__(self, label: str, writers: Sequence[AllureWriter | _AllureCallBuffer]) -> None:
        self._label = label
        self._writers = writers

    @hookimpl  # type: ignore[untyped-decorator]
    def attach_data(self, name: str, body: bytes, attachment_type: _HasMimeType | None, extension: str | None) -> None:
        for w in self._writers:
            w.accumulate_attachment(self._label, name, body, attachment_type)

    @hookimpl  # type: ignore[untyped-decorator]
    def add_link(self, url: str, link_type: str, name: str) -> None:
        for w in self._writers:
            w.accumulate_link(self._label, url, link_type, name)

    @hookimpl  # type: ignore[untyped-decorator]
    def add_title(self, test_title: str) -> None:
        for w in self._writers:
            w.accumulate_title(self._label, test_title)

    @hookimpl  # type: ignore[untyped-decorator]
    def add_description(self, test_description: str) -> None:
        for w in self._writers:
            w.accumulate_description(self._label, test_description)
