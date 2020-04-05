# pylint: disable=too-many-instance-attributes
import time
from typing import Dict, List, Optional, Union

import attr
from requests import exceptions

from ..exceptions import HTTPError
from ..models import Endpoint, Status, TestResultSet
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
    location: Optional[str] = attr.ib()
    base_url: Optional[str] = attr.ib()
    specification_name: str = attr.ib()
    # Timestamp of test run start
    start_time: float = attr.ib(factory=time.monotonic)

    @classmethod
    def from_schema(cls, *, schema: BaseSchema) -> "Initialized":
        """Computes all needed data from the schema instance."""
        return cls(
            endpoints_count=schema.endpoints_count,
            location=schema.location,
            base_url=schema.base_url,
            specification_name=schema.verbose_name,
        )


@attr.s(slots=True)  # pragma: no mutate
class BeforeExecution(ExecutionEvent):
    """Happens before each examined endpoint.

    It happens before a single hypothesis test, that may contain many examples inside.
    """

    method: str = attr.ib()  # pragma: no mutate
    path: str = attr.ib()  # pragma: no mutate

    @classmethod
    def from_endpoint(cls, endpoint: Endpoint) -> "BeforeExecution":
        return cls(method=endpoint.method, path=endpoint.path)


@attr.s(slots=True)  # pragma: no mutate
class AfterExecution(ExecutionEvent):
    """Happens after each examined endpoint."""

    # Endpoint test status - success / failure / error
    status: Status = attr.ib()  # pragma: no mutate
    # Captured hypothesis stdout
    hypothesis_output: List[str] = attr.ib(factory=list)  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class Interrupted(ExecutionEvent):
    """If execution was interrupted by Ctrl-C or a received SIGTERM."""


@attr.s(slots=True)  # pragma: no mutate
class InternalError(ExecutionEvent):
    """An error that happened inside the runner."""

    message: str = attr.ib()
    exception: Optional[str] = attr.ib(default=None)

    @classmethod
    def from_exc(cls, exc: Exception) -> "InternalError":
        if isinstance(exc, HTTPError):
            if exc.response.status_code == 404:
                message = f"Schema was not found at {exc.url}"
            else:
                message = f"Failed to load schema, code {exc.response.status_code} was returned from {exc.url}"
            return cls(message=message)
        if isinstance(exc, exceptions.ConnectionError):
            return cls(message=f"Failed to load schema from {exc.request.url}", exception=format_exception(exc),)
        return cls(message="An internal error happened during a test run", exception=format_exception(exc),)


@attr.s(slots=True)  # pragma: no mutate
class Finished(ExecutionEvent):
    """The final event of the run.

    No more events after this point.
    """

    # Holder for all tests results in a particular run
    results: List[SerializedTestResult] = attr.ib()  # pragma: no mutate

    passed_count: int = attr.ib()
    failed_count: int = attr.ib()
    errored_count: int = attr.ib()

    has_failures: bool = attr.ib()
    has_errors: bool = attr.ib()
    has_logs: bool = attr.ib()
    is_empty: bool = attr.ib()

    total: Dict[str, Dict[Union[str, Status], int]] = attr.ib()

    # Total test run execution time
    running_time: float = attr.ib()

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
            results=[SerializedTestResult.from_test_result(result) for result in results],
            running_time=running_time,
        )
