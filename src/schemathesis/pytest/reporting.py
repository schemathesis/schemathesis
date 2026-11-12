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
    from schemathesis.hooks import HookContext, HookDispatcher
    from schemathesis.reporting.allure import AllureWriter
    from schemathesis.reporting.har import HarWriter
    from schemathesis.reporting.html.writer import HtmlWriter
    from schemathesis.reporting.junitxml import JunitXmlWriter
    from schemathesis.reporting.vcr import VcrWriter

    _ReportWriter: TypeAlias = VcrWriter | HarWriter | JunitXmlWriter | AllureWriter | HtmlWriter


# Reports written during the run, as path -> format name, in the order they were opened.
REPORT_LOCATIONS_KEY: pytest.StashKey[dict[Path, str]] = pytest.StashKey()
# Listed in the order the CLI lists them.
PYTEST_FORMATS = (ReportFormat.JUNIT, ReportFormat.VCR, ReportFormat.HAR, ReportFormat.ALLURE, ReportFormat.HTML)


def remember_report_locations(
    config: pytest.Config, reports: ReportsConfig, path_of: Callable[[ReportFormat], Path]
) -> None:
    locations = config.stash.setdefault(REPORT_LOCATIONS_KEY, {})
    for format in PYTEST_FORMATS:
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
    # Report files may go into a directory that does not exist yet, e.g. the default one in a fresh checkout.
    for format in (ReportFormat.VCR, ReportFormat.HAR, ReportFormat.JUNIT):
        if reports.get(format).enabled:
            path_of(format).parent.mkdir(parents=True, exist_ok=True)
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


def write_to_writers(
    writers: list[_ReportWriter],
    recorder: ScenarioRecorder,
    elapsed_sec: float,
    tags: list[str] | None = None,
    outcome: PytestReportOutcome | None = None,
    has_recorder: bool = True,
) -> None:
    from schemathesis.reporting.har import HarWriter
    from schemathesis.reporting.html.writer import HtmlWriter
    from schemathesis.reporting.junitxml import JunitXmlWriter
    from schemathesis.reporting.vcr import VcrWriter

    status = outcome.status if outcome is not None else None
    message = outcome.message if outcome is not None else None
    for writer in writers:
        if isinstance(writer, (JunitXmlWriter, HtmlWriter)):
            writer.write(recorder, elapsed_sec, status=status, message=message)
        elif isinstance(writer, (VcrWriter, HarWriter)):
            if has_recorder:
                writer.write(recorder)
        else:
            # Only the Allure writer remains; checking its type would retry the optional Allure import on every call.
            writer.write(recorder, elapsed_sec, tags=tags, status=status, message=message)


class PytestReportDispatcher:
    """Records the calls each parametrized test makes into one ScenarioRecorder per test.

    Registers after_call, after_validate and after_network_error on the hooks of the schemas it records (not
    globally). Does not own writers - the plugin owns writers and calls pop_recorder() at teardown.
    """

    def __init__(self, schema: SchemaMetadata) -> None:
        self._schema = schema
        # Schemas loaded separately can share one report config, each with its own hooks.
        self._hooks: list[HookDispatcher] = []
        # The running test, which owns every call it makes. Stateful tests hand their own recorder to the writers,
        # and calls made outside a parametrized test belong to no result.
        self._label: str | None = None
        self._recorder: ScenarioRecorder | None = None
        self._started_at = 0.0
        # Store bound methods so unregister() removes the same objects.
        # Python bound methods are not cached - accessing self._on_after_call twice
        # gives two different objects, breaking identity comparison in hooks.unregister().
        self._after_call_hook = self._on_after_call
        self._after_validate_hook = self._on_after_validate
        self._after_network_error_hook = self._on_after_network_error

    def start(self, schema: SchemaMetadata, label: str) -> None:
        """Record the calls made through `schema` for the test of `label` until `pop_recorder`."""
        if not any(hooks is schema.hooks for hooks in self._hooks):
            schema.hooks.register_hook_with_name(self._after_call_hook, "after_call")
            schema.hooks.register_hook_with_name(self._after_validate_hook, "after_validate")
            schema.hooks.register_hook_with_name(self._after_network_error_hook, "after_network_error")
            self._hooks.append(schema.hooks)
        self._label = label
        self._recorder = None

    def _current_recorder(self) -> ScenarioRecorder | None:
        if self._label is None:
            return None
        if self._recorder is None:
            self._recorder = ScenarioRecorder(label=self._label, config=self._schema.config.output)
            self._started_at = time.monotonic()
        return self._recorder

    def _on_after_call(self, context: HookContext, case: Case, response: Response) -> None:
        recorder = self._current_recorder()
        if recorder is not None:
            recorder.record_case(case=case)
            recorder.record_response(case_id=case.id, response=response)

    def _on_after_network_error(self, context: HookContext, case: Case, request: requests.PreparedRequest) -> None:
        recorder = self._current_recorder()
        if recorder is not None:
            recorder.record_case(case=case)
            recorder.record_request(case_id=case.id, request=request)

    def _on_after_validate(
        self, context: HookContext, case: Case, response: Response, check_results: list[CheckResult]
    ) -> None:
        recorder = self._current_recorder()
        if recorder is None:
            return
        # A response the test received with its own client never passed through `after_call`.
        if case.id not in recorder.interactions:
            recorder.record_case(case=case)
            recorder.record_response(case_id=case.id, response=response)
        recorder.record_check_results(case, response, check_results)

    def pop_recorder(self) -> tuple[ScenarioRecorder, float] | None:
        """Stop recording and return what the test recorded with the elapsed seconds, or None if it made no calls.

        Dropping the recorder lets the GC reclaim the Case objects it holds.
        Called by the plugin at test teardown, before writing to the report writers.
        """
        recorder = self._recorder
        self._label = None
        self._recorder = None
        if recorder is None:
            return None
        return recorder, time.monotonic() - self._started_at

    def unregister(self) -> None:
        """Remove all hooks from the recorded schemas' hooks. Called at pytest_sessionfinish."""
        for hooks in self._hooks:
            hooks.unregister(self._after_call_hook)
            hooks.unregister(self._after_validate_hook)
            hooks.unregister(self._after_network_error_hook)
