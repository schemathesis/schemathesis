import ctypes
import threading
import time
from queue import Queue
from typing import Any, Callable, Dict, Generator, Iterable, List, Optional, cast

import attr
import hypothesis

from ..._hypothesis import make_test_or_exception
from ...models import CheckFunction, TestResultSet
from ...types import RawAuth
from ...utils import capture_hypothesis_output, get_requests_auth
from .. import events
from .core import BaseRunner, get_session, network_test, run_test, wsgi_test


def _run_task(
    test_template: Callable,
    tasks_queue: Queue,
    events_queue: Queue,
    checks: Iterable[CheckFunction],
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
            for event in run_test(endpoint, test, checks, results, **kwargs):
                events_queue.put(event)


def thread_task(
    tasks_queue: Queue,
    events_queue: Queue,
    checks: Iterable[CheckFunction],
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
        _run_task(network_test, tasks_queue, events_queue, checks, settings, seed, results, session=session, **kwargs)


def wsgi_thread_task(
    tasks_queue: Queue,
    events_queue: Queue,
    checks: Iterable[CheckFunction],
    settings: hypothesis.settings,
    seed: Optional[int],
    results: TestResultSet,
    kwargs: Any,
) -> None:
    # pylint: disable=too-many-arguments
    _run_task(wsgi_test, tasks_queue, events_queue, checks, settings, seed, results, **kwargs)


def stop_worker(thread_id: int) -> None:
    """Raise an error in a thread so it is possible to asynchronously stop thread execution."""
    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread_id), ctypes.py_object(SystemExit))


@attr.s(slots=True)  # pragma: no mutate
class ThreadPoolRunner(BaseRunner):
    """Spread different tests among multiple worker threads."""

    workers_num: int = attr.ib(default=2)  # pragma: no mutate

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
            yield events.Interrupted()

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
            "checks": self.checks,
            "settings": self.hypothesis_settings,
            "seed": self.seed,
            "results": results,
            "kwargs": {"auth": self.auth, "auth_type": self.auth_type, "headers": self.headers},
        }


class ThreadInterrupted(Exception):
    """Special exception when worker thread received SIGINT."""
