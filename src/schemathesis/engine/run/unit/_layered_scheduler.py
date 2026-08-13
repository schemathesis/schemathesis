"""Layered task scheduler for dependency-aware operation ordering."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import TYPE_CHECKING

from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Err, Ok, Result

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


class _Dispatched(threading.local):
    """Whether the calling worker holds an operation from the current layer."""

    active = False


class LayeredScheduler:
    """Schedules operations in dependency layers.

    Operations are grouped into layers and dispatched sequentially by layer.
    Layer N + 1 is not dispatched until every Layer N operation has finished, so
    dependent operations observe the resources their producers created.

    A worker signals completion by asking for its next operation; `release` covers
    workers that stop without asking again.
    """

    def __init__(
        self,
        layers: list[list[APIOperation]],
        errors: list[InvalidSchema] | None = None,
    ) -> None:
        """Initialize the scheduler with pre-computed layers.

        Args:
            layers: List of layers, each containing operations that can execute in parallel
            errors: Optional list of error results from schema parsing to be returned after all layers are exhausted

        """
        assert layers
        self.layers = layers
        self.current_layer_index = 0
        self.current_layer_iterator: Iterator[APIOperation] | None = None
        self.lock = threading.Condition()
        # Operations dispatched from the current layer that are still running
        self.in_flight = 0
        self._dispatched = _Dispatched()
        self.errors = errors or []
        self.error_iterator: Iterator[InvalidSchema] | None = None

        # Initialize first layer
        if self.layers:
            self.current_layer_iterator = iter(self.layers[0])

    def next_operation(self) -> Result[APIOperation, InvalidSchema] | None:
        """Get next API operation in a thread-safe manner.

        Advances through layers sequentially. When a layer is exhausted, blocks until
        its operations finish, then moves to the next layer. After all layers are
        exhausted, returns schema errors.

        Returns:
            Ok(operation) if operation available, Err() for schema errors,
            None if all layers and errors exhausted

        """
        with self.lock:
            # Asking for more work means the previously dispatched operation is done
            self._release()
            # Try to get operation from current layer
            while self.current_layer_iterator is not None:
                try:
                    operation = next(self.current_layer_iterator)
                except StopIteration:
                    layer_index = self.current_layer_index
                    if layer_index + 1 >= len(self.layers):
                        # No more layers
                        self.current_layer_iterator = None
                        break
                    # Wait for the layer to drain, unless another worker already advanced it
                    while self.in_flight and layer_index == self.current_layer_index:
                        self.lock.wait()
                    if layer_index == self.current_layer_index:
                        self.current_layer_index += 1
                        self.current_layer_iterator = iter(self.layers[self.current_layer_index])
                    continue
                self.in_flight += 1
                self._dispatched.active = True
                return Ok(operation)

            # All layers exhausted - return schema errors if any
            if self.error_iterator is None and self.errors:
                self.error_iterator = iter(self.errors)

            if self.error_iterator is not None:
                try:
                    return Err(next(self.error_iterator))
                except StopIteration:
                    return None

            return None

    def release(self) -> None:
        """Mark this worker's operation as finished, for workers that stop asking for more."""
        with self.lock:
            self._release()

    def _release(self) -> None:
        if self._dispatched.active:
            self._dispatched.active = False
            self.in_flight -= 1
            if not self.in_flight:
                self.lock.notify_all()
