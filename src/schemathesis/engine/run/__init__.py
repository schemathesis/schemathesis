from __future__ import annotations

import enum
import queue
import threading
import warnings
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from schemathesis.engine import Status

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EventGenerator

T = TypeVar("T")


class PhaseName(enum.StrEnum):
    """Available execution phases."""

    PROBING = "probing"
    SCHEMA_ANALYSIS = "schema_analysis"
    AUTH_BOOTSTRAP = "auth_bootstrap"
    EXAMPLES = "examples"
    COVERAGE = "coverage"
    FUZZING = "fuzzing"
    STATEFUL_TESTING = "stateful"

    @property
    def display(self) -> str:
        """Title-cased label for terminal output."""
        if self is PhaseName.PROBING:
            return "API probing"
        return self.value.replace("_", " ").capitalize()

    @classmethod
    def from_str(cls, value: str) -> PhaseName:
        return cls(value.lower())


# Internal phases are not part of the reported test phases, matching the terminal.
INTERNAL_PHASES = (PhaseName.PROBING, PhaseName.SCHEMA_ANALYSIS, PhaseName.AUTH_BOOTSTRAP)


# Phases that test each operation on its own.
UNIT_PHASES = (PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING)


# Phases that send test cases; their operations can error out before any test runs.
TESTING_PHASES = (*UNIT_PHASES, PhaseName.STATEFUL_TESTING)


# Phases worth repeating: their yield scales with the time given to them.
ELASTIC_PHASES = (PhaseName.FUZZING, PhaseName.STATEFUL_TESTING)


class PhaseSkipReason(enum.StrEnum):
    """Reasons why a phase might not be executed."""

    # Explicitly disabled via config
    DISABLED = "disabled"
    # Feature not supported by schema
    NOT_SUPPORTED = "not_supported"
    # No relevant data (e.g., no transitions for stateful)
    NOT_APPLICABLE = "not_applicable"
    FAILURE_LIMIT_REACHED = "failure_limit_reached"
    SERVER_UNAVAILABLE = "server_unavailable"
    NOTHING_TO_TEST = "nothing_to_test"
    # Cases ran, but no selected check applied to any of them
    NO_CHECKS_RAN = "no_checks_ran"

    @property
    def display(self) -> str:
        """Label for terminal output."""
        return self.value.replace("_", " ")


@dataclass(slots=True)
class Phase:
    """A logically separate engine execution phase."""

    name: PhaseName
    is_enabled: bool
    skip_reason: PhaseSkipReason | None

    def __init__(self, name: PhaseName, is_enabled: bool = True, skip_reason: PhaseSkipReason | None = None) -> None:
        self.name = name
        self.is_enabled = is_enabled
        self.skip_reason = skip_reason

    def should_execute(self, ctx: EngineContext) -> bool:
        """Determine if phase should run based on context & configuration."""
        return self.is_enabled and not ctx.has_to_stop

    def enable(self) -> None:
        """Enable this test phase."""
        self.is_enabled = True
        self.skip_reason = None


@dataclass(slots=True)
class PhaseProgress:
    """What a phase's event stream has shown so far, kept across interrupts."""

    status: Status | None = None
    is_executed: bool = False
    checks_ran: bool = False

    def record_error(self) -> None:
        self.is_executed = True
        self.status = Status.ERROR

    def final_status(self, phase: Phase) -> Status:
        """The phase outcome; a phase skipped for lack of tests or checks records why."""
        if not self.is_executed:
            phase.skip_reason = PhaseSkipReason.NOTHING_TO_TEST
            return Status.SKIP
        if self.status is None:
            return Status.SKIP
        if self.status == Status.SUCCESS and not self.checks_ran:
            phase.skip_reason = PhaseSkipReason.NO_CHECKS_RAN
            return Status.SKIP
        return self.status


def report_outage(engine: EngineContext, phase: PhaseName, progress: PhaseProgress) -> EventGenerator:
    outage = engine.server.take_report(phase)
    if outage is not None:
        progress.record_error()
        yield outage


def relay_events(event_queue: queue.Queue[T], threads: Sequence[threading.Thread], *, timeout: float) -> Iterator[T]:
    """Yield queued events until every producer thread has exited and nothing is left to drain."""
    while True:
        try:
            event = event_queue.get(timeout=timeout)
        except queue.Empty:
            # A producer may put its last events and exit between the poll timing out and the liveness check.
            if not any(thread.is_alive() for thread in threads) and event_queue.empty():
                return
            continue
        yield event


def execute(ctx: EngineContext, phase: Phase, *, only: frozenset[str] | None = None) -> EventGenerator:
    from urllib3.exceptions import InsecureRequestWarning

    from . import analysis, auth_bootstrap, probes, stateful, unit

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureRequestWarning)

        if phase.name == PhaseName.PROBING:
            yield from probes.execute(ctx, phase)
        elif phase.name == PhaseName.SCHEMA_ANALYSIS:
            yield from analysis.execute(ctx, phase)
        elif phase.name == PhaseName.AUTH_BOOTSTRAP:
            yield from auth_bootstrap.execute(ctx, phase)
        elif phase.name in UNIT_PHASES:
            yield from unit.execute(ctx, phase, only=only)
        elif phase.name == PhaseName.STATEFUL_TESTING:
            yield from stateful.execute(ctx, phase)
