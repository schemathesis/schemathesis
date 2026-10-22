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
    writers: list[_ReportWriter] = []
    try:
        if reports.vcr.enabled:
            from schemathesis.reporting.vcr import VcrWriter

            vcr_writer = VcrWriter(
                output=path_of(ReportFormat.VCR), config=output, preserve_bytes=reports.preserve_bytes
            )
            vcr_writer.open(seed=seed, command=command)
            writers.append(vcr_writer)
        if reports.har.enabled:
            from schemathesis.reporting.har import HarWriter

            har_writer = HarWriter(
                output=path_of(ReportFormat.HAR), config=output, preserve_bytes=reports.preserve_bytes
            )
            har_writer.open(seed=seed)
            writers.append(har_writer)
        if reports.junit.enabled:
            from schemathesis.reporting.junitxml import JunitXmlWriter

            writers.append(JunitXmlWriter(output=path_of(ReportFormat.JUNIT), config=output))
        if reports.allure.enabled:
            try:
                from schemathesis.reporting.allure import AllureWriter
            except ImportError as exc:
                raise pytest.UsageError(str(exc)) from exc

            writers.append(AllureWriter(output_dir=path_of(ReportFormat.ALLURE), config=output, api_title=api_title))
        if reports.html.enabled:
            from schemathesis.reporting.html.writer import HtmlWriter

            writers.append(
                HtmlWriter(
                    path_of(ReportFormat.HTML),
                    config=output,
                    location=location,
                    base_url=base_url,
                    command=command,
                    seed=seed,
                    checks=checks,
                    started_at=started_at,
                )
            )
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
