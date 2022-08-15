import ctypes
import queue
import threading
import time
from queue import Queue
from typing import Any, Callable, Dict, Generator, Iterable, List, Optional, Union, cast

import attr
import hypothesis

from ..._hypothesis import create_test
from ...constants import DataGenerationMethod
from ...models import CheckFunction, TestResultSet
from ...stateful import Feedback, Stateful
from ...targets import Target
from ...types import RawAuth, RequestCert
from ...utils import Ok, capture_hypothesis_output, get_requests_auth
from .. import events
from .core import BaseRunner, asgi_test, get_session, handle_schema_error, network_test, run_test, wsgi_test


def _run_task(
    test_template: Callable,
    tasks_queue: Queue,
    events_queue: Queue,
    generator_done: threading.Event,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    data_generation_methods: Iterable[DataGenerationMethod],
    settings: hypothesis.settings,
    seed: Optional[int],
    results: TestResultSet,
    stateful: Optional[Stateful],
    stateful_recursion_limit: int,
    **kwargs: Any,
) -> None:
    def _run_tests(maker: Callable, recursion_level: int = 0) -> None:
        if recursion_level > stateful_recursion_limit:
            return
        for _result in maker(test_template, settings, seed):
            # `result` is always `Ok` here
            _operation, test = _result.ok()
            feedback = Feedback(stateful, _operation)
            for _event in run_test(
                _operation,
                test,
                checks,
                data_generation_methods,
                targets,
                results,
                recursion_level=recursion_level,
                feedback=feedback,
                **kwargs,
            ):
                events_queue.put(_event)
            _run_tests(feedback.get_stateful_tests, recursion_level + 1)

    with capture_hypothesis_output():
        while True:
            try:
                result = tasks_queue.get(timeout=0.001)
            except queue.Empty:
                # The queue is empty & there will be no more tasks
                if generator_done.is_set():
                    break
                # If there is a possibility for new tasks - try again
                continue
            if isinstance(result, Ok):
                operation = result.ok()
                test_function = create_test(
                    operation=operation,
                    test=test_template,
                    settings=settings,
                    seed=seed,
                    data_generation_methods=list(data_generation_methods),
                )
                items = Ok((operation, test_function))
                # This lambda ignores the input arguments to support the same interface for
                # `feedback.get_stateful_tests`
                _run_tests(lambda *_: (items,))
            else:
                for event in handle_schema_error(result.err(), results, data_generation_methods, 0):
                    events_queue.put(event)


def thread_task(
    tasks_queue: Queue,
    events_queue: Queue,
    generator_done: threading.Event,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    data_generation_methods: Iterable[DataGenerationMethod],
    settings: hypothesis.settings,
    auth: Optional[RawAuth],
    auth_type: Optional[str],
    headers: Optional[Dict[str, Any]],
    seed: Optional[int],
    results: TestResultSet,
    stateful: Optional[Stateful],
    stateful_recursion_limit: int,
    kwargs: Any,
) -> None:
    """A single task, that threads do.

    Pretty similar to the default one-thread flow, but includes communication with the main thread via the events queue.
    """
    prepared_auth = get_requests_auth(auth, auth_type)
    with get_session(prepared_auth) as session:
        _run_task(
            network_test,
            tasks_queue,
            events_queue,
            generator_done,
            checks,
            targets,
            data_generation_methods,
            settings,
            seed,
            results,
            stateful=stateful,
            stateful_recursion_limit=stateful_recursion_limit,
            session=session,
            headers=headers,
            **kwargs,
        )


def wsgi_thread_task(
    tasks_queue: Queue,
    events_queue: Queue,
    generator_done: threading.Event,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    data_generation_methods: Iterable[DataGenerationMethod],
    settings: hypothesis.settings,
    seed: Optional[int],
    results: TestResultSet,
    stateful: Optional[Stateful],
    stateful_recursion_limit: int,
    kwargs: Any,
) -> None:
    _run_task(
        wsgi_test,
        tasks_queue,
        events_queue,
        generator_done,
        checks,
        targets,
        data_generation_methods,
        settings,
        seed,
        results,
        stateful=stateful,
        stateful_recursion_limit=stateful_recursion_limit,
        **kwargs,
    )


def asgi_thread_task(
    tasks_queue: Queue,
    events_queue: Queue,
    generator_done: threading.Event,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    data_generation_methods: Iterable[DataGenerationMethod],
    settings: hypothesis.settings,
    headers: Optional[Dict[str, Any]],
    seed: Optional[int],
    results: TestResultSet,
    stateful: Optional[Stateful],
    stateful_recursion_limit: int,
    kwargs: Any,
) -> None:
    _run_task(
        asgi_test,
        tasks_queue,
        events_queue,
        generator_done,
        checks,
        targets,
        data_generation_methods,
        settings,
        seed,
        results,
        stateful=stateful,
        stateful_recursion_limit=stateful_recursion_limit,
        headers=headers,
        **kwargs,
    )


def stop_worker(thread_id: int) -> None:
    """Raise an error in a thread, so it is possible to asynchronously stop thread execution."""
    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread_id), ctypes.py_object(SystemExit))


