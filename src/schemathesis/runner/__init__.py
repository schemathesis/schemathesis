from contextlib import contextmanager
from typing import Any, Callable, Dict, Generator, Iterable, Optional, Tuple, Union

import hypothesis
import hypothesis.errors
import requests
from requests.auth import AuthBase

from ..constants import USER_AGENT
from ..loaders import from_uri
from ..models import Case, Status, TestResult, TestResultSet
from ..schemas import BaseSchema
from ..utils import get_base_url
from . import events

Auth = Union[Tuple[str, str], AuthBase]  # pragma: no mutate


def not_a_server_error(response: requests.Response) -> None:
    """A check to verify that the response is not a server-side error."""
    assert response.status_code < 500


DEFAULT_CHECKS = (not_a_server_error,)


@contextmanager
def get_session(
    auth: Optional[Auth] = None, headers: Optional[Dict[str, Any]] = None
) -> Generator[requests.Session, None, None]:
    with requests.Session() as session:
        if auth is not None:
            session.auth = auth
        session.headers["User-agent"] = USER_AGENT
        if headers is not None:
            session.headers.update(**headers)
        yield session


def get_hypothesis_settings(hypothesis_options: Optional[Dict[str, Any]] = None) -> hypothesis.settings:
    # Default settings, used as a parent settings object below
    settings = hypothesis.settings(deadline=500)
    if hypothesis_options is not None:
        settings = hypothesis.settings(settings, **hypothesis_options)
    return settings


def execute_from_schema(
    schema: BaseSchema,
    base_url: str,
    checks: Iterable[Callable],
    *,
    hypothesis_options: Optional[Dict[str, Any]] = None,
    auth: Optional[Auth] = None,
    headers: Optional[Dict[str, Any]] = None,
) -> Generator[events.ExecutionEvent, None, None]:
    """Execute tests for the given schema.

    Provides the main testing loop and preparation step.
    """
    results = TestResultSet()

    with get_session(auth, headers) as session:
        settings = get_hypothesis_settings(hypothesis_options)

        yield events.Initialized(results=results, schema=schema, checks=checks, hypothesis_settings=settings)

        for endpoint, test in schema.get_all_tests(single_test, settings):
            if not endpoint.is_valid:
                status = Status.invalid
            result = TestResult(path=endpoint.path, method=endpoint.method)
            yield events.BeforeExecution(results=results, schema=schema, endpoint=endpoint)
            try:
                test(session, base_url, checks, result)
                status = Status.success
            except AssertionError:
                status = Status.failure
            except hypothesis.errors.Unsatisfiable:
                # We need more clear error message here
                status = Status.error
                exception = hypothesis.errors.Unsatisfiable("Unable to satisfy schema parameters for this endpoint")
                result.add_error(exception)
            except Exception as error:
                status = Status.error
                result.add_error(error)
            results.append(result)
            yield events.AfterExecution(results=results, schema=schema, endpoint=endpoint, status=status)

    yield events.Finished(results=results, schema=schema)


def execute(  # pylint: disable=too-many-arguments
    schema_uri: str,
    checks: Iterable[Callable] = DEFAULT_CHECKS,
    api_options: Optional[Dict[str, Any]] = None,
    loader_options: Optional[Dict[str, Any]] = None,
    hypothesis_options: Optional[Dict[str, Any]] = None,
    loader: Callable = from_uri,
) -> TestResultSet:
    generator = prepare(
        schema_uri=schema_uri,
        checks=checks,
        api_options=api_options,
        loader_options=loader_options,
        hypothesis_options=hypothesis_options,
        loader=loader,
    )
    all_events = list(generator)
    finished = all_events[-1]
    return finished.results


def prepare(  # pylint: disable=too-many-arguments
    schema_uri: str,
    checks: Iterable[Callable] = DEFAULT_CHECKS,
    api_options: Optional[Dict[str, Any]] = None,
    loader_options: Optional[Dict[str, Any]] = None,
    hypothesis_options: Optional[Dict[str, Any]] = None,
    loader: Callable = from_uri,
) -> Generator[events.ExecutionEvent, None, None]:
    """Prepare a generator that will run test cases against the given API definition."""
    api_options = api_options or {}
    loader_options = loader_options or {}

    schema = loader(schema_uri, **loader_options)
    base_url = api_options.pop("base_url", "") or get_base_url(schema_uri)
    return execute_from_schema(schema, base_url, checks, hypothesis_options=hypothesis_options, **api_options)


def single_test(
    case: Case, session: requests.Session, base_url: str, checks: Iterable[Callable], stats: TestResult
) -> None:
    """A single test body that will be executed against the target."""
    response = case.call(base_url=base_url, session=session)
    errors = False

    for check in checks:
        check_name = check.__name__
        try:
            check(response)
            stats.add_success(check_name)
        except AssertionError:
            errors = True
            stats.add_failure(check_name, case)

    if errors:
        # An exception needed to trigger Hypothesis shrinking & flaky tests detection logic
        # The message doesn't matter
        raise AssertionError
