from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Deque, Iterable, Sequence

from schemathesis.core.transforms import UNRESOLVABLE, resolve_pointer
from schemathesis.core.transport import status_code_matches
from schemathesis.resources.descriptors import Cardinality, ResourceDescriptor

# Maximum number of resource instances cached per resource type.
PER_TYPE_CAPACITY = 50


@dataclass(slots=True)
class ResourceInstance:
    """Concrete resource captured from an API response."""

    data: dict[str, Any]
    source_operation: str
    status_code: int


class ResourceRepository:
    """Thread-safe cache storing resources from API responses for test generation.

    Uses FIFO eviction with per-type capacity limits.
    """

    __slots__ = (
        "_descriptors_by_operation",
        "_resource_buckets",
        "_lock",
    )

    def __init__(self, descriptors: Sequence[ResourceDescriptor]) -> None:
        self._descriptors_by_operation: dict[str, list[ResourceDescriptor]] = defaultdict(list)
        self._resource_buckets: dict[str, Deque[ResourceInstance]] = {}
        self._lock = threading.Lock()

        for descriptor in descriptors:
            self._descriptors_by_operation[descriptor.operation].append(descriptor)
            if descriptor.resource_name not in self._resource_buckets:
                self._resource_buckets[descriptor.resource_name] = deque(maxlen=PER_TYPE_CAPACITY)

    def descriptors_for_operation(self, operation_label: str) -> Sequence[ResourceDescriptor]:
        return self._descriptors_by_operation.get(operation_label, ())

    def iter_instances(self, resource_name: str) -> tuple[ResourceInstance, ...]:
        """Iterate over cached instances of a resource type."""
        bucket = self._resource_buckets.get(resource_name)
        if not bucket:
            return ()
        # Create a new tuple as this deque could be modified concurrently
        return tuple(bucket)

    def record_response(self, *, operation: str, status_code: int, payload: Any) -> None:
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

    def _store(self, *, resource_name: str, data: dict[str, Any], source_operation: str, status_code: int) -> None:
        """Store a resource instance with FIFO eviction."""
        bucket = self._resource_buckets.get(resource_name)
        assert bucket is not None, "Buckets should be created for all resources"

        instance = ResourceInstance(data=data, source_operation=source_operation, status_code=status_code)

        with self._lock:
            bucket.append(instance)
