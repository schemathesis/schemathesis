import threading
import time
from typing import Any, Dict, List, Optional, Union

import attr
from requests import exceptions

from ..constants import USE_WAIT_FOR_SCHEMA_SUGGESTION_MESSAGE, DataGenerationMethod
from ..exceptions import HTTPError
from ..models import APIOperation, Status, TestResult, TestResultSet
from ..schemas import BaseSchema
from ..utils import current_datetime, format_exception
from .serialization import SerializedError, SerializedTestResult


@attr.s()  # pragma: no mutate
class ExecutionEvent:
    """Generic execution event."""

    # Whether this event is expected to be the last one in the event stream
    is_terminal = False

    def asdict(self, **kwargs: Any) -> Dict[str, Any]:
        data = attr.asdict(self, **kwargs)
        # An internal tag for simpler type identification
        data["event_type"] = self.__class__.__name__
        return data


@attr.s(slots=True)  # pragma: no mutate
class Initialized(ExecutionEvent):
    """Runner is initialized, settings are prepared, requests session is ready."""

    schema: Dict[str, Any] = attr.ib()  # pragma: no mutate
    # Total number of operations in the schema
    operations_count: Optional[int] = attr.ib()  # pragma: no mutate
    # The place, where the API schema is located
    location: Optional[str] = attr.ib()  # pragma: no mutate
    # The base URL against which the tests are running
    base_url: str = attr.ib()  # pragma: no mutate
    # API schema specification name
    specification_name: str = attr.ib()  # pragma: no mutate
    # Monotonic clock value when the test run started. Used to properly calculate run duration, since this clock
    # can't go backwards.
    start_time: float = attr.ib(factory=time.monotonic)  # pragma: no mutate
    # Datetime of the test run start
    started_at: str = attr.ib(factory=current_datetime)  # pragma: no mutate
    thread_id: int = attr.ib(factory=threading.get_ident)  # pragma: no mutate

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


@attr.s(slots=True)  # pragma: no mutate
class BeforeExecution(CurrentOperationMixin, ExecutionEvent):
    """Happens before each tested API operation.

    It happens before a single hypothesis test, that may contain many examples inside.
    """

    # HTTP method
    method: str = attr.ib()  # pragma: no mutate
    # Full path, including the base path
    path: str = attr.ib()  # pragma: no mutate
    # Specification-specific operation name
    verbose_name: str = attr.ib()  # pragma: no mutate
    # Path without the base path
    relative_path: str = attr.ib()  # pragma: no mutate
    # The current level of recursion during stateful testing
    recursion_level: int = attr.ib()  # pragma: no mutate
    # The way data will be generated
    data_generation_method: List[DataGenerationMethod] = attr.ib()  # pragma: no mutate
    # A unique ID which connects events that happen during testing of the same API operation
    # It may be useful when multiple threads are involved where incoming events are not ordered
    correlation_id: str = attr.ib()  # pragma: no mutate
    thread_id: int = attr.ib(factory=threading.get_ident)  # pragma: no mutate

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


@attr.s(slots=True)  # pragma: no mutate
class AfterExecution(CurrentOperationMixin, ExecutionEvent):
    """Happens after each tested API operation."""

    method: str = attr.ib()  # pragma: no mutate
    path: str = attr.ib()  # pragma: no mutate
    relative_path: str = attr.ib()  # pragma: no mutate
    # Specification-specific operation name
    verbose_name: str = attr.ib()  # pragma: no mutate

    # APIOperation test status - success / failure / error
    status: Status = attr.ib()  # pragma: no mutate
    # The way data was generated
    data_generation_method: List[DataGenerationMethod] = attr.ib()  # pragma: no mutate
    result: SerializedTestResult = attr.ib()  # pragma: no mutate
    # Test running time
    elapsed_time: float = attr.ib()  # pragma: no mutate
    correlation_id: str = attr.ib()  # pragma: no mutate
    thread_id: int = attr.ib(factory=threading.get_ident)  # pragma: no mutate
    # Captured hypothesis stdout
    hypothesis_output: List[str] = attr.ib(factory=list)  # pragma: no mutate

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


@attr.s(slots=True)  # pragma: no mutate
class Interrupted(ExecutionEvent):
    """If execution was interrupted by Ctrl-C, or a received SIGTERM."""

    thread_id: int = attr.ib(factory=threading.get_ident)  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class InternalError(ExecutionEvent):
    """An error that happened inside the runner."""

    is_terminal = True

    message: str = attr.ib()  # pragma: no mutate
    exception_type: str = attr.ib()  # pragma: no mutate
    exception: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    exception_with_traceback: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    thread_id: int = attr.ib(factory=threading.get_ident)  # pragma: no mutate

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


@attr.s(slots=True)  # pragma: no mutate
class Finished(ExecutionEvent):
    """The final event of the run.

    No more events after this point.
    """

    is_terminal = True

    passed_count: int = attr.ib()  # pragma: no mutate
    skipped_count: int = attr.ib()  # pragma: no mutate
    failed_count: int = attr.ib()  # pragma: no mutate
    errored_count: int = attr.ib()  # pragma: no mutate

    has_failures: bool = attr.ib()  # pragma: no mutate
    has_errors: bool = attr.ib()  # pragma: no mutate
    has_logs: bool = attr.ib()  # pragma: no mutate
    is_empty: bool = attr.ib()  # pragma: no mutate
    generic_errors: List[SerializedError] = attr.ib()  # pragma: no mutate
    warnings: List[str] = attr.ib()  # pragma: no mutate

    total: Dict[str, Dict[Union[str, Status], int]] = attr.ib()  # pragma: no mutate

    # Total test run execution time
    running_time: float = attr.ib()  # pragma: no mutate
    thread_id: int = attr.ib(factory=threading.get_ident)  # pragma: no mutate

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
