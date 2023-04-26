import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Union

from requests import exceptions

from ..constants import USE_WAIT_FOR_SCHEMA_SUGGESTION_MESSAGE, DataGenerationMethod
from ..exceptions import HTTPError
from ..models import APIOperation, Status, TestResult, TestResultSet
from ..schemas import BaseSchema
from ..utils import current_datetime, format_exception
from .serialization import SerializedError, SerializedTestResult


@dataclass
class ExecutionEvent:
    """Generic execution event."""

    # Whether this event is expected to be the last one in the event stream
    is_terminal = False

    def asdict(self, **kwargs: Any) -> Dict[str, Any]:
        data = asdict(self, **kwargs)
        # An internal tag for simpler type identification
        data["event_type"] = self.__class__.__name__
        return data


@dataclass
class Initialized(ExecutionEvent):
    """Runner is initialized, settings are prepared, requests session is ready."""

    schema: Dict[str, Any]
    # Total number of operations in the schema
    operations_count: Optional[int]
    # The place, where the API schema is located
    location: Optional[str]
    # The base URL against which the tests are running
    base_url: str
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
        cls, *, schema: BaseSchema, count_operations: bool = True, started_at: Optional[str] = None
    ) -> "Initialized":
        """Computes all needed data from a schema instance."""
        return cls(
            schema=schema.raw_schema,
            operations_count=schema.operations_count if count_operations else None,
            location=schema.location,
            base_url=schema.get_base_url(),
            started_at=started_at or current_datetime(),
            specification_name=schema.verbose_name,
        )


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
    data_generation_method: List[DataGenerationMethod]
    # A unique ID which connects events that happen during testing of the same API operation
    # It may be useful when multiple threads are involved where incoming events are not ordered
    correlation_id: str
    thread_id: int = field(default_factory=threading.get_ident)

    @classmethod
    def from_operation(
        cls,
        operation: APIOperation,
        recursion_level: int,
        data_generation_method: List[DataGenerationMethod],
        correlation_id: str,
    ) -> "BeforeExecution":
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
    data_generation_method: List[DataGenerationMethod]
    result: SerializedTestResult
    # Test running time
    elapsed_time: float
    correlation_id: str
    thread_id: int = field(default_factory=threading.get_ident)
    # Captured hypothesis stdout
    hypothesis_output: List[str] = field(default_factory=list)

    @classmethod
    def from_result(
        cls,
        result: TestResult,
        status: Status,
        elapsed_time: float,
        hypothesis_output: List[str],
        operation: APIOperation,
        data_generation_method: List[DataGenerationMethod],
        correlation_id: str,
    ) -> "AfterExecution":
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


@dataclass
class InternalError(ExecutionEvent):
    """An error that happened inside the runner."""

    is_terminal = True

    message: str
    exception_type: str
    exception: Optional[str] = None
    exception_with_traceback: Optional[str] = None
    thread_id: int = field(default_factory=threading.get_ident)

    @classmethod
    def from_exc(cls, exc: Exception, wait_for_schema: Optional[float] = None) -> "InternalError":
        exception_type = f"{exc.__class__.__module__}.{exc.__class__.__qualname__}"
        if isinstance(exc, HTTPError):
            if exc.response.status_code == 404:
                message = f"Schema was not found at {exc.url}"
            else:
                message = f"Failed to load schema, code {exc.response.status_code} was returned from {exc.url}"
            return cls(message=message, exception_type=exception_type)
        exception = format_exception(exc)
        exception_with_traceback = format_exception(exc, include_traceback=True)
        if isinstance(exc, exceptions.ConnectionError):
            message = f"Failed to load schema from {exc.request.url}"
            if wait_for_schema is None:
                message += f"\n{USE_WAIT_FOR_SCHEMA_SUGGESTION_MESSAGE}"
        else:
            message = "An internal error happened during a test run"
        return cls(
            message=message,
            exception_type=exception_type,
            exception=exception,
            exception_with_traceback=exception_with_traceback,
        )


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
    generic_errors: List[SerializedError]
    warnings: List[str]

    total: Dict[str, Dict[Union[str, Status], int]]

    # Total test run execution time
    running_time: float
    thread_id: int = field(default_factory=threading.get_ident)

    @classmethod
    def from_results(cls, results: TestResultSet, running_time: float) -> "Finished":
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
                SerializedError.from_error(error, None, None, error.full_path) for error in results.generic_errors
            ],
            warnings=results.warnings,
            running_time=running_time,
        )
