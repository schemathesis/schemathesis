"""Per-operation reporting for lazy schemas.

pytest 9 ships subtests in its core. On pytest 8 the equivalent reports are emitted directly through
the runner hooks, so no extra plugin is required.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from _pytest.fixtures import FixtureRequest

HAS_CORE_SUBTESTS = pytest.version_tuple >= (9,)


def _suspend_capture(request: FixtureRequest) -> Callable[[], AbstractContextManager[None]]:
    capturemanager = request.node.config.pluginmanager.get_plugin("capturemanager")
    if capturemanager is not None:
        return capturemanager.global_and_fixture_disabled
    return nullcontext


if HAS_CORE_SUBTESTS:
    from _pytest.subtests import SubtestReport

    def make_subtests(request: FixtureRequest) -> pytest.Subtests:
        return pytest.Subtests(request.node.ihook, _suspend_capture(request), request, _ispytest=True)

    def is_subtest_report(report: pytest.TestReport) -> bool:
        return isinstance(report, SubtestReport)

else:
    from _pytest._code import ExceptionInfo
    from _pytest.runner import CallInfo, check_interactive_exception

    # Attribute set on reports produced for a single API operation. Plain attributes survive the
    # `pytest-xdist` round-trip, which a custom report subclass would not without extra hooks.
    SUBTEST_MARKER = "_schemathesis_subtest"

    def is_subtest_report(report: pytest.TestReport) -> bool:
        return getattr(report, SUBTEST_MARKER, False)

    class _SubtestContext:
        __slots__ = ("_precise_start", "_start", "_subtests")

        def __init__(self, subtests: _Subtests) -> None:
            self._subtests = subtests

        def __enter__(self) -> None:
            __tracebackhide__ = True
            self._start = time.time()
            self._precise_start = time.perf_counter()

        def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: Any) -> bool:
            __tracebackhide__ = True
            request = self._subtests.request
            node = request.node
            call = CallInfo[None](
                None,
                ExceptionInfo.from_exception(exc_val) if exc_val is not None else None,
                start=self._start,
                stop=time.time(),
                duration=time.perf_counter() - self._precise_start,
                when="call",
                _ispytest=True,
            )
            report = node.ihook.pytest_runtest_makereport(item=node, call=call)
            setattr(report, SUBTEST_MARKER, True)
            with self._subtests.suspend_capture():
                node.ihook.pytest_runtest_logreport(report=report)
            if check_interactive_exception(call, report):
                node.ihook.pytest_exception_interact(node=node, call=call, report=report)
            # Swallow the failure so the remaining operations still run, unless the session is bailing out
            return exc_val is None or not request.session.shouldfail

    class _Subtests:
        __slots__ = ("request", "suspend_capture")

        def __init__(self, request: FixtureRequest) -> None:
            self.request = request
            self.suspend_capture = _suspend_capture(request)

        def test(self, **kwargs: Any) -> _SubtestContext:
            # Labels are dropped: the reported name comes from the node id Schemathesis sets per operation
            return _SubtestContext(self)

    def make_subtests(request: FixtureRequest) -> pytest.Subtests:
        # Stand-in for the core type, which does not exist on pytest 8
        return _Subtests(request)

    @pytest.hookimpl(tryfirst=True)  # type: ignore[untyped-decorator]
    def pytest_report_teststatus(report: pytest.TestReport, config: pytest.Config) -> tuple[str, str, str] | None:
        if report.when != "call" or not is_subtest_report(report):
            return None
        if report.failed:
            return report.outcome, "u", "SUBFAILED"
        if config.get_verbosity() == 0:
            return "", "", ""
        if report.passed:
            return f"subtests {report.outcome}", "u", "SUBPASSED"
        if report.skipped:
            return report.outcome, "-", "SUBSKIPPED"
        return None