@attr.s(slots=True)  # pragma: no mutate
class ThreadPoolRunner(BaseRunner):
    """Spread different tests among multiple worker threads."""

    workers_num: int = attr.ib(default=2)  # pragma: no mutate
    request_tls_verify: Union[bool, str] = attr.ib(default=True)  # pragma: no mutate
    request_cert: Optional[RequestCert] = attr.ib(default=None)  # pragma: no mutate

    def _execute(
        self, results: TestResultSet, stop_event: threading.Event
    ) -> Generator[events.ExecutionEvent, None, None]:
        """All events come from a queue where different workers push their events."""
        # Instead of generating all tests at once, we do it when there is a free worker to pick it up
        # This is extremely important for memory consumption when testing large schemas
        # IMPLEMENTATION NOTE:
        # It would be better to have a separate producer thread and communicate via threading events.
        # Though it is a bit more complex, so the current solution is suboptimal in terms of resources utilization,
        # but good enough and easy enough to implement.
        tasks_generator = iter(self.schema.get_all_operations())
        generator_done = threading.Event()
        tasks_queue: Queue = Queue()
        # Add at least `workers_num` tasks first, so all workers are busy
        for _ in range(self.workers_num):
            try:
                # SAFETY: Workers didn't start yet, direct modification is OK
                tasks_queue.queue.append(next(tasks_generator))
            except StopIteration:
                generator_done.set()
                break
        # Events are pushed by workers via a separate queue
        events_queue: Queue = Queue()
        workers = self._init_workers(tasks_queue, events_queue, results, generator_done)

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
                # iterations without waiting are too frequent, and a lot of time will be spent on waiting for this locks
                time.sleep(0.001)
                is_finished = all(not worker.is_alive() for worker in workers)
                while not events_queue.empty():
                    event = events_queue.get()
                    if stop_event.is_set() or isinstance(event, events.Interrupted) or self._should_stop(event):
                        # We could still have events in the queue, but ignore them to keep the logic simple
                        # for now, could be improved in the future to show more info in such corner cases
                        stop_workers()
                        is_finished = True
                        if stop_event.is_set():
                            # Discard the event. The invariant is: the next event after `stream.stop()` is `Finished`
                            break
                    yield event
                    # When we know that there are more tasks, put another task to the queue.
                    # The worker might not actually finish the current one yet, but we put the new one now, so
                    # the worker can immediately pick it up when the current one is done
                    if isinstance(event, events.BeforeExecution) and not generator_done.is_set():
                        try:
                            tasks_queue.put(next(tasks_generator))
                        except StopIteration:
                            generator_done.set()
        except KeyboardInterrupt:
            stop_workers()
            yield events.Interrupted()

    def _init_workers(
        self, tasks_queue: Queue, events_queue: Queue, results: TestResultSet, generator_done: threading.Event
    ) -> List[threading.Thread]:
        """Initialize & start workers that will execute tests."""
        workers = [
            threading.Thread(
                target=self._get_task(),
                kwargs=self._get_worker_kwargs(tasks_queue, events_queue, results, generator_done),
                name=f"schemathesis_{num}",
            )
            for num in range(self.workers_num)
        ]
        for worker in workers:
            worker.start()
        return workers

    def _get_task(self) -> Callable:
        return thread_task

    def _get_worker_kwargs(
        self, tasks_queue: Queue, events_queue: Queue, results: TestResultSet, generator_done: threading.Event
    ) -> Dict[str, Any]:
        return {
            "tasks_queue": tasks_queue,
            "events_queue": events_queue,
            "generator_done": generator_done,
            "checks": self.checks,
            "targets": self.targets,
            "settings": self.hypothesis_settings,
            "auth": self.auth,
            "auth_type": self.auth_type,
            "headers": self.headers,
            "seed": self.seed,
            "results": results,
            "stateful": self.stateful,
            "stateful_recursion_limit": self.stateful_recursion_limit,
            "data_generation_methods": self.schema.data_generation_methods,
            "kwargs": {
                "request_timeout": self.request_timeout,
                "request_tls_verify": self.request_tls_verify,
                "request_cert": self.request_cert,
                "store_interactions": self.store_interactions,
                "max_response_time": self.max_response_time,
                "dry_run": self.dry_run,
            },
        }


class ThreadPoolWSGIRunner(ThreadPoolRunner):
    def _get_task(self) -> Callable:
        return wsgi_thread_task

    def _get_worker_kwargs(
        self, tasks_queue: Queue, events_queue: Queue, results: TestResultSet, generator_done: threading.Event
    ) -> Dict[str, Any]:
        return {
            "tasks_queue": tasks_queue,
            "events_queue": events_queue,
            "generator_done": generator_done,
            "checks": self.checks,
            "targets": self.targets,
            "settings": self.hypothesis_settings,
            "seed": self.seed,
            "results": results,
            "stateful": self.stateful,
            "stateful_recursion_limit": self.stateful_recursion_limit,
            "data_generation_methods": self.schema.data_generation_methods,
            "kwargs": {
                "auth": self.auth,
                "auth_type": self.auth_type,
                "headers": self.headers,
                "store_interactions": self.store_interactions,
                "max_response_time": self.max_response_time,
                "dry_run": self.dry_run,
            },
        }


class ThreadPoolASGIRunner(ThreadPoolRunner):
    def _get_task(self) -> Callable:
        return asgi_thread_task

    def _get_worker_kwargs(
        self, tasks_queue: Queue, events_queue: Queue, results: TestResultSet, generator_done: threading.Event
    ) -> Dict[str, Any]:
        return {
            "tasks_queue": tasks_queue,
            "events_queue": events_queue,
            "generator_done": generator_done,
            "checks": self.checks,
            "targets": self.targets,
            "settings": self.hypothesis_settings,
            "headers": self.headers,
            "seed": self.seed,
            "results": results,
            "stateful": self.stateful,
            "stateful_recursion_limit": self.stateful_recursion_limit,
            "data_generation_methods": self.schema.data_generation_methods,
            "kwargs": {
                "store_interactions": self.store_interactions,
                "max_response_time": self.max_response_time,
                "dry_run": self.dry_run,
            },
        }
