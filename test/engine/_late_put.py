from __future__ import annotations

import queue
from queue import Queue
from typing import Any

from schemathesis.engine import events


def attach_late_put(events_queue: Queue, late_event: events.EngineEvent) -> None:
    # Plants `late_event` exactly once when `get` raises Empty — the race window
    # where the producer's final put lands after its `get(timeout=...)` returned Empty.
    original_get = events_queue.get
    state = {"done": False}

    def get(*args: Any, **kwargs: Any) -> events.EngineEvent:
        try:
            return original_get(*args, **kwargs)
        except queue.Empty:
            if not state["done"]:
                state["done"] = True
                events_queue.put(late_event)
            raise

    events_queue.get = get  # type: ignore[method-assign]
