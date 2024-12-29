from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generator

from schemathesis.core.errors import format_exception
from schemathesis.core.transport import Response
from schemathesis.generation.case import Case
from schemathesis.runner.models.check import Check
from schemathesis.runner.phases import Phase, PhaseName

if TYPE_CHECKING:
    from schemathesis.core import Specification
    from schemathesis.runner import Status
    from schemathesis.runner.phases.probes import ProbingPayload
    from schemathesis.runner.phases.stateful import StatefulTestingPayload

    from ..schemas import BaseSchema
    from .models import TestResult, TestResultSet

EventGenerator = Generator["EngineEvent", None, None]


@dataclass
class EngineEvent:
    """An event within the engine's lifecycle."""

    id: uuid.UUID
    timestamp: float
    # Indicates whether this event is the last in the event stream
    is_terminal = False

    def _asdict(self) -> dict[str, Any]:
        return {}

    def asdict(self, **kwargs: Any) -> dict[str, Any]:
        data = self._asdict()
        data["id"] = self.id.hex
        data["timestamp"] = self.timestamp
        data.update(**kwargs)
        return {self.__class__.__name__: data}


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

    def _asdict(self) -> dict[str, Any]:
        return {"phase": self.phase.asdict()}


@dataclass
class PhaseFinished(PhaseEvent):
    """End of an execution phase."""

    status: Status
    payload: ProbingPayload | StatefulTestingPayload | None

    __slots__ = ("id", "timestamp", "phase", "status", "payload")

    def __init__(
        self, *, phase: Phase, status: Status, payload: ProbingPayload | StatefulTestingPayload | None
    ) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.phase = phase
        self.status = status
        self.payload = payload

    def _asdict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "phase": self.phase.asdict(),
            "status": self.status.name,
        }
        if self.payload is None:
            data["payload"] = None
        else:
            data["payload"] = self.payload.asdict()
        return data


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

    def _asdict(self) -> dict[str, Any]:
        return {"phase": self.phase.name}


@dataclass
class SuiteFinished(TestEvent):
    """After executing a set of test scenarios."""

    status: Status
    failures: list[Check]

    __slots__ = ("id", "timestamp", "phase", "status", "failures")

    def __init__(self, *, phase: PhaseName, status: Status, failures: list[Check]) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.phase = phase
        self.status = status
        self.failures = failures

    def _asdict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.name,
            "status": self.status,
            "failures": [failure.asdict() for failure in self.failures],
        }


@dataclass
class ScenarioEvent(TestEvent):
    suite_id: uuid.UUID


@dataclass
class ScenarioStarted(ScenarioEvent):
    """Before executing a grouped set of test steps."""

    # Whether this is a scenario that tries to reproduce a failure
    is_final: bool

    __slots__ = ("id", "timestamp", "phase", "suite_id", "is_final")

    def __init__(self, *, phase: PhaseName, suite_id: uuid.UUID, is_final: bool) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.phase = phase
        self.suite_id = suite_id
        self.is_final = is_final

    def _asdict(self) -> dict[str, Any]:
        return {"phase": self.phase.name, "is_final": self.is_final}


@dataclass
class ScenarioFinished(ScenarioEvent):
    """After executing a grouped set of test steps."""

    status: Status | None
    # Whether this is a scenario that tries to reproduce a failure
    is_final: bool

    __slots__ = ("id", "timestamp", "phase", "suite_id", "status", "is_final")

    def __init__(self, *, phase: PhaseName, suite_id: uuid.UUID, status: Status | None, is_final: bool) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.phase = phase
        self.suite_id = suite_id
        self.status = status
        self.is_final = is_final

    def _asdict(self) -> dict[str, Any]:
        return {"is_final": self.is_final, "status": self.status}


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
    case: Case
    response: Response | None
    checks: list[Check]

    __slots__ = (
        "id",
        "timestamp",
        "phase",
        "status",
        "suite_id",
        "scenario_id",
        "transition_id",
        "target",
        "case",
        "response",
        "checks",
    )

    def __init__(
        self,
        *,
        phase: PhaseName,
        status: Status | None,
        suite_id: uuid.UUID,
        scenario_id: uuid.UUID,
        transition_id: TransitionId | None,
        target: str,
        case: Case,
        response: Response | None,
        checks: list[Check],
    ) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.phase = phase
        self.status = status
        self.suite_id = suite_id
        self.scenario_id = scenario_id
        self.transition_id = transition_id
        self.target = target
        self.case = case
        self.response = response
        self.checks = checks

    def _asdict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.name,
            "status": self.status,
            "transition_id": {
                "name": self.transition_id.name,
                "status_code": self.transition_id.status_code,
                "source": self.transition_id.source,
            }
            if self.transition_id is not None
            else None,
            "target": self.target,
            "response": {
                "status_code": self.response.status_code,
                "elapsed": self.response.elapsed,
            }
            if self.response is not None
            else None,
        }


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

    def _asdict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "specification": self.specification.asdict(),
            "operations_count": self.operations_count,
            "links_count": self.links_count,
            "location": self.location,
            "seed": self.seed,
            "base_url": self.base_url,
            "base_path": self.base_path,
        }


@dataclass
class BeforeExecution(EngineEvent):
    """Happens before each tested API operation.

    It happens before a single hypothesis test, that may contain many examples inside.
    """

    # Specification-specific operation name
    label: str
    correlation_id: uuid.UUID

    __slots__ = ("id", "timestamp", "label", "correlation_id")

    def __init__(self, *, label: str, correlation_id: uuid.UUID) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.label = label
        self.correlation_id = correlation_id

    def _asdict(self) -> dict[str, Any]:
        return {"label": self.label, "correlation_id": self.correlation_id.hex}


@dataclass
class AfterExecution(EngineEvent):
    """Happens after each tested API operation."""

    status: Status
    result: TestResult
    elapsed_time: float
    correlation_id: uuid.UUID

    def __init__(self, *, status: Status, result: TestResult, elapsed_time: float, correlation_id: uuid.UUID) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.status = status
        self.result = result
        self.elapsed_time = elapsed_time
        self.correlation_id = correlation_id

    def _asdict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "result": self.result.asdict(),
            "elapsed_time": self.elapsed_time,
            "correlation_id": self.correlation_id.hex,
        }


@dataclass
class Interrupted(EngineEvent):
    """If execution was interrupted by Ctrl-C, or a received SIGTERM."""

    phase: PhaseName | None

    __slots__ = ("id", "timestamp", "phase")

    def __init__(self, *, phase: PhaseName | None) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.phase = phase

    def _asdict(self) -> dict[str, Any]:
        return {"phase": self.phase.name if self.phase is not None else None}


@dataclass
class InternalError(EngineEvent):
    """Internal error in the engine."""

    exception: Exception
    is_terminal = True

    __slots__ = ("id", "timestamp", "exception")

    def __init__(self, *, exception: Exception) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.exception = exception

    def _asdict(self) -> dict[str, Any]:
        return {"exception": format_exception(self.exception, with_traceback=True)}


@dataclass
class EngineFinished(EngineEvent):
    """The final event of the run.

    No more events after this point.
    """

    is_terminal = True
    results: TestResultSet
    running_time: float

    __slots__ = ("id", "timestamp", "results", "running_time")

    def __init__(self, *, results: TestResultSet, running_time: float) -> None:
        self.id = uuid.uuid4()
        self.timestamp = time.time()
        self.results = results
        self.running_time = running_time

    def _asdict(self) -> dict[str, Any]:
        return {
            "results": self.results.asdict(),
            "running_time": self.running_time,
        }
