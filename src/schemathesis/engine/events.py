from __future__ import annotations

import time
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.core.result import Result
from schemathesis.core.schema_analysis import SchemaWarning
from schemathesis.engine.errors import EngineErrorInfo
from schemathesis.engine.phases import Phase, PhaseName
from schemathesis.engine.recorder import ScenarioRecorder

if TYPE_CHECKING:
    from schemathesis.engine import Status
    from schemathesis.engine.phases.probes import ProbePayload

EventGenerator = Generator["EngineEvent", None, None]


@dataclass
class EngineEvent:
    """An event within the engine's lifecycle."""

    id: uuid.UUID
    timestamp: float
    # Indicates whether this event is the last in the event stream
    is_terminal = False


@dataclass
class EngineStarted(EngineEvent):
    """Start of an engine."""

    __slots__ = ("id", "timestamp")

    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()


@dataclass
class PhaseEvent(EngineEvent):
    """Event associated with a specific execution phase."""

    phase: Phase


@dataclass
class StatefulPhasePayload:
    inferred_links: int

    __slots__ = ("inferred_links",)


@dataclass
class PhaseStarted(PhaseEvent):
    """Start of an execution phase."""

    payload: StatefulPhasePayload | None

    __slots__ = ("id", "timestamp", "phase", "payload")

    def __init__(self, *, phase: Phase, payload: StatefulPhasePayload | None) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.phase = phase
        self.payload = payload


@dataclass
class PhaseFinished(PhaseEvent):
    """End of an execution phase."""

    status: Status
    payload: Result[ProbePayload, Exception] | None

    __slots__ = ("id", "timestamp", "phase", "status", "payload")

    def __init__(self, *, phase: Phase, status: Status, payload: Result[ProbePayload, Exception] | None) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.phase = phase
        self.status = status
        self.payload = payload


@dataclass
class SchemaAnalysisWarnings(PhaseEvent):
    """Schema analysis discovered warnings."""

    warnings: list[SchemaWarning]

    __slots__ = ("id", "timestamp", "phase", "warnings")

    def __init__(self, *, phase: Phase, warnings: list[SchemaWarning]) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.phase = phase
        self.warnings = warnings


@dataclass
class TestEvent(EngineEvent):
    phase: PhaseName


@dataclass
class SuiteStarted(TestEvent):
    """Before executing a set of scenarios."""

    __slots__ = ("id", "timestamp", "phase")

    def __init__(self, *, phase: PhaseName) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.phase = phase


@dataclass
class SuiteFinished(TestEvent):
    """After executing a set of test scenarios."""

    status: Status

    __slots__ = ("id", "timestamp", "phase", "status")

    def __init__(self, *, id: uuid.UUID, phase: PhaseName, status: Status) -> None:
        self.id = id
        self.timestamp = time.time()
        self.phase = phase
        self.status = status


@dataclass
class ScenarioEvent(TestEvent):
    suite_id: uuid.UUID


@dataclass
class ScenarioStarted(ScenarioEvent):
    """Before executing a grouped set of test steps."""

    __slots__ = ("id", "timestamp", "phase", "suite_id", "label")

    def __init__(self, *, phase: PhaseName, suite_id: uuid.UUID, label: str | None) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.phase = phase
        self.suite_id = suite_id
        self.label = label


@dataclass
class ScenarioFinished(ScenarioEvent):
    """After executing a grouped set of test steps."""

    status: Status
    recorder: ScenarioRecorder
    elapsed_time: float
    skip_reason: str | None
    # Whether this is a scenario that tries to reproduce a failure
    is_final: bool

    __slots__ = (
        "id",
        "timestamp",
        "phase",
        "suite_id",
        "label",
        "status",
        "recorder",
        "elapsed_time",
        "skip_reason",
        "is_final",
    )

    def __init__(
        self,
        *,
        id: uuid.UUID,
        phase: PhaseName,
        suite_id: uuid.UUID,
        label: str | None,
        status: Status,
        recorder: ScenarioRecorder,
        elapsed_time: float,
        skip_reason: str | None,
        is_final: bool,
    ) -> None:
        self.id = id
        self.timestamp = time.time()
        self.phase = phase
        self.suite_id = suite_id
        self.label = label
        self.status = status
        self.recorder = recorder
        self.elapsed_time = elapsed_time
        self.skip_reason = skip_reason
        self.is_final = is_final


@dataclass
class Interrupted(EngineEvent):
    """If execution was interrupted by Ctrl-C, or a received SIGTERM."""

    phase: PhaseName | None

    __slots__ = ("id", "timestamp", "phase")

    def __init__(self, *, phase: PhaseName | None) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.phase = phase


@dataclass
class NonFatalError(EngineEvent):
    """Error that doesn't halt execution but should be reported."""

    info: EngineErrorInfo
    value: Exception
    phase: PhaseName
    label: str
    related_to_operation: bool

    __slots__ = ("id", "timestamp", "info", "value", "phase", "label", "related_to_operation")

    def __init__(
        self,
        *,
        error: Exception,
        phase: PhaseName,
        label: str,
        related_to_operation: bool,
        code_sample: str | None = None,
    ) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.info = EngineErrorInfo(error=error, code_sample=code_sample)
        self.value = error
        self.phase = phase
        self.label = label
        self.related_to_operation = related_to_operation

    def __eq__(self, other: object) -> bool:
        assert isinstance(other, NonFatalError)
        return self.label == other.label and type(self.value) is type(other.value)

    def __hash__(self) -> int:
        return hash((self.label, type(self.value)))


@dataclass
class FatalError(EngineEvent):
    """Internal error in the engine."""

    exception: Exception
    is_terminal = True

    __slots__ = ("id", "timestamp", "exception")

    def __init__(self, *, exception: Exception) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.exception = exception


@dataclass
class EngineFinished(EngineEvent):
    """The final event of the run.

    No more events after this point.
    """

    is_terminal = True
    running_time: float

    __slots__ = ("id", "timestamp", "running_time")

    def __init__(self, *, running_time: float) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.running_time = running_time
