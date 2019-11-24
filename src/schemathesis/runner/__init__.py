import ctypes
import threading
import time
from contextlib import contextmanager
from queue import Queue
from typing import Any, Callable, Dict, Generator, Iterable, List, Optional, Tuple, Union

import attr
import hypothesis
import hypothesis.errors
import requests
from requests.auth import AuthBase

from .._hypothesis import make_test_or_exception
from ..constants import USER_AGENT
from ..exceptions import InvalidSchema
from ..loaders import from_uri
from ..models import Case, Endpoint, Status, TestResult, TestResultSet
from ..schemas import BaseSchema
from ..utils import capture_hypothesis_output, get_base_url
from . import events
from .checks import DEFAULT_CHECKS

DEFAULT_DEADLINE = 500  # pragma: no mutate

Auth = Union[Tuple[str, str], AuthBase]  # pragma: no mutate


def get_hypothesis_settings(hypothesis_options: Optional[Dict[str, Any]] = None) -> hypothesis.settings:
    # Default settings, used as a parent settings object below
    settings = hypothesis.settings(deadline=DEFAULT_DEADLINE)
    if hypothesis_options is not None:
        settings = hypothesis.settings(settings, **hypothesis_options)
    return settings


@attr.s
class BaseRunner:
    schema: BaseSchema = attr.ib()
    checks: Iterable[Callable] = attr.ib()
    hypothesis_settings: hypothesis.settings = attr.ib(converter=get_hypothesis_settings)
    auth: Optional[Auth] = attr.ib(default=None)
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
        with get_session(self.auth, self.headers) as session:
            for endpoint, test in self.schema.get_all_tests(single_test, self.hypothesis_settings, self.seed):
                for event in run_test(self.schema, endpoint, test, self.checks, session, results, self.request_timeout):
                    yield event
                    if isinstance(event, events.Interrupted):
                        return


def thread_task(
    tasks_queue: Queue,
    events_queue: Queue,
    schema: BaseSchema,
    checks: Iterable[Callable],
    settings: hypothesis.settings,
    auth: Optional[Auth],
    headers: Optional[Dict[str, Any]],
    request_timeout: Optional[int],
    seed: Optional[int],
    results: TestResultSet,
) -> None:
    """A single task, that threads do.

    Pretty similar to the default one-thread flow, but includes communication with the main thread via the events queue.
    """
    # pylint: disable=too-many-arguments
    # TODO. catch hypothesis output - we should move it to the main thread
    with get_session(auth, headers) as session:
        with capture_hypothesis_output():
            while not tasks_queue.empty():
                endpoint = tasks_queue.get()
                test = make_test_or_exception(endpoint, single_test, settings, seed)
                for event in run_test(schema, endpoint, test, checks, session, results, request_timeout):
                    events_queue.put(event)


class Worker(threading.Thread):
    def stop(self) -> None:
        """Raise an error in a thread so it is possible to immediately stop thread execution."""
        thread_id = self._ident  # type: ignore
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread_id), ctypes.py_object(SystemExit))
        if res > 1:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, 0)


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

        is_finished = False
        try:
            while not is_finished:
                # Sleep is needed for performance reasons
                # each call to `is_alive` of an alive worker waits for a lock
                # iterations without waiting are too frequent and a lot of time will be spent on waiting for this locks
                time.sleep(0.001)
                is_finished = all(not worker.is_alive() for worker in workers)
                while not events_queue.empty():
                    yield events_queue.get()
        except KeyboardInterrupt:
            for worker in workers:
                worker.stop()
                worker.join()
            yield events.Interrupted(results=results, schema=self.schema)

    def _get_tasks_queue(self) -> Queue:
        """All endpoints are distributed among all workers via a queue."""
        tasks_queue: Queue = Queue()
        tasks_queue.queue.extend(self.schema.get_all_endpoints())
        return tasks_queue

    def _init_workers(self, tasks_queue: Queue, events_queue: Queue, results: TestResultSet) -> List[Worker]:
        """Initialize & start workers that will execute tests."""
        workers = [
            Worker(
                target=thread_task,
                kwargs={
                    "tasks_queue": tasks_queue,
                    "events_queue": events_queue,
                    "schema": self.schema,
                    "checks": self.checks,
                    "settings": self.hypothesis_settings,
                    "auth": self.auth,
                    "headers": self.headers,
                    "request_timeout": self.request_timeout,
                    "seed": self.seed,
                    "results": results,
                },
            )
            for _ in range(self.workers_num)
        ]
        for worker in workers:
            worker.start()
        return workers


def execute_from_schema(
    schema: BaseSchema,
    checks: Iterable[Callable],
    *,
    workers_num: int = 1,
    hypothesis_options: Optional[Dict[str, Any]] = None,
    auth: Optional[Auth] = None,
    headers: Optional[Dict[str, Any]] = None,
    request_timeout: Optional[int] = None,
    seed: Optional[int] = None,
) -> Generator[events.ExecutionEvent, None, None]:
    """Execute tests for the given schema.

    Provides the main testing loop and preparation step.
    """
    runner: BaseRunner
    if workers_num > 1:
        runner = ThreadPoolRunner(
            schema, checks, hypothesis_options, auth, headers, request_timeout, seed, workers_num=workers_num
        )
    else:
        runner = SingleThreadRunner(schema, checks, hypothesis_options, auth, headers, request_timeout, seed)

    yield from runner.execute()


def run_test(
    schema: BaseSchema,
    endpoint: Endpoint,
    test: Union[Callable, InvalidSchema],
    checks: Iterable[Callable],
    session: requests.Session,
    results: TestResultSet,
    request_timeout: Optional[int],
) -> Generator[events.ExecutionEvent, None, None]:
    """A single test run with all error handling needed."""
    # pylint: disable=too-many-arguments
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
    results.append(result)  # TODO. make thread safe
    yield events.AfterExecution(results=results, schema=schema, endpoint=endpoint, status=status)


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
