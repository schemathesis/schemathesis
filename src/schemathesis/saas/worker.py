from queue import Queue

from . import events
from .client import SaaSClient
from .constants import STOP_MARKER
from .serialization import serialize_event


def start(url: str, token: str, in_queue: Queue, out_queue: Queue) -> None:
    """Main working loop that sends data to SaaS."""
    try:
        client = SaaSClient(url, token)
        job_id = client.create_test_job()
        while True:
            event = in_queue.get()
            if event is STOP_MARKER:
                # It is an equivalent of an internal error in some other handler.
                # In the happy path scenario, the worker will exit on any terminal event
                client.finish_test_job(job_id)
                break
            data = serialize_event(event)
            client.send_event(job_id, data)
            if event.is_terminal:
                break
        out_queue.put(events.Success())
    except Exception:
        out_queue.put(events.Error())
