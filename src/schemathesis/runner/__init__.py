import ctypes
import logging
import threading
import time
from contextlib import contextmanager
from queue import Queue
from typing import Any, Callable, Dict, Generator, Iterable, List, Optional, Tuple, Union, cast

import attr
import hypothesis
import hypothesis.errors
import requests
from _pytest.logging import LogCaptureHandler, catching_logs
from requests.auth import HTTPDigestAuth, _basic_auth_str

from .._hypothesis import make_test_or_exception
from ..checks import DEFAULT_CHECKS
from ..constants import USER_AGENT
from ..exceptions import InvalidSchema
from ..loaders import from_uri
from ..models import Case, Endpoint, Status, TestResult, TestResultSet
from ..schemas import BaseSchema
from ..utils import WSGIResponse, capture_hypothesis_output, get_base_url
from . import events

DEFAULT_DEADLINE = 500  # pragma: no mutate
RawAuth = Tuple[str, str]  # pragma: no mutate


def get_hypothesis_settings(hypothesis_options: Optional[Dict[str, Any]] = None) -> hypothesis.settings:
    # Default settings, used as a parent settings object below
    settings = hypothesis.settings(deadline=DEFAULT_DEADLINE)
    if hypothesis_options is not None:
        settings = hypothesis.settings(settings, **hypothesis_options)
    return settings


# pylint: disable=too-many-instance-attributes
@attr.s
class BaseRunner:
    schema: BaseSchema = attr.ib()
    checks: Iterable[Callable] = attr.ib()
    hypothesis_settings: hypothesis.settings = attr.ib(converter=get_hypothesis_settings)
    auth: Optional[RawAuth] = attr.ib(default=None)
    auth_type: Optional[str] = attr.ib(default=None)
    headers: Optional[Dict[str, Any]] = attr.ib(default=None)
    request_timeout: Optional[int] = attr.ib(default=None)
    seed: Optional[int] = attr.ib(default=None)

    def execute(self,) -> Generator[events.ExecutionEvent, None, None]:
        """Common logic for all runners."""
        results = TestResultSet()

        initialized = events.Initialized(
            results=results, schema=self.schema, checks=self.checks, hypothesis_settings=self.hypothesis_settings
        )
        yield initialized

        yield from self._execute(results)

        yield events.Finished(results=results, schema=self.schema, running_time=time.time() - initialized.start_time)

    def _execute(self, results: TestResultSet) -> Generator[events.ExecutionEvent, None, None]:
        raise NotImplementedError


@attr.s(slots=True)
class SingleThreadRunner(BaseRunner):
    """Fast runner that runs tests sequentially in the main thread."""

    def _execute(self, results: TestResultSet) -> Generator[events.ExecutionEvent, None, None]:
        auth = get_requests_auth(self.auth, self.auth_type)
        with get_session(auth, self.headers) as session:
            for endpoint, test in self.schema.get_all_tests(network_test, self.hypothesis_settings, self.seed):
                for event in run_test(
                    self.schema,
                    endpoint,
                    test,
                    self.checks,
                    results,
                    session=session,
                    request_timeout=self.request_timeout,
                ):
                    yield event
                    if isinstance(event, events.Interrupted):
                        return


@attr.s(slots=True)
class SingleThreadWSGIRunner(SingleThreadRunner):
    def _execute(self, results: TestResultSet) -> Generator[events.ExecutionEvent, None, None]:
        for endpoint, test in self.schema.get_all_tests(wsgi_test, self.hypothesis_settings, self.seed):
            for event in run_test(
                self.schema,
                endpoint,
                test,
                self.checks,
                results,
                auth=self.auth,
                auth_type=self.auth_type,
                headers=self.headers,
            ):
                yield event
                if isinstance(event, events.Interrupted):
                    return


def _run_task(
    test_template: Callable,
    tasks_queue: Queue,
    events_queue: Queue,
    schema: BaseSchema,
    checks: Iterable[Callable],
    settings: hypothesis.settings,
    seed: Optional[int],
    results: TestResultSet,
    **kwargs: Any,
) -> None:
    # pylint: disable=too-many-arguments
    with capture_hypothesis_output():
        while not tasks_queue.empty():
            endpoint = tasks_queue.get()
            test = make_test_or_exception(endpoint, test_template, settings, seed)
            for event in run_test(schema, endpoint, test, checks, results, **kwargs):
                events_queue.put(event)


