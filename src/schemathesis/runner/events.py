from __future__ import annotations

import enum
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from ..exceptions import RuntimeErrorType, SchemaError, SchemaErrorType, format_exception
from ..generation import DataGenerationMethod
from ..internal.datetime import current_datetime
from ..internal.result import Err, Ok, Result
from .serialization import SerializedError, SerializedTestResult

if TYPE_CHECKING:
    from ..models import APIOperation, Status, TestResult, TestResultSet
    from ..schemas import BaseSchema, Specification
    from ..service.models import AnalysisResult
    from ..stateful import events
    from . import probes


@dataclass
class ExecutionEvent:
    """Generic execution event."""

    # Whether this event is expected to be the last one in the event stream
    is_terminal = False

    def asdict(self, **kwargs: Any) -> dict[str, Any]:
        data = asdict(self, **kwargs)
        # An internal tag for simpler type identification
        data["event_type"] = self.__class__.__name__
        return data


@dataclass
class Initialized(ExecutionEvent):
    """Runner is initialized, settings are prepared, requests session is ready."""

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
    # API schema specification name
    specification_name: str
    # Monotonic clock value when the test run started. Used to properly calculate run duration, since this clock
    # can't go backwards.
    start_time: float = field(default_factory=time.monotonic)
    # Datetime of the test run start
    started_at: str = field(default_factory=current_datetime)
    thread_id: int = field(default_factory=threading.get_ident)

    @classmethod
    def from_schema(
        cls,
        *,
        schema: BaseSchema,
        count_operations: bool = True,
        count_links: bool = True,
        start_time: float | None = None,
        started_at: str | None = None,
        seed: int | None,
    ) -> Initialized:
        """Computes all needed data from a schema instance."""
        return cls(
            schema=schema.raw_schema,
            specification=schema.specification,
            operations_count=schema.operations_count if count_operations else None,
            links_count=schema.links_count if count_links else None,
            location=schema.location,
            base_url=schema.get_base_url(),
            base_path=schema.base_path,
            start_time=start_time or time.monotonic(),
            started_at=started_at or current_datetime(),
            specification_name=schema.verbose_name,
            seed=seed,
        )


@dataclass
class BeforeProbing(ExecutionEvent):
    pass


@dataclass
class AfterProbing(ExecutionEvent):
    probes: list[probes.ProbeRun] | None

    def asdict(self, **kwargs: Any) -> dict[str, Any]:
        probes = self.probes or []
        return {"probes": [probe.serialize() for probe in probes], "events_type": self.__class__.__name__}


@dataclass
class BeforeAnalysis(ExecutionEvent):
    pass


@dataclass
class AfterAnalysis(ExecutionEvent):
    analysis: Result[AnalysisResult, Exception] | None

    def _serialize(self) -> dict[str, Any]:
        from ..service.models import AnalysisSuccess

        data = {}
        if isinstance(self.analysis, Ok):
            result = self.analysis.ok()
            if isinstance(result, AnalysisSuccess):
                data["analysis_id"] = result.id
            else:
                data["error"] = result.message
        elif isinstance(self.analysis, Err):
            data["error"] = format_exception(self.analysis.err())
        return data

    def asdict(self, **kwargs: Any) -> dict[str, Any]:
        data = self._serialize()
        data["event_type"] = self.__class__.__name__
        return data


class CurrentOperationMixin:
    method: str
    path: str

    @property
    def current_operation(self) -> str:
        return f"{self.method} {self.path}"


@dataclass
class BeforeExecution(CurrentOperationMixin, ExecutionEvent):
    """Happens before each tested API operation.

    It happens before a single hypothesis test, that may contain many examples inside.
    """

    # HTTP method
    method: str
    # Full path, including the base path
    path: str
    # Specification-specific operation name
    verbose_name: str
    # Path without the base path
    relative_path: str
    # The current level of recursion during stateful testing
    recursion_level: int
    # The way data will be generated
    data_generation_method: list[DataGenerationMethod]
    # A unique ID which connects events that happen during testing of the same API operation
    # It may be useful when multiple threads are involved where incoming events are not ordered
    correlation_id: str
    thread_id: int = field(default_factory=threading.get_ident)

    @classmethod
    def from_operation(
        cls,
        operation: APIOperation,
        recursion_level: int,
        data_generation_method: list[DataGenerationMethod],
        correlation_id: str,
    ) -> BeforeExecution:
        return cls(
            method=operation.method.upper(),
            path=operation.full_path,
            verbose_name=operation.verbose_name,
            relative_path=operation.path,
            recursion_level=recursion_level,
            data_generation_method=data_generation_method,
            correlation_id=correlation_id,
        )


