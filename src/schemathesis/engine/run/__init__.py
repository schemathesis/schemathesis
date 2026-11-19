from __future__ import annotations

import enum
import queue
import signal
import threading
import warnings
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from schemathesis.engine import Status

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import FrameType

    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EventGenerator
    from schemathesis.generation.case import Case

T = TypeVar("T")


class PhaseName(enum.StrEnum):
    """Available execution phases."""

    SETUP = "setup"
    EXAMPLES = "examples"
    COVERAGE = "coverage"
    FUZZING = "fuzzing"
    STATEFUL_TESTING = "stateful"
    CONTINUOUS_FUZZING = "continuous_fuzzing"
    TEARDOWN = "teardown"

    @property
    def display(self) -> str:
        """Title-cased label for terminal output."""
        return self.value.replace("_", " ").capitalize()

    @classmethod
    def from_str(cls, value: str) -> PhaseName:
        return cls(value.lower())


# Internal phases are not part of the reported test phases, matching the terminal.
INTERNAL_PHASES = (PhaseName.SETUP, PhaseName.TEARDOWN)


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
        if not self.is_enabled:
            return False
        control = ctx.control
        # A budget spent before continuous fuzzing starts still buys it one scenario rather than an empty run.
        if self.name is PhaseName.CONTINUOUS_FUZZING:
            return not (
                control.is_interrupted or control.is_server_unavailable or control.has_reached_the_failure_limit
            )
        return not ctx.has_to_stop

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


# Set while the main thread relays events, so Ctrl-C waits for a point where the run can stop cleanly.
_pending_interrupt: threading.Event | None = None


def _record_interrupt(signum: int, frame: FrameType | None) -> None:
    pending = _pending_interrupt
    # Outside a relay, or on a second press, Ctrl-C raises as usual
    if pending is None or pending.is_set():
        signal.default_int_handler(signum, frame)
    pending.set()


@contextmanager
def deferred_interrupt() -> Iterator[threading.Event]:
    """Record Ctrl-C instead of raising it in whatever lock or event-handling code the main thread is running."""
    global _pending_interrupt
    is_main_thread = threading.current_thread() is threading.main_thread()
    # A relay inside a phase that already defers Ctrl-C shares its record
    if is_main_thread and _pending_interrupt is not None:
        yield _pending_interrupt
        return
    interrupted = threading.Event()
    # Only the default handler raises `KeyboardInterrupt`; custom and ignored handlers are left alone.
    if is_main_thread and signal.getsignal(signal.SIGINT) in (
        signal.default_int_handler,
        _record_interrupt,
    ):
        signal.signal(signal.SIGINT, _record_interrupt)
        _pending_interrupt = interrupted
    try:
        yield interrupted
    finally:
        if _pending_interrupt is interrupted:
            _pending_interrupt = None
        # A stream closed from another thread leaves the handler in place, where it acts as the default one
        if (
            threading.current_thread() is threading.main_thread()
            and signal.getsignal(signal.SIGINT) is _record_interrupt
        ):
            signal.signal(signal.SIGINT, signal.default_int_handler)


def relay_events(event_queue: queue.Queue[T], threads: Sequence[threading.Thread], *, timeout: float) -> Iterator[T]:
    """Yield queued events until every producer thread has exited and nothing is left to drain."""
    with deferred_interrupt() as interrupted:
        while True:
            try:
                event = event_queue.get(timeout=timeout)
            except queue.Empty:
                # A producer may put its last events and exit between the poll timing out and the liveness check.
                if interrupted.is_set() or (not any(thread.is_alive() for thread in threads) and event_queue.empty()):
                    break
                continue
            # Stop before handling the event, so producers learn about the interrupt as soon as possible
            if interrupted.is_set():
                break
            yield event
    if interrupted.is_set():
        raise KeyboardInterrupt


def execute(
    ctx: EngineContext,
    phase: Phase,
    *,
    only: frozenset[str] | None = None,
    held_back: Mapping[str, list[Case]] | None = None,
) -> EventGenerator:
    from urllib3.exceptions import InsecureRequestWarning

    from schemathesis.engine import fuzz

    from . import setup, stateful, teardown, unit

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureRequestWarning)

        if phase.name == PhaseName.SETUP:
            yield from setup.execute(ctx, phase)
        elif phase.name in UNIT_PHASES:
            yield from unit.execute(ctx, phase, only=only, held_back=held_back)
        elif phase.name == PhaseName.STATEFUL_TESTING:
            yield from stateful.execute(ctx, phase)
        elif phase.name == PhaseName.CONTINUOUS_FUZZING:
            yield from fuzz.execute(ctx, phase)
        elif phase.name == PhaseName.TEARDOWN:
            yield from teardown.execute(ctx, phase)
        else:
            raise NotImplementedError(phase.name)
