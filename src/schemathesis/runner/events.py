from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generator

from schemathesis.core.transport import Response
from schemathesis.runner.errors import EngineErrorInfo
from schemathesis.runner.phases import Phase, PhaseName
from schemathesis.runner.recorder import ScenarioRecorder

if TYPE_CHECKING:
    from schemathesis.core import Specification
    from schemathesis.runner import Status
    from schemathesis.runner.phases.stateful import StatefulTestingPayload

    from ..schemas import BaseSchema

EventGenerator = Generator["EngineEvent", None, None]


@dataclass
class EngineEvent:
    """An event within the engine's lifecycle."""

    id: uuid.UUID
    timestamp: float
    # Indicates whether this event is the last in the event stream
    is_terminal = False


@dataclass
class PhaseEvent(EngineEvent):
    """Event associated with a specific execution phase."""

    phase: Phase


@dataclass
class PhaseStarted(PhaseEvent):
    """Start of an execution phase."""

    __slots__ = ("id", "timestamp", "phase")

    def __init__(self, *, phase: Phase) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.phase = phase


@dataclass
class PhaseFinished(PhaseEvent):
    """End of an execution phase."""

    status: Status
    payload: StatefulTestingPayload | None

    __slots__ = ("id", "timestamp", "phase", "status", "payload")

    def __init__(self, *, phase: Phase, status: Status, payload: StatefulTestingPayload | None) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.phase = phase
        self.status = status
        self.payload = payload


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
        self.status = status
        self.recorder = recorder
        self.elapsed_time = elapsed_time
        self.skip_reason = skip_reason
        self.is_final = is_final


@dataclass
class StepEvent(ScenarioEvent):
    scenario_id: uuid.UUID


@dataclass
class StepStarted(StepEvent):
    """Before executing a test case."""

    __slots__ = (
        "id",
        "timestamp",
        "phase",
        "suite_id",
        "scenario_id",
    )

    def __init__(
        self,
        *,
        phase: PhaseName,
        suite_id: uuid.UUID,
        scenario_id: uuid.UUID,
    ) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.phase = phase
        self.suite_id = suite_id
        self.scenario_id = scenario_id


@dataclass
class TransitionId:
    """Id of the the that was hit."""

    name: str
    # Status code as defined in the transition, i.e. may be `default`
    status_code: str
    source: str

    __slots__ = ("name", "status_code", "source")


@dataclass
class ResponseData:
    """Common data for responses."""

    status_code: int
    elapsed: float
    __slots__ = ("status_code", "elapsed")


@dataclass
class StepFinished(StepEvent):
    """After executing a test case."""

    status: Status | None
    transition_id: TransitionId | None
    target: str
    response: Response | None

    __slots__ = (
        "id",
        "timestamp",
        "phase",
        "status",
        "suite_id",
        "scenario_id",
        "transition_id",
        "target",
        "response",
    )

    def __init__(
        self,
        *,
        phase: PhaseName,
        id: uuid.UUID,
        suite_id: uuid.UUID,
        scenario_id: uuid.UUID,
        status: Status | None,
        transition_id: TransitionId | None,
        target: str,
        response: Response | None,
    ) -> None:
        self.id = id
        self.timestamp = time.time()
        self.phase = phase
        self.status = status
        self.suite_id = suite_id
        self.scenario_id = scenario_id
        self.transition_id = transition_id
        self.target = target
        self.response = response


@dataclass
class Initialized(EngineEvent):
    schema: dict[str, Any]
    specification: Specification
    # Total number of operations in the schema
    operations_count: int | None
    # Total number of links in the schema
    links_count: int | None
    # The place, where the API schema is located
    location: str | None
    seed: int | None
    # The base URL against which the tests are running
    base_url: str
    # The base path part of every operation
    base_path: str

    @classmethod
    def from_schema(cls, *, schema: BaseSchema, seed: int | None) -> Initialized:
        """Computes all needed data from a schema instance."""
        return cls(
            id=uuid.uuid4(),
            timestamp=time.time(),
            schema=schema.raw_schema,
            specification=schema.specification,
            operations_count=schema.operations_count,
            links_count=schema.links_count,
            location=schema.location,
            base_url=schema.get_base_url(),
            base_path=schema.base_path,
            seed=seed,
        )


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

    def __init__(self, *, error: Exception, phase: PhaseName, label: str) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.info = EngineErrorInfo(error=error)
        self.value = error
        self.phase = phase
        self.label = label


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