def thread_task(
    tasks_queue: Queue,
    events_queue: Queue,
    schema: BaseSchema,
    checks: Iterable[Callable],
    settings: hypothesis.settings,
    auth: Optional[RawAuth],
    auth_type: Optional[str],
    headers: Optional[Dict[str, Any]],
    seed: Optional[int],
    results: TestResultSet,
    kwargs: Any,
) -> None:
    """A single task, that threads do.

    Pretty similar to the default one-thread flow, but includes communication with the main thread via the events queue.
    """
    # pylint: disable=too-many-arguments
    prepared_auth = get_requests_auth(auth, auth_type)
    with get_session(prepared_auth, headers) as session:
        _run_task(
            network_test, tasks_queue, events_queue, schema, checks, settings, seed, results, session=session, **kwargs
        )


def wsgi_thread_task(
    tasks_queue: Queue,
    events_queue: Queue,
    schema: BaseSchema,
    checks: Iterable[Callable],
    settings: hypothesis.settings,
    seed: Optional[int],
    results: TestResultSet,
    kwargs: Any,
) -> None:
    # pylint: disable=too-many-arguments
    _run_task(wsgi_test, tasks_queue, events_queue, schema, checks, settings, seed, results, **kwargs)


def stop_worker(thread_id: int) -> None:
    """Raise an error in a thread so it is possible to asynchronously stop thread execution."""
    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread_id), ctypes.py_object(SystemExit))


class ThreadInterrupted(Exception):
    """Special exception when worker thread received SIGINT."""


@attr.s(slots=True)
class ThreadPoolRunner(BaseRunner):
    """Spread different tests among multiple worker threads."""

    workers_num: int = attr.ib(default=2)

    def _execute(self, results: TestResultSet) -> Generator[events.ExecutionEvent, None, None]:
        """All events come from a queue where different workers push their events."""
        tasks_queue = self._get_tasks_queue()
        # Events are pushed by workers via a separate queue
        events_queue: Queue = Queue()
        workers = self._init_workers(tasks_queue, events_queue, results)

        def stop_workers() -> None:
            for worker in workers:
                # workers are initialized at this point and `worker.ident` is set with an integer value
                ident = cast(int, worker.ident)
                stop_worker(ident)
                worker.join()

        is_finished = False
        try:
            while not is_finished:
                # Sleep is needed for performance reasons
                # each call to `is_alive` of an alive worker waits for a lock
                # iterations without waiting are too frequent and a lot of time will be spent on waiting for this locks
                time.sleep(0.001)
                is_finished = all(not worker.is_alive() for worker in workers)
                while not events_queue.empty():
                    event = events_queue.get()
                    yield event
                    if isinstance(event, events.Interrupted):
                        # Thread received SIGINT
                        # We could still have events in the queue, but ignore them to keep the logic simple
                        # for now, could be improved in the future to show more info in such corner cases
                        raise ThreadInterrupted
        except ThreadInterrupted:
            stop_workers()
        except KeyboardInterrupt:
            stop_workers()
            yield events.Interrupted(results=results, schema=self.schema)

    def _get_tasks_queue(self) -> Queue:
        """All endpoints are distributed among all workers via a queue."""
        tasks_queue: Queue = Queue()
        tasks_queue.queue.extend(self.schema.get_all_endpoints())
        return tasks_queue

    def _init_workers(self, tasks_queue: Queue, events_queue: Queue, results: TestResultSet) -> List[threading.Thread]:
        """Initialize & start workers that will execute tests."""
        workers = [
            threading.Thread(
                target=self._get_task(), kwargs=self._get_worker_kwargs(tasks_queue, events_queue, results)
            )
            for _ in range(self.workers_num)
        ]
        for worker in workers:
            worker.start()
        return workers

    def _get_task(self) -> Callable:
        return thread_task

    def _get_worker_kwargs(self, tasks_queue: Queue, events_queue: Queue, results: TestResultSet) -> Dict[str, Any]:
        return {
            "tasks_queue": tasks_queue,
            "events_queue": events_queue,
            "schema": self.schema,
            "checks": self.checks,
            "settings": self.hypothesis_settings,
            "auth": self.auth,
            "auth_type": self.auth_type,
            "headers": self.headers,
            "seed": self.seed,
            "results": results,
            "kwargs": {"request_timeout": self.request_timeout},
        }


class ThreadPoolWSGIRunner(ThreadPoolRunner):
    def _get_task(self) -> Callable:
        return wsgi_thread_task

    def _get_worker_kwargs(self, tasks_queue: Queue, events_queue: Queue, results: TestResultSet) -> Dict[str, Any]:
        return {
            "tasks_queue": tasks_queue,
            "events_queue": events_queue,
            "schema": self.schema,
            "checks": self.checks,
            "settings": self.hypothesis_settings,
            "seed": self.seed,
            "results": results,
            "kwargs": {"auth": self.auth, "auth_type": self.auth_type, "headers": self.headers},
        }