@dataclass
class AfterExecution(CurrentOperationMixin, ExecutionEvent):
    """Happens after each tested API operation."""

    method: str
    path: str
    relative_path: str
    # Specification-specific operation name
    verbose_name: str

    # APIOperation test status - success / failure / error
    status: Status
    # The way data was generated
    data_generation_method: list[DataGenerationMethod]
    result: SerializedTestResult
    # Test running time
    elapsed_time: float
    correlation_id: str
    thread_id: int = field(default_factory=threading.get_ident)
    # Captured hypothesis stdout
    hypothesis_output: list[str] = field(default_factory=list)

    @classmethod
    def from_result(
        cls,
        result: TestResult,
        status: Status,
        elapsed_time: float,
        hypothesis_output: list[str],
        operation: APIOperation,
        data_generation_method: list[DataGenerationMethod],
        correlation_id: str,
    ) -> AfterExecution:
        return cls(
            method=operation.method.upper(),
            path=operation.full_path,
            relative_path=operation.path,
            verbose_name=operation.verbose_name,
            result=SerializedTestResult.from_test_result(result),
            status=status,
            elapsed_time=elapsed_time,
            hypothesis_output=hypothesis_output,
            data_generation_method=data_generation_method,
            correlation_id=correlation_id,
        )


@dataclass
class Interrupted(ExecutionEvent):
    """If execution was interrupted by Ctrl-C, or a received SIGTERM."""

    thread_id: int = field(default_factory=threading.get_ident)


@enum.unique
class InternalErrorType(str, enum.Enum):
    SCHEMA = "schema"
    OTHER = "other"


DEFAULT_INTERNAL_ERROR_MESSAGE = "An internal error occurred during the test run"


@dataclass
class InternalError(ExecutionEvent):
    """An error that happened inside the runner."""

    is_terminal = True

    # Main error info
    type: InternalErrorType
    subtype: SchemaErrorType | None
    title: str
    message: str
    extras: list[str]

    # Exception info
    exception_type: str
    exception: str
    exception_with_traceback: str
    # Auxiliary data
    thread_id: int = field(default_factory=threading.get_ident)

    @classmethod
    def from_schema_error(cls, error: SchemaError) -> InternalError:
        return cls.with_exception(
            error,
            type_=InternalErrorType.SCHEMA,
            subtype=error.type,
            title="Schema Loading Error",
            message=error.message,
            extras=error.extras,
        )

    @classmethod
    def from_exc(cls, exc: Exception) -> InternalError:
        return cls.with_exception(
            exc,
            type_=InternalErrorType.OTHER,
            subtype=None,
            title="Test Execution Error",
            message=DEFAULT_INTERNAL_ERROR_MESSAGE,
            extras=[],
        )

    @classmethod
    def with_exception(
        cls,
        exc: Exception,
        type_: InternalErrorType,
        subtype: SchemaErrorType | None,
        title: str,
        message: str,
        extras: list[str],
    ) -> InternalError:
        exception_type = f"{exc.__class__.__module__}.{exc.__class__.__qualname__}"
        exception = format_exception(exc)
        exception_with_traceback = format_exception(exc, include_traceback=True)
        return cls(
            type=type_,
            subtype=subtype,
            title=title,
            message=message,
            extras=extras,
            exception_type=exception_type,
            exception=exception,
            exception_with_traceback=exception_with_traceback,
        )


@dataclass
class StatefulEvent(ExecutionEvent):
    """Represents an event originating from the state machine runner."""

    data: events.StatefulEvent

    __slots__ = ("data",)

    def asdict(self, **kwargs: Any) -> dict[str, Any]:
        return {"data": self.data.asdict(**kwargs), "event_type": self.__class__.__name__}


@dataclass
class AfterStatefulExecution(ExecutionEvent):
    """Happens after the stateful test run."""

    status: Status
    result: SerializedTestResult
    elapsed_time: float
    data_generation_method: list[DataGenerationMethod]
    thread_id: int = field(default_factory=threading.get_ident)


@dataclass
class Finished(ExecutionEvent):
    """The final event of the run.

    No more events after this point.
    """

    is_terminal = True

    passed_count: int
    skipped_count: int
    failed_count: int
    errored_count: int

    has_failures: bool
    has_errors: bool
    has_logs: bool
    is_empty: bool
    generic_errors: list[SerializedError]
    warnings: list[str]

    total: dict[str, dict[str | Status, int]]

    # Total test run execution time
    running_time: float
    thread_id: int = field(default_factory=threading.get_ident)

    @classmethod
    def from_results(cls, results: TestResultSet, running_time: float) -> Finished:
        return cls(
            passed_count=results.passed_count,
            skipped_count=results.skipped_count,
            failed_count=results.failed_count,
            errored_count=results.errored_count,
            has_failures=results.has_failures,
            has_errors=results.has_errors,
            has_logs=results.has_logs,
            is_empty=results.is_empty,
            total=results.total,
            generic_errors=[
                SerializedError.with_exception(
                    type_=RuntimeErrorType.SCHEMA_GENERIC,
                    exception=error,
                    title=error.full_path,
                    message=error.message,
                    extras=[],
                )
                for error in results.generic_errors
            ],
            warnings=results.warnings,
            running_time=running_time,
        )
