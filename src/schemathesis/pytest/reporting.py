from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

import pytest

from schemathesis.config import ReportFormat
from schemathesis.engine import Status
from schemathesis.engine.recorder import ScenarioRecorder

if TYPE_CHECKING:
    import requests
    from _pytest.terminal import TerminalReporter

    from schemathesis.checks import CheckResult
    from schemathesis.config import OutputConfig, ReportsConfig
    from schemathesis.core.spec import SchemaMetadata
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.hooks import HookContext
    from schemathesis.reporting.allure import AllureWriter
    from schemathesis.reporting.har import HarWriter
    from schemathesis.reporting.html.writer import HtmlWriter
    from schemathesis.reporting.junitxml import JunitXmlWriter
    from schemathesis.reporting.vcr import VcrWriter

    _ReportWriter: TypeAlias = VcrWriter | HarWriter | JunitXmlWriter | AllureWriter | HtmlWriter


# Reports written during the run, as path -> format name, in the order they were opened.
REPORT_LOCATIONS_KEY: pytest.StashKey[dict[Path, str]] = pytest.StashKey()
# Listed in the order the CLI lists them.
_PYTEST_FORMATS = (ReportFormat.JUNIT, ReportFormat.VCR, ReportFormat.HAR, ReportFormat.ALLURE, ReportFormat.HTML)


def remember_report_locations(
    config: pytest.Config, reports: ReportsConfig, path_of: Callable[[ReportFormat], Path]
) -> None:
    locations = config.stash.setdefault(REPORT_LOCATIONS_KEY, {})
    for format in _PYTEST_FORMATS:
        if reports.get(format).enabled:
            path = path_of(format)
            # Point at the page itself so it opens in one click.
            if format == ReportFormat.HTML:
                path = path / "index.html"
            locations.setdefault(path, format.display_name)


@dataclass(slots=True)
class _WriterOptions:
    output: OutputConfig
    preserve_bytes: bool
    seed: int | None
    command: str
    api_title: str | None
    location: str | None
    base_url: str | None
    checks: list[str]
    started_at: float


def _open_vcr(path: Path, options: _WriterOptions) -> VcrWriter:
    from schemathesis.reporting.vcr import VcrWriter

    writer = VcrWriter(output=path, config=options.output, preserve_bytes=options.preserve_bytes)
    writer.open(seed=options.seed, command=options.command)
    return writer


def _open_har(path: Path, options: _WriterOptions) -> HarWriter:
    from schemathesis.reporting.har import HarWriter

    writer = HarWriter(output=path, config=options.output, preserve_bytes=options.preserve_bytes)
    writer.open(seed=options.seed)
    return writer


def _open_junit(path: Path, options: _WriterOptions) -> JunitXmlWriter:
    from schemathesis.reporting.junitxml import JunitXmlWriter

    return JunitXmlWriter(output=path, config=options.output)


def _open_allure(path: Path, options: _WriterOptions) -> AllureWriter:
    try:
        from schemathesis.reporting.allure import AllureWriter
    except ImportError as exc:
        raise pytest.UsageError(str(exc)) from exc

    return AllureWriter(output_dir=path, config=options.output, api_title=options.api_title)


def _open_html(path: Path, options: _WriterOptions) -> HtmlWriter:
    from schemathesis.reporting.html.writer import HtmlWriter

    return HtmlWriter(
        path,
        config=options.output,
        location=options.location,
        base_url=options.base_url,
        command=options.command,
        seed=options.seed,
        checks=options.checks,
        started_at=options.started_at,
    )


# In the order the writers are opened.
_WRITER_OPENERS: tuple[tuple[ReportFormat, Callable[[Path, _WriterOptions], _ReportWriter]], ...] = (
    (ReportFormat.VCR, _open_vcr),
    (ReportFormat.HAR, _open_har),
    (ReportFormat.JUNIT, _open_junit),
    (ReportFormat.ALLURE, _open_allure),
    (ReportFormat.HTML, _open_html),
)


