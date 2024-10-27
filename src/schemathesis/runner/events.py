from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..exceptions import SchemaError, SchemaErrorType
from ..internal.exceptions import format_exception
from ..internal.result import Err, Ok, Result
from .errors import EngineErrorInfo

if TYPE_CHECKING:
    from ..models import APIOperation
    from ..schemas import BaseSchema, Specification
    from ..service.models import AnalysisResult
    from ..stateful import events
    from . import probes
    from .models import Status, TestResult, TestResultSet


@dataclass
class ExecutionEvent:
    """Generic execution event."""

    # Whether this event is expected to be the last one in the event stream
    is_terminal = False

    def _asdict(self) -> dict[str, Any]:
        return {}

    def asdict(self, **kwargs: Any) -> dict[str, Any]:
        data = self._asdict()
        data.update(**kwargs)
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

    @classmethod
    def from_schema(
        cls,
        *,
        schema: BaseSchema,
        count_operations: bool = True,
        count_links: bool = True,
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
            specification_name=schema.verbose_name,
            seed=seed,
        )

    def _asdict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "specification": self.specification.name,
            "operations_count": self.operations_count,
            "links_count": self.links_count,
            "location": self.location,
            "seed": self.seed,
            "base_url": self.base_url,
            "base_path": self.base_path,
            "specification_name": self.specification_name,
        }


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

    def _asdict(self) -> dict[str, Any]:
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


@dataclass
class BeforeExecution(ExecutionEvent):
    """Happens before each tested API operation.

    It happens before a single hypothesis test, that may contain many examples inside.
    """

    # Specification-specific operation name
    verbose_name: str
    # A unique ID which connects events that happen during testing of the same API operation
    # It may be useful when multiple threads are involved where incoming events are not ordered
    correlation_id: str

    @classmethod
    def from_operation(cls, operation: APIOperation, correlation_id: str) -> BeforeExecution:
        return cls(verbose_name=operation.verbose_name, correlation_id=correlation_id)

    def _asdict(self) -> dict[str, Any]:
        return {"verbose_name": self.verbose_name, "correlation_id": self.correlation_id}


@dataclass
class AfterExecution(ExecutionEvent):
    """Happens after each tested API operation."""

    # Specification-specific operation name
    verbose_name: str

    # APIOperation test status - success / failure / error
    status: Status
    result: TestResult
    # Test running time
    elapsed_time: float
    correlation_id: str
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
        correlation_id: str,
    ) -> AfterExecution:
        return cls(
            verbose_name=operation.verbose_name,
            result=result,
            status=status,
            elapsed_time=elapsed_time,
            hypothesis_output=hypothesis_output,
            correlation_id=correlation_id,
        )

    def _asdict(self) -> dict[str, Any]:
        return {
            "verbose_name": self.verbose_name,
            "status": self.status.value,
            "result": self.result.asdict(),
            "elapsed_time": self.elapsed_time,
            "correlation_id": self.correlation_id,
            "hypothesis_output": self.hypothesis_output,
        }


@dataclass
class Interrupted(ExecutionEvent):
    """If execution was interrupted by Ctrl-C, or a received SIGTERM."""


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
        exception_with_traceback = format_exception(exc, with_traceback=True)
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

    def _asdict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "subtype": self.subtype.value if self.subtype else None,
            "title": self.title,
            "message": self.message,
            "extras": self.extras,
            "exception_type": self.exception_type,
            "exception": self.exception,
            "exception_with_traceback": self.exception_with_traceback,
        }


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
    result: TestResult
    elapsed_time: float

    def _asdict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "result": self.result.asdict(),
            "elapsed_time": self.elapsed_time,
        }


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
    generic_errors: list[EngineErrorInfo]
    warnings: list[str]

    total: dict[str, dict[str | Status, int]]

    # Total test run execution time
    running_time: float

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
            generic_errors=[EngineErrorInfo(error, title=error.full_path) for error in results.generic_errors],
            warnings=results.warnings,
            running_time=running_time,
        )

    def _asdict(self) -> dict[str, Any]:
        return {
            "passed_count": self.passed_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "errored_count": self.errored_count,
            "has_failures": self.has_failures,
            "has_errors": self.has_errors,
            "has_logs": self.has_logs,
            "is_empty": self.is_empty,
            "generic_errors": [error.asdict() for error in self.generic_errors],
            "warnings": self.warnings,
            "total": self.total,
            "running_time": self.running_time,
        }
