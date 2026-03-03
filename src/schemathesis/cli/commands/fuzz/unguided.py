"""Unguided fuzzing mode with long-lived worker tests."""

from __future__ import annotations

import threading
from collections.abc import Callable


class _FuzzingStopped(BaseException):
    """Raised inside a Hypothesis test body to stop a fuzzing worker gracefully.

    Using a dedicated BaseException subclass instead of KeyboardInterrupt avoids
    the semantic mismatch of raising an OS-signal exception from application code,
    and makes the intent explicit to future readers of the call stack.
    """


def run_unguided(
    test_fn: Callable | None = None,
    *,
    n_workers: int,
    stop_event: threading.Event,
    on_failure: Callable[[Exception], None] | None = None,
    continue_on_failure: bool = False,
    test_fn_factory: Callable[[int], Callable] | None = None,
) -> None:
    """Run N threads with long-lived Hypothesis tests.

    Workers do not restart the Hypothesis test function after failures.
    Recoverable failures should be handled inside `test_fn` when
    `continue_on_failure=True`.
    """
    threads = [
        threading.Thread(
            target=_worker_loop,
            args=(
                (test_fn_factory(worker_id) if test_fn_factory is not None else test_fn),
                stop_event,
                on_failure,
                continue_on_failure,
            ),
            daemon=True,
        )
        for worker_id in range(n_workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def _worker_loop(
    test_fn: Callable,
    stop_event: threading.Event,
    on_failure: Callable[[Exception], None] | None,
    continue_on_failure: bool,
) -> None:
    if stop_event.is_set():
        return
    try:
        test_fn()
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, _FuzzingStopped)):
            stop_event.set()
            return
        if not isinstance(exc, Exception):
            # Non-Exception BaseException (e.g. SystemExit) — treat as a shutdown
            # signal: stop all workers but don't report as a fuzzing error.
            stop_event.set()
            return
        if on_failure is not None:
            on_failure(exc)
        if not continue_on_failure:
            stop_event.set()
