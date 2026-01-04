from __future__ import annotations

import json
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Deque, Iterable, Sequence

from schemathesis.core.transforms import UNRESOLVABLE, resolve_pointer
from schemathesis.core.transport import status_code_matches
from schemathesis.resources.descriptors import Cardinality, ResourceDescriptor

# Maximum number of resource instances cached per unique context within a resource type.
# This ensures diversity across parent resources (e.g., pets from different owners).
# Set high enough to retain most resources created during typical test runs.
PER_CONTEXT_CAPACITY = 500

# Maximum number of unique contexts to track per resource type.
MAX_CONTEXTS_PER_TYPE = 20


@dataclass(slots=True)
class ResourceInstance:
    """Concrete resource captured from an API response."""

    data: dict[str, Any]
    source_operation: str
    status_code: int
    context: dict[str, Any]


class ResourceRepository:
    """Thread-safe cache storing resources from API responses for test generation.

    Uses context-aware eviction to maintain diversity across parent resources.
    For example, pets from different owners are preserved rather than being
    dominated by pets from a single owner.
    """

    __slots__ = (
        "_descriptors_by_operation",
        "_resource_buckets",
        "_context_order",
        "_lock",
    )

    def __init__(self, descriptors: Sequence[ResourceDescriptor]) -> None:
        self._descriptors_by_operation: dict[str, list[ResourceDescriptor]] = defaultdict(list)
        # Nested structure: resource_name -> context_key -> deque of instances
        self._resource_buckets: dict[str, dict[str, Deque[ResourceInstance]]] = {}
        # Track context insertion order for FIFO eviction of contexts
        self._context_order: dict[str, Deque[str]] = {}
        self._lock = threading.Lock()

        for descriptor in descriptors:
            self._descriptors_by_operation[descriptor.operation].append(descriptor)
            if descriptor.resource_name not in self._resource_buckets:
                self._resource_buckets[descriptor.resource_name] = {}
                self._context_order[descriptor.resource_name] = deque()

    def descriptors_for_operation(self, operation_label: str) -> Sequence[ResourceDescriptor]:
        return self._descriptors_by_operation.get(operation_label, ())

    def iter_instances(self, resource_name: str) -> tuple[ResourceInstance, ...]:
        """Iterate over cached instances of a resource type.

        Returns instances from all contexts, providing diversity across parent resources.
        """
        context_buckets = self._resource_buckets.get(resource_name)
        if not context_buckets:
            return ()
        # Collect instances from all context buckets
        instances: list[ResourceInstance] = []
        for bucket in context_buckets.values():
            instances.extend(bucket)
        return tuple(instances)

    def record_response(
        self, *, operation: str, status_code: int, payload: Any, context: dict[str, Any] | None = None
    ) -> None:
        """Capture resources from an API response based on configured descriptors."""
        descriptors = self._descriptors_by_operation.get(operation, [])

        for descriptor in descriptors:
            # Match if exact status code matches OR both are 2xx
            # This handles cases where schema says "200" but server returns "201"
            if not status_code_matches(descriptor.status_code, status_code) and not (
                str(descriptor.status_code).startswith("2") and str(status_code).startswith("2")
            ):
                continue
            for candidate in self._extract_payload(payload, descriptor):
                self._store(
                    resource_name=descriptor.resource_name,
                    data=candidate,
                    source_operation=operation,
                    status_code=status_code,
                    context=context or {},
                )

    def _extract_payload(self, payload: Any, descriptor: ResourceDescriptor) -> Iterable[dict[str, Any]]:
        pointer = descriptor.pointer
        if pointer in ("", None, "/"):
            target = payload
        else:
            target = resolve_pointer(payload, pointer)
            if target is UNRESOLVABLE:
                return ()

        if descriptor.cardinality == Cardinality.MANY and isinstance(target, list):
            values = target
        else:
            values = [target]

        return [value for value in values if isinstance(value, dict)]

    def _store(
        self,
        *,
        resource_name: str,
        data: dict[str, Any],
        source_operation: str,
        status_code: int,
        context: dict[str, Any],
    ) -> None:
        """Store a resource instance with context-aware eviction.

        Maintains diversity by limiting instances per context and evicting
        oldest contexts when capacity is reached.
        """
        context_buckets = self._resource_buckets.get(resource_name)
        context_order = self._context_order.get(resource_name)
        assert context_buckets is not None, "Buckets should be created for all resources"
        assert context_order is not None, "Context order should be created for all resources"

        # Create a stable key for the context
        context_key = json.dumps(context, sort_keys=True) if context else ""

        instance = ResourceInstance(
            data=data, source_operation=source_operation, status_code=status_code, context=context
        )

        with self._lock:
            # Get or create bucket for this context
            if context_key not in context_buckets:
                # Check if we need to evict an old context
                if len(context_buckets) >= MAX_CONTEXTS_PER_TYPE:
                    # Remove the oldest context
                    oldest_key = context_order.popleft()
                    del context_buckets[oldest_key]
                # Create new bucket for this context
                context_buckets[context_key] = deque(maxlen=PER_CONTEXT_CAPACITY)
                context_order.append(context_key)

            context_buckets[context_key].append(instance)