def open_writers(
    pytest_config: pytest.Config,
    reports: ReportsConfig,
    path_of: Callable[[ReportFormat], Path],
    *,
    output: OutputConfig,
    seed: int | None,
    command: str,
    api_title: str | None,
    location: str | None,
    base_url: str | None,
    checks: list[str],
    started_at: float,
) -> list[_ReportWriter]:
    """Open a writer for every enabled report, closing the opened ones if a later one fails."""
    options = _WriterOptions(
        output=output,
        preserve_bytes=reports.preserve_bytes,
        seed=seed,
        command=command,
        api_title=api_title,
        location=location,
        base_url=base_url,
        checks=checks,
        started_at=started_at,
    )
    writers: list[_ReportWriter] = []
    try:
        for format, open_writer in _WRITER_OPENERS:
            if getattr(reports, format.value).enabled:
                writers.append(open_writer(path_of(format), options))
    except Exception:
        for writer in writers:
            writer.close()
        raise
    remember_report_locations(pytest_config, reports, path_of)
    return writers


def write_report_locations(terminalreporter: TerminalReporter, config: pytest.Config) -> None:
    locations = config.stash.get(REPORT_LOCATIONS_KEY, {})
    if locations:
        terminalreporter.write_sep("=", "Schemathesis reports")
        for path, name in locations.items():
            terminalreporter.write_line(f"  - {name}: {path}")


@dataclass(frozen=True, slots=True)
class PytestReportOutcome:
    status: Status
    message: str | None = None


class PytestReportDispatcher:
    """Accumulates ScenarioRecorders for a schema, one per API operation label.

    Registers after_call and after_validate on schema.hooks (not globally).
    Does not own writers - the plugin owns writers and calls pop_recorder() at teardown.
    This separation keeps the dispatcher unchanged when xdist support is added later.
    """

    def __init__(self, schema: SchemaMetadata) -> None:
        self._schema = schema
        self._recorders: dict[str, ScenarioRecorder] = {}
        self._start_times: dict[str, float] = {}
        # Store bound methods so unregister() removes the same objects.
        # Python bound methods are not cached - accessing self._on_after_call twice
        # gives two different objects, breaking identity comparison in hooks.unregister().
        self._after_call_hook = self._on_after_call
        self._after_validate_hook = self._on_after_validate
        self._after_network_error_hook = self._on_after_network_error
        schema.hooks.register_hook_with_name(self._after_call_hook, "after_call")
        schema.hooks.register_hook_with_name(self._after_validate_hook, "after_validate")
        schema.hooks.register_hook_with_name(self._after_network_error_hook, "after_network_error")

    def _on_after_call(self, context: HookContext, case: Case, response: Response) -> None:
        label = case.operation.label
        self._start_times.setdefault(label, time.monotonic())
        recorder = self._recorders.setdefault(label, ScenarioRecorder(label=label, config=self._schema.config.output))
        recorder.record_case(case=case)
        recorder.record_response(case_id=case.id, response=response)

    def _on_after_network_error(self, context: HookContext, case: Case, request: requests.PreparedRequest) -> None:
        label = case.operation.label
        self._start_times.setdefault(label, time.monotonic())
        recorder = self._recorders.setdefault(label, ScenarioRecorder(label=label, config=self._schema.config.output))
        recorder.record_case(case=case)
        recorder.record_request(case_id=case.id, request=request)

    def _on_after_validate(
        self, context: HookContext, case: Case, response: Response, check_results: list[CheckResult]
    ) -> None:
        recorder = self._recorders.get(case.operation.label)
        assert recorder is not None, "after_validate fired without a prior after_call for the same operation"
        for result in check_results:
            if result.failure is not None:
                code_sample = case.as_curl_command(
                    headers=dict(response.request.headers),
                    verify=response.verify,
                )
                recorder.record_check_failure(
                    name=result.name,
                    case_id=case.id,
                    code_sample=code_sample,
                    failure=result.failure,
                )
            else:
                recorder.record_check_success(name=result.name, case_id=case.id)

    def pop_recorder(self, label: str) -> tuple[ScenarioRecorder, float] | None:
        """Remove and return the recorder and elapsed seconds for this label.

        Removing the recorder lets the GC reclaim the Case objects it holds.
        Called by the plugin at test teardown after writing to all report writers.
        """
        recorder = self._recorders.pop(label, None)
        if recorder is None:
            return None
        elapsed = time.monotonic() - self._start_times.pop(label, time.monotonic())
        return recorder, elapsed

    def unregister(self) -> None:
        """Remove all hooks from schema.hooks. Called at pytest_sessionfinish."""
        self._schema.hooks.unregister(self._after_call_hook)
        self._schema.hooks.unregister(self._after_validate_hook)
        self._schema.hooks.unregister(self._after_network_error_hook)
