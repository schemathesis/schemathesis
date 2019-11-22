import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Generator, Iterable, Optional, Tuple, Union

import hypothesis
import hypothesis.errors
import requests
from requests.auth import AuthBase

from ..constants import USER_AGENT
from ..exceptions import InvalidSchema
from ..loaders import from_uri
from ..models import Case, Status, TestResult, TestResultSet
from ..schemas import BaseSchema
from ..utils import get_base_url
from . import events
from .checks import DEFAULT_CHECKS

DEFAULT_DEADLINE = 500  # pragma: no mutate

Auth = Union[Tuple[str, str], AuthBase]  # pragma: no mutate


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
    settings = hypothesis.settings(deadline=DEFAULT_DEADLINE)
    if hypothesis_options is not None:
        settings = hypothesis.settings(settings, **hypothesis_options)
    return settings


def execute_from_schema(
    schema: BaseSchema,
    checks: Iterable[Callable],
    *,
    hypothesis_options: Optional[Dict[str, Any]] = None,
    auth: Optional[Auth] = None,
    headers: Optional[Dict[str, Any]] = None,
    request_timeout: Optional[int] = None,
    seed: Optional[int] = None,
) -> Generator[events.ExecutionEvent, None, None]:
    """Execute tests for the given schema.

    Provides the main testing loop and preparation step.
    """
    # pylint: disable=too-many-locals
    results = TestResultSet()

    with get_session(auth, headers) as session:
        settings = get_hypothesis_settings(hypothesis_options)

        initialized = events.Initialized(results=results, schema=schema, checks=checks, hypothesis_settings=settings)
        yield initialized

        for endpoint, test in schema.get_all_tests(single_test, settings, seed=seed):
            result = TestResult(endpoint=endpoint, schema=schema)
            yield events.BeforeExecution(results=results, schema=schema, endpoint=endpoint)
            try:
                if isinstance(test, InvalidSchema):
                    status = Status.error
                    result.add_error(test)
                else:
                    test(session, checks, result, request_timeout)
                    status = Status.success
            except AssertionError:
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
                result.add_error(
                    hypothesis.errors.Unsatisfiable("Unable to satisfy schema parameters for this endpoint")
                )
            except KeyboardInterrupt:
                yield events.Interrupted(results=results, schema=schema)
                break
            except Exception as error:
                status = Status.error
                result.add_error(error)
            # Fetch seed value, hypothesis generates it during test execution
            result.seed = getattr(test, "_hypothesis_internal_use_seed", None) or getattr(
                test, "_hypothesis_internal_use_generated_seed", None
            )
            results.append(result)
            yield events.AfterExecution(results=results, schema=schema, endpoint=endpoint, status=status)

    yield events.Finished(results=results, schema=schema, running_time=time.time() - initialized.start_time)


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
    seed: Optional[int] = None,
) -> Generator[events.ExecutionEvent, None, None]:
    """Prepare a generator that will run test cases against the given API definition."""
    api_options = api_options or {}
    loader_options = loader_options or {}

    if "base_url" not in loader_options:
        loader_options["base_url"] = get_base_url(schema_uri)
    schema = loader(schema_uri, **loader_options)
    return execute_from_schema(schema, checks, hypothesis_options=hypothesis_options, seed=seed, **api_options)


def single_test(
    case: Case,
    session: requests.Session,
    checks: Iterable[Callable],
    result: TestResult,
    request_timeout: Optional[int],
) -> None:
    """A single test body that will be executed against the target."""
    # pylint: disable=too-many-arguments
    timeout = prepare_timeout(request_timeout)
    response = case.call(session=session, timeout=timeout)
    errors = None

    for check in checks:
        check_name = check.__name__
        try:
            check(response, result)
            result.add_success(check_name, case)
        except AssertionError as exc:
            errors = True  # pragma: no mutate
            result.add_failure(check_name, case, str(exc))

    if errors is not None:
        # An exception needed to trigger Hypothesis shrinking & flaky tests detection logic
        # The message doesn't matter
        raise AssertionError


def prepare_timeout(timeout: Optional[int]) -> Optional[float]:
    """Request timeout is in milliseconds, but `requests` uses seconds"""
    output: Optional[Union[int, float]] = timeout
    if timeout is not None:
        output = timeout / 1000
    return output