def execute_from_schema(
    schema: BaseSchema,
    checks: Iterable[Callable],
    *,
    workers_num: int = 1,
    hypothesis_options: Optional[Dict[str, Any]] = None,
    auth: Optional[RawAuth] = None,
    auth_type: Optional[str] = None,
    headers: Optional[Dict[str, Any]] = None,
    request_timeout: Optional[int] = None,
    seed: Optional[int] = None,
) -> Generator[events.ExecutionEvent, None, None]:
    """Execute tests for the given schema.

    Provides the main testing loop and preparation step.
    """
    runner: BaseRunner
    if workers_num > 1:
        if schema.app:
            runner = ThreadPoolWSGIRunner(
                schema=schema,
                checks=checks,
                hypothesis_settings=hypothesis_options,
                auth=auth,
                auth_type=auth_type,
                headers=headers,
                seed=seed,
                workers_num=workers_num,
            )
        else:
            runner = ThreadPoolRunner(
                schema=schema,
                checks=checks,
                hypothesis_settings=hypothesis_options,
                auth=auth,
                auth_type=auth_type,
                headers=headers,
                seed=seed,
                request_timeout=request_timeout,
            )
    else:
        if schema.app:
            runner = SingleThreadWSGIRunner(
                schema=schema,
                checks=checks,
                hypothesis_settings=hypothesis_options,
                auth=auth,
                auth_type=auth_type,
                headers=headers,
                seed=seed,
            )
        else:
            runner = SingleThreadRunner(
                schema=schema,
                checks=checks,
                hypothesis_settings=hypothesis_options,
                auth=auth,
                auth_type=auth_type,
                headers=headers,
                seed=seed,
                request_timeout=request_timeout,
            )

    yield from runner.execute()


def run_test(
    schema: BaseSchema,
    endpoint: Endpoint,
    test: Union[Callable, InvalidSchema],
    checks: Iterable[Callable],
    results: TestResultSet,
    **kwargs: Any,
) -> Generator[events.ExecutionEvent, None, None]:
    """A single test run with all error handling needed."""
    # pylint: disable=too-many-arguments
    result = TestResult(endpoint=endpoint)
    yield events.BeforeExecution(results=results, schema=schema, endpoint=endpoint)
    hypothesis_output: List[str] = []
    try:
        if isinstance(test, InvalidSchema):
            status = Status.error
            result.add_error(test)
        else:
            with capture_hypothesis_output() as hypothesis_output:
                test(checks, result, **kwargs)
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
        result.add_error(hypothesis.errors.Unsatisfiable("Unable to satisfy schema parameters for this endpoint"))
    except KeyboardInterrupt:
        yield events.Interrupted(results=results, schema=schema)
        return
    except Exception as error:
        status = Status.error
        result.add_error(error)
    # Fetch seed value, hypothesis generates it during test execution
    result.seed = getattr(test, "_hypothesis_internal_use_seed", None) or getattr(
        test, "_hypothesis_internal_use_generated_seed", None
    )
    results.append(result)
    yield events.AfterExecution(
        results=results, schema=schema, endpoint=endpoint, status=status, hypothesis_output=hypothesis_output
    )


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
    workers_num: int = 1,
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
    return execute_from_schema(
        schema, checks, hypothesis_options=hypothesis_options, seed=seed, workers_num=workers_num, **api_options
    )


def network_test(
    case: Case,
    checks: Iterable[Callable],
    result: TestResult,
    session: requests.Session,
    request_timeout: Optional[int],
) -> None:
    """A single test body that will be executed against the target."""
    # pylint: disable=too-many-arguments
    timeout = prepare_timeout(request_timeout)
    response = case.call(session=session, timeout=timeout)
    _run_checks(case, checks, result, response)


def wsgi_test(
    case: Case,
    checks: Iterable[Callable],
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
    _run_checks(case, checks, result, response)


def _prepare_wsgi_headers(
    headers: Optional[Dict[str, Any]], auth: Optional[RawAuth], auth_type: Optional[str]
) -> Dict[str, Any]:
    headers = headers or {}
    headers.setdefault("User-agent", USER_AGENT)
    wsgi_auth = get_wsgi_auth(auth, auth_type)
    if wsgi_auth:
        headers["Authorization"] = wsgi_auth
    return headers


def _run_checks(
    case: Case, checks: Iterable[Callable], result: TestResult, response: Union[requests.Response, WSGIResponse]
) -> None:
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


def get_requests_auth(auth: Optional[RawAuth], auth_type: Optional[str]) -> Optional[Union[HTTPDigestAuth, RawAuth]]:
    if auth and auth_type == "digest":
        return HTTPDigestAuth(*auth)
    return auth


def get_wsgi_auth(auth: Optional[RawAuth], auth_type: Optional[str]) -> Optional[str]:
    if auth:
        if auth_type == "digest":
            raise ValueError("Digest auth is not supported for WSGI apps")
        return _basic_auth_str(*auth)
    return None
