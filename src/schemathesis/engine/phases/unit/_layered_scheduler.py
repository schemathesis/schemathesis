"""Layered task scheduler for dependency-aware operation ordering."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Iterator

from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Err, Ok, Result

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


class LayeredScheduler:
    """Schedules operations in dependency layers.

    Operations are grouped into layers and dispatched sequentially by layer.
    All operations from Layer N are dispatched before Layer N + 1 operations.

    Note: With multiple workers, Layer N+1 operations may start executing before
    Layer N operations finish. Additional synchronization could enforce strict
    layer completion but is not currently implemented.
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
        self.lock = threading.Lock()
        self.errors = errors or []
        self.error_iterator: Iterator[InvalidSchema] | None = None

        # Initialize first layer
        if self.layers:
            self.current_layer_iterator = iter(self.layers[0])

    def next_operation(self) -> Result[APIOperation, InvalidSchema] | None:
        """Get next API operation in a thread-safe manner.

        Advances through layers sequentially. When a layer is exhausted, automatically
        moves to the next layer. After all layers are exhausted, returns schema errors.

        Returns:
            Ok(operation) if operation available, Err() for schema errors,
            None if all layers and errors exhausted

        """
        with self.lock:
            # Try to get operation from current layer
            while self.current_layer_iterator is not None:
                try:
                    return Ok(next(self.current_layer_iterator))
                except StopIteration:
                    # Current layer exhausted - advance to next layer
                    self.current_layer_index += 1
                    if self.current_layer_index < len(self.layers):
                        self.current_layer_iterator = iter(self.layers[self.current_layer_index])
                        # Continue loop to try next layer
                    else:
                        # No more layers
                        self.current_layer_iterator = None
                        break

            # All layers exhausted - return schema errors if any
            if self.error_iterator is None and self.errors:
                self.error_iterator = iter(self.errors)

            if self.error_iterator is not None:
                try:
                    return Err(next(self.error_iterator))
                except StopIteration:
                    return None

            return None
