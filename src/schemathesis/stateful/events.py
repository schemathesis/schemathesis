from __future__ import annotations

import time
from dataclasses import asdict as _asdict
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Type

from ..exceptions import format_exception

if TYPE_CHECKING:
    from ..models import Case, Check
    from ..transports.responses import GenericResponse
    from .state_machine import APIStateMachine


class RunStatus(str, Enum):
    """Status of the state machine run."""

    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    INTERRUPTED = "interrupted"


@dataclass
class StatefulEvent:
    """Basic stateful test event."""

    timestamp: float

    __slots__ = ("timestamp",)

    def asdict(self) -> dict[str, Any]:
        return _asdict(self)


@dataclass
class RunStarted(StatefulEvent):
    """Before executing all scenarios."""

    started_at: float
    state_machine: Type[APIStateMachine]

    __slots__ = ("state_machine", "timestamp", "started_at")

    def __init__(self, *, state_machine: Type[APIStateMachine]) -> None:
        self.state_machine = state_machine
        self.started_at = time.time()
        self.timestamp = time.monotonic()

    def asdict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "started_at": self.started_at,
        }


@dataclass
class RunFinished(StatefulEvent):
    """After executing all scenarios."""

    status: RunStatus

    __slots__ = ("timestamp", "status")

    def __init__(self, *, status: RunStatus) -> None:
        self.status = status
        self.timestamp = time.monotonic()


class SuiteStatus(str, Enum):
    """Status of the suite execution."""

    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    INTERRUPTED = "interrupted"


@dataclass
class SuiteStarted(StatefulEvent):
    """Before executing a set of scenarios."""

    __slots__ = ("timestamp",)

    def __init__(self) -> None:
        self.timestamp = time.monotonic()


@dataclass
class SuiteFinished(StatefulEvent):
    """After executing a set of scenarios."""

    status: SuiteStatus
    failures: list[Check]

    __slots__ = ("timestamp", "status", "failures")

    def __init__(self, *, status: SuiteStatus, failures: list[Check]) -> None:
        self.status = status
        self.failures = failures
        self.timestamp = time.monotonic()

    def asdict(self) -> dict[str, Any]:
        from ..runner.serialization import SerializedCheck, _serialize_check

        return {
            "timestamp": self.timestamp,
            "status": self.status,
            "failures": [_serialize_check(SerializedCheck.from_check(failure)) for failure in self.failures],
        }


class ScenarioStatus(str, Enum):
    """Status of a single scenario execution."""

    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    # Rejected by Hypothesis
    REJECTED = "rejected"
    INTERRUPTED = "interrupted"


@dataclass
class ScenarioStarted(StatefulEvent):
    """Before a single state machine execution."""

    # Whether this is a scenario that tries to reproduce a failure
    is_final: bool

    __slots__ = ("timestamp", "is_final")

    def __init__(self, *, is_final: bool) -> None:
        self.is_final = is_final
        self.timestamp = time.monotonic()


@dataclass
class ScenarioFinished(StatefulEvent):
    """After a single state machine execution."""

    status: ScenarioStatus
    # Whether this is a scenario that tries to reproduce a failure
    is_final: bool

    __slots__ = ("timestamp", "status", "is_final")

    def __init__(self, *, status: ScenarioStatus, is_final: bool) -> None:
        self.status = status
        self.is_final = is_final
        self.timestamp = time.monotonic()


class StepStatus(str, Enum):
    """Status of a single state machine step."""

    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    INTERRUPTED = "interrupted"


@dataclass
class StepStarted(StatefulEvent):
    """Before a single state machine step."""

    __slots__ = ("timestamp",)

    def __init__(self) -> None:
        self.timestamp = time.monotonic()


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
class StepFinished(StatefulEvent):
    """After a single state machine step."""

    status: StepStatus | None
    transition_id: TransitionId | None
    target: str
    case: Case
    response: GenericResponse | None
    checks: list[Check]

    __slots__ = ("timestamp", "status", "transition_id", "target", "case", "response", "checks")

    def __init__(
        self,
        *,
        status: StepStatus | None,
        transition_id: TransitionId | None,
        target: str,
        case: Case,
        response: GenericResponse | None,
        checks: list[Check],
    ) -> None:
        self.status = status
        self.transition_id = transition_id
        self.target = target
        self.case = case
        self.response = response
        self.checks = checks
        self.timestamp = time.monotonic()

    def asdict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
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
                "elapsed": self.response.elapsed.total_seconds(),
            }
            if self.response is not None
            else None,
        }


@dataclass
class Interrupted(StatefulEvent):
    """The state machine execution was interrupted."""

    __slots__ = ("timestamp",)

    def __init__(self) -> None:
        self.timestamp = time.monotonic()


@dataclass
class Errored(StatefulEvent):
    """An error occurred during the state machine execution."""

    exception: Exception

    __slots__ = ("timestamp", "exception")

    def __init__(self, *, exception: Exception) -> None:
        self.exception = exception
        self.timestamp = time.monotonic()

    def asdict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "exception": format_exception(self.exception, True),
        }
