"""Layered task scheduler for dependency-aware operation ordering."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Iterator

from schemathesis.core.result import Ok, Result

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


class LayeredScheduler:
    """Schedules test tasks respecting dependency layers.

    Operations are grouped into layers where each layer can execute in parallel,
    but layers must complete sequentially. This ensures operations with dependencies
    run in the correct order.

    Thread-safe: Multiple workers can call next_operation() concurrently.
    Uses completion counter instead of barrier to avoid deadlock if workers crash.
    """

    def __init__(
        self,
        operations_by_layer: list[list[APIOperation]],
        workers_num: int = 1,
        error_results: list[Result] | None = None,
        wait_timeout: float = 1.0,
    ) -> None:
        """Initialize the scheduler with pre-computed layers.

        Args:
            operations_by_layer: List of layers, each containing operations that
                                can execute in parallel
            workers_num: Number of workers that will call next_operation()
            error_results: Err results that should still be yielded to workers
            wait_timeout: How long workers should wait before re-checking for advancement

        """
        self.layers = operations_by_layer
        self.current_layer_index = 0
        self.current_layer_iterator: Iterator[APIOperation] | None = None
        self.lock = threading.Lock()
        self.active_workers = workers_num
        self.workers_finished_layer = 0
        self.layer_advanced = threading.Event()
        self.error_iterator: Iterator[Result] | None = iter(error_results) if error_results else None
        self.layer_waiters: dict[int, int] = {}
        self.wait_timeout = wait_timeout

        # Initialize first layer
        if self.layers:
            self.current_layer_iterator = iter(self.layers[0])

    def _advance_layer_locked(self) -> None:
        """Advance to the next layer and wake waiting workers (requires lock)."""
        self.current_layer_index += 1
        if self.current_layer_index < len(self.layers):
            self.current_layer_iterator = iter(self.layers[self.current_layer_index])
        else:
            self.current_layer_iterator = None

        self.workers_finished_layer = 0
        self.layer_waiters.clear()
        self.layer_advanced.set()

    def next_operation(self) -> Result | None:
        """Get next API operation from current layer in a thread-safe manner.

        Automatically handles layer advancement when all workers finish current layer.
        Workers that finish early will retry after layer advancement.

        Returns:
            Ok(operation) if operation available, None if all layers exhausted

        """
        worker_id = threading.get_ident()

        while True:
            should_wait = False
            with self.lock:
                # Emit pending errors first so they are not lost when layering is enabled
                if self.error_iterator is not None:
                    try:
                        return next(self.error_iterator)
                    except StopIteration:
                        self.error_iterator = None

                if self.current_layer_iterator is None:
                    return None

                try:
                    operation = next(self.current_layer_iterator)
                    self.layer_waiters.pop(worker_id, None)
                    return Ok(operation)
                except StopIteration:
                    layer_index = self.current_layer_index
                    already_counted = self.layer_waiters.get(worker_id) == layer_index

                    if not already_counted:
                        self.layer_waiters[worker_id] = layer_index
                        self.workers_finished_layer += 1

                        expected_workers = max(self.active_workers, 1)
                        if self.workers_finished_layer >= expected_workers:
                            # Last worker - advance to next layer
                            self._advance_layer_locked()
                            continue

                    # Not last worker (or already counted) - wait for layer advancement
                    should_wait = True

            # Wait for layer advancement (without holding lock)
            if should_wait:
                self.layer_advanced.wait(timeout=self.wait_timeout)
                # Clear event for next usage
                with self.lock:
                    if self.layer_advanced.is_set():
                        self.layer_advanced.clear()
                continue

    def worker_stopped(self) -> None:
        worker_id = threading.get_ident()
        with self.lock:
            if self.active_workers == 0:
                return

            recorded_layer = self.layer_waiters.pop(worker_id, None)
            if recorded_layer == self.current_layer_index and self.workers_finished_layer > 0:
                self.workers_finished_layer -= 1

            self.active_workers -= 1

            if self.active_workers == 0:
                self.current_layer_iterator = None
                self.layer_waiters.clear()
                self.layer_advanced.set()
                return

            if self.current_layer_iterator is None:
                self.layer_advanced.set()
                return

            expected_workers = max(self.active_workers, 1)
            if self.workers_finished_layer >= expected_workers:
                self._advance_layer_locked()
