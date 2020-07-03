# pylint: disable=too-many-instance-attributes
import time
from typing import Dict, List, Optional, Union

import attr
from requests import exceptions

from ..exceptions import HTTPError
from ..models import Endpoint, Status, TestResult, TestResultSet
from ..schemas import BaseSchema
from ..utils import format_exception
from .serialization import SerializedTestResult


@attr.s()  # pragma: no mutate
class ExecutionEvent:
    """Generic execution event."""


@attr.s(slots=True)  # pragma: no mutate
class Initialized(ExecutionEvent):
    """Runner is initialized, settings are prepared, requests session is ready."""

    # Total number of endpoints in the schema
    endpoints_count: int = attr.ib()  # pragma: no mutate
    location: Optional[str] = attr.ib()  # pragma: no mutate
    base_url: str = attr.ib()  # pragma: no mutate
    specification_name: str = attr.ib()  # pragma: no mutate
    # Timestamp of test run start
    start_time: float = attr.ib(factory=time.monotonic)  # pragma: no mutate

    @classmethod
    def from_schema(cls, *, schema: BaseSchema) -> "Initialized":
        """Computes all needed data from a schema instance."""
        return cls(
            endpoints_count=schema.endpoints_count,
            location=schema.location,
            base_url=schema.get_base_url(),
            specification_name=schema.verbose_name,
        )


@attr.s(slots=True)  # pragma: no mutate
class BeforeExecution(ExecutionEvent):
    """Happens before each examined endpoint.

    It happens before a single hypothesis test, that may contain many examples inside.
    """

    method: str = attr.ib()  # pragma: no mutate
    path: str = attr.ib()  # pragma: no mutate
    recursion_level: int = attr.ib()  # pragma: no mutate

    @classmethod
    def from_endpoint(cls, endpoint: Endpoint, recursion_level: int) -> "BeforeExecution":
        return cls(method=endpoint.method, path=endpoint.full_path, recursion_level=recursion_level)


@attr.s(slots=True)  # pragma: no mutate
class AfterExecution(ExecutionEvent):
    """Happens after each examined endpoint."""

    # Endpoint test status - success / failure / error
    status: Status = attr.ib()  # pragma: no mutate
    result: SerializedTestResult = attr.ib()  # pragma: no mutate
    # Test running time
    elapsed_time: float = attr.ib()  # pragma: no mutate
    # Captured hypothesis stdout
    hypothesis_output: List[str] = attr.ib(factory=list)  # pragma: no mutate

    @classmethod
    def from_result(
        cls, result: TestResult, status: Status, elapsed_time: float, hypothesis_output: List[str]
    ) -> "AfterExecution":
        return cls(
            result=SerializedTestResult.from_test_result(result),
            status=status,
            elapsed_time=elapsed_time,
            hypothesis_output=hypothesis_output,
        )


@attr.s(slots=True)  # pragma: no mutate
class Interrupted(ExecutionEvent):
    """If execution was interrupted by Ctrl-C or a received SIGTERM."""


@attr.s(slots=True)  # pragma: no mutate
class InternalError(ExecutionEvent):
    """An error that happened inside the runner."""

    message: str = attr.ib()  # pragma: no mutate
    exception_type: str = attr.ib()  # pragma: no mutate
    exception: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    exception_with_traceback: Optional[str] = attr.ib(default=None)  # pragma: no mutate

    @classmethod
    def from_exc(cls, exc: Exception) -> "InternalError":
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

    passed_count: int = attr.ib()  # pragma: no mutate
    failed_count: int = attr.ib()  # pragma: no mutate
    errored_count: int = attr.ib()  # pragma: no mutate

    has_failures: bool = attr.ib()  # pragma: no mutate
    has_errors: bool = attr.ib()  # pragma: no mutate
    has_logs: bool = attr.ib()  # pragma: no mutate
    is_empty: bool = attr.ib()  # pragma: no mutate

    total: Dict[str, Dict[Union[str, Status], int]] = attr.ib()  # pragma: no mutate

    # Total test run execution time
    running_time: float = attr.ib()  # pragma: no mutate

    @classmethod
    def from_results(cls, results: TestResultSet, running_time: float) -> "Finished":
        return cls(
            passed_count=results.passed_count,
            failed_count=results.failed_count,
            errored_count=results.errored_count,
            has_failures=results.has_failures,
            has_errors=results.has_errors,
            has_logs=results.has_logs,
            is_empty=results.is_empty,
            total=results.total,
            running_time=running_time,
        )
