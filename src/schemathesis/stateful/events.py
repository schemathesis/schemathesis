from __future__ import annotations

from enum import Enum
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import Check


class RunStatus(str, Enum):
    """Status of the state machine run."""

    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    INTERRUPTED = "interrupted"


class StatefulEvent:
    """Basic stateful test event."""


@dataclass
class BeforeRun(StatefulEvent):
    """Before executing all scenarios."""

    __slots__ = ("timestamp",)

    def __init__(self) -> None:
        self.timestamp = time.monotonic()


@dataclass
class AfterRun(StatefulEvent):
    """After executing all scenarios."""

    status: RunStatus

    __slots__ = ("timestamp", "status")

    def __init__(self, status: RunStatus) -> None:
        self.status = status
        self.timestamp = time.monotonic()


class SuiteStatus(str, Enum):
    """Status of the suite execution."""

    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    INTERRUPTED = "interrupted"


@dataclass
class BeforeSuite(StatefulEvent):
    """Before executing a set of scenarios."""

    __slots__ = ("timestamp",)

    def __init__(self) -> None:
        self.timestamp = time.monotonic()


@dataclass
class AfterSuite(StatefulEvent):
    """After executing a set of scenarios."""

    status: SuiteStatus
    failures: list[Check]

    __slots__ = ("timestamp", "status", "failures")

    def __init__(self, status: SuiteStatus, failures: list[Check]) -> None:
        self.status = status
        self.failures = failures
        self.timestamp = time.monotonic()


class ScenarioStatus(str, Enum):
    """Status of a single scenario execution."""

    SUCCESS = "success"
    FAILURE = "failure"
    # TODO: Count for Hypothesis' rejected?
    ERROR = "error"


@dataclass
class BeforeScenario(StatefulEvent):
    """Before a single state machine execution."""

    __slots__ = ("timestamp",)

    def __init__(self) -> None:
        self.timestamp = time.monotonic()


@dataclass
class AfterScenario(StatefulEvent):
    """After a single state machine execution."""

    status: ScenarioStatus

    __slots__ = ("timestamp", "status")

    def __init__(self, status: ScenarioStatus) -> None:
        self.status = status
        self.timestamp = time.monotonic()


class StepStatus(str, Enum):
    """Status of a single state machine step."""

    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"


@dataclass
class BeforeStep(StatefulEvent):
    """Before a single state machine step."""

    __slots__ = ("timestamp",)

    def __init__(self) -> None:
        self.timestamp = time.monotonic()


@dataclass
class AfterStep(StatefulEvent):
    """After a single state machine step."""

    status: StepStatus

    __slots__ = ("timestamp", "status")

    def __init__(self, status: StepStatus) -> None:
        self.status = status
        self.timestamp = time.monotonic()


@dataclass
class Interrupted(StatefulEvent):
    """The state machine execution was interrupted."""

    __slots__ = ("timestamp",)

    def __init__(self) -> None:
        self.timestamp = time.monotonic()


@dataclass
class Error(StatefulEvent):
    """An error occurred during the state machine execution."""

    exception: Exception

    __slots__ = ("timestamp", "exception")

    def __init__(self, exception: Exception) -> None:
        self.exception = exception
        self.timestamp = time.monotonic()
