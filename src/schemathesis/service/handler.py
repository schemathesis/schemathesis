import threading
from queue import Queue

import attr

from ..cli.context import ExecutionContext
from ..cli.handlers import EventHandler
from ..runner import events
from . import worker
from .client import ServiceClient
from .constants import STOP_MARKER, WORKER_JOIN_TIMEOUT
from .models import TestRun


@attr.s(slots=True)  # pragma: no mutate
class ServiceReporter(EventHandler):
    """Send events to the worker that communicates with Schemathesis.io."""

    client: ServiceClient = attr.ib()  # pragma: no mutate
    test_run: TestRun = attr.ib()  # pragma: no mutate
    out_queue: Queue = attr.ib()  # pragma: no mutate
    in_queue: Queue = attr.ib(factory=Queue)  # pragma: no mutate
    worker: threading.Thread = attr.ib(init=False)  # pragma: no mutate

    def __attrs_post_init__(self) -> None:
        # A worker thread, that does all the work concurrently
        self.worker = threading.Thread(
            target=worker.start,
            kwargs={
                "client": self.client,
                "test_run": self.test_run,
                "in_queue": self.in_queue,
                "out_queue": self.out_queue,
            },
        )
        self.worker.start()

    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        self.in_queue.put(event)

    def shutdown(self) -> None:
        self._stop_worker()

    def _stop_worker(self) -> None:
        self.in_queue.put(STOP_MARKER)
        self.worker.join(WORKER_JOIN_TIMEOUT)
