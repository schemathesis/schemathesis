import logging
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Generator, Iterable, List, Optional, Union

import attr
import hypothesis
import requests
from _pytest.logging import LogCaptureHandler, catching_logs
from requests.auth import HTTPDigestAuth, _basic_auth_str

from ...constants import USER_AGENT
from ...exceptions import InvalidSchema, get_grouped_exception
from ...models import Case, CheckFunction, Endpoint, Status, TestResult, TestResultSet
from ...runner import events
from ...schemas import BaseSchema
from ...types import RawAuth
from ...utils import GenericResponse, capture_hypothesis_output

DEFAULT_DEADLINE = 500  # pragma: no mutate


def get_hypothesis_settings(hypothesis_options: Dict[str, Any]) -> hypothesis.settings:
    # Default settings, used as a parent settings object below
    hypothesis_options.setdefault("deadline", DEFAULT_DEADLINE)
    return hypothesis.settings(**hypothesis_options)


# pylint: disable=too-many-instance-attributes
@attr.s  # pragma: no mutate
class BaseRunner:
    schema: BaseSchema = attr.ib()  # pragma: no mutate
    checks: Iterable[CheckFunction] = attr.ib()  # pragma: no mutate
    hypothesis_settings: hypothesis.settings = attr.ib(converter=get_hypothesis_settings)  # pragma: no mutate
    auth: Optional[RawAuth] = attr.ib(default=None)  # pragma: no mutate
    auth_type: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    headers: Optional[Dict[str, Any]] = attr.ib(default=None)  # pragma: no mutate
    request_timeout: Optional[int] = attr.ib(default=None)  # pragma: no mutate
    seed: Optional[int] = attr.ib(default=None)  # pragma: no mutate
    exit_first: bool = attr.ib(default=False)  # pragma: no mutate

    def execute(self) -> Generator[events.ExecutionEvent, None, None]:
        """Common logic for all runners."""
        results = TestResultSet()

        initialized = events.Initialized.from_schema(schema=self.schema)
        yield initialized

        for event in self._execute(results):
            if (
                self.exit_first
                and isinstance(event, events.AfterExecution)
                and event.status in (Status.error, Status.failure)
            ):
                break
            yield event

        yield events.Finished.from_results(results=results, running_time=time.monotonic() - initialized.start_time)

    def _execute(self, results: TestResultSet) -> Generator[events.ExecutionEvent, None, None]:
        raise NotImplementedError


def run_test(
    endpoint: Endpoint,
    test: Union[Callable, InvalidSchema],
    checks: Iterable[CheckFunction],
    results: TestResultSet,
    **kwargs: Any,
) -> Generator[events.ExecutionEvent, None, None]:
    """A single test run with all error handling needed."""
    # pylint: disable=too-many-arguments
    result = TestResult(endpoint=endpoint)
    yield events.BeforeExecution.from_endpoint(endpoint=endpoint)
    hypothesis_output: List[str] = []
    try:
        if isinstance(test, InvalidSchema):
            status = Status.error
            result.add_error(test)
        else:
            with capture_hypothesis_output() as hypothesis_output:
                test(checks, result, **kwargs)
            status = Status.success
    except (AssertionError, hypothesis.errors.MultipleFailures):
        status = Status.failure
    except hypothesis.errors.Flaky:
        status = Status.error
        result.mark_errored()
        # Sometimes Hypothesis detects inconsistent test results and checks are not available
        if result.checks:
            flaky_example = result.checks[-1].example
        else:
            flaky_example = None
        result.add_error(
            hypothesis.errors.Flaky(
                "Tests on this endpoint produce unreliable results: \n"
                "Falsified on the first call but did not on a subsequent one"
            ),
            flaky_example,
        )
    except hypothesis.errors.Unsatisfiable:
        # We need more clear error message here
        status = Status.error
        result.add_error(hypothesis.errors.Unsatisfiable("Unable to satisfy schema parameters for this endpoint"))
    except KeyboardInterrupt:
        yield events.Interrupted()
        return
    except Exception as error:
        status = Status.error
        result.add_error(error)
    # Fetch seed value, hypothesis generates it during test execution
    result.seed = getattr(test, "_hypothesis_internal_use_seed", None) or getattr(
        test, "_hypothesis_internal_use_generated_seed", None
    )
    results.append(result)
    yield events.AfterExecution.from_result(result=result, status=status, hypothesis_output=hypothesis_output)


def run_checks(case: Case, checks: Iterable[CheckFunction], result: TestResult, response: GenericResponse) -> None:
    errors = []

    for check in checks:
        check_name = check.__name__
        try:
            check(response, case)
            result.add_success(check_name, case)
        except AssertionError as exc:
            errors.append(exc)
            result.add_failure(check_name, case, str(exc))

    if errors:
        raise get_grouped_exception(*errors)


def network_test(
    case: Case,
    checks: Iterable[CheckFunction],
    result: TestResult,
    session: requests.Session,
    request_timeout: Optional[int],
) -> None:
    """A single test body that will be executed against the target."""
    # pylint: disable=too-many-arguments
    timeout = prepare_timeout(request_timeout)
    response = case.call(session=session, timeout=timeout)
    run_checks(case, checks, result, response)


@contextmanager
def get_session(
    auth: Optional[Union[HTTPDigestAuth, RawAuth]] = None, headers: Optional[Dict[str, Any]] = None
) -> Generator[requests.Session, None, None]:
    with requests.Session() as session:
        if auth is not None:
            session.auth = auth
        session.headers["User-agent"] = USER_AGENT
        if headers is not None:
            session.headers.update(**headers)
        yield session


def prepare_timeout(timeout: Optional[int]) -> Optional[float]:
    """Request timeout is in milliseconds, but `requests` uses seconds."""
    output: Optional[Union[int, float]] = timeout
    if timeout is not None:
        output = timeout / 1000
    return output


def wsgi_test(
    case: Case,
    checks: Iterable[CheckFunction],
    result: TestResult,
    auth: Optional[RawAuth],
    auth_type: Optional[str],
    headers: Optional[Dict[str, Any]],
) -> None:
    # pylint: disable=too-many-arguments
    headers = _prepare_wsgi_headers(headers, auth, auth_type)
    with catching_logs(LogCaptureHandler(), level=logging.DEBUG) as recorded:
        response = case.call_wsgi(headers=headers)
    result.logs.extend(recorded.records)
    run_checks(case, checks, result, response)


def _prepare_wsgi_headers(
    headers: Optional[Dict[str, Any]], auth: Optional[RawAuth], auth_type: Optional[str]
) -> Dict[str, Any]:
    headers = headers or {}
    headers.setdefault("User-agent", USER_AGENT)
    wsgi_auth = get_wsgi_auth(auth, auth_type)
    if wsgi_auth:
        headers["Authorization"] = wsgi_auth
    return headers


def get_wsgi_auth(auth: Optional[RawAuth], auth_type: Optional[str]) -> Optional[str]:
    if auth:
        if auth_type == "digest":
            raise ValueError("Digest auth is not supported for WSGI apps")
        return _basic_auth_str(*auth)
    return None
