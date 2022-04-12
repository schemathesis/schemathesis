from queue import Queue

from . import events
from .client import ServiceClient
from .constants import STOP_MARKER
from .models import TestRun
from .serialization import serialize_event


def start(client: ServiceClient, test_run: TestRun, in_queue: Queue, out_queue: Queue) -> None:
    """Initialize a new run and start consuming events."""
    try:
        consume_events(client, in_queue, test_run.run_id)
        # Reached a terminal event or a stop marker.
        # In the case of stop marker, it is still a successful result for the handler itself as the error happened in
        # a different handler
        out_queue.put(events.Completed(short_url=test_run.short_url))
    except Exception as exc:
        out_queue.put(events.Error(exc))


def consume_events(client: ServiceClient, in_queue: Queue, run_id: str) -> None:
    """Main working loop that sends data to Schemathesis.io."""
    try:
        while True:
            event = in_queue.get()
            if event is STOP_MARKER:
                # It is an equivalent of an internal error in some other handler.
                # In the happy path scenario, the worker will exit on any terminal event
                client.finish_test_run(run_id)
                break
            data = serialize_event(event)
            client.send_event(run_id, data)
            if event.is_terminal:
                break
    except Exception:
        # Internal error on our side, try to finish the test run
        client.finish_test_run(run_id)
        raise
