from __future__ import annotations

import json
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Deque, Iterable, Iterator, Sequence

from schemathesis.core.transforms import UNRESOLVABLE, resolve_pointer
from schemathesis.resources.descriptors import Cardinality, ResourceDescriptor

DEFAULT_PER_TYPE_CAPACITY = 50
MAX_STORED_FIELDS = 64
MAX_SERIALIZED_CHARS = 4096


@dataclass
class ResourceInstance:
    """Concrete resource captured from an API response."""

    __slots__ = ("data", "fingerprint", "source_operation", "status_code", "sequence")

    data: dict[str, Any]
    fingerprint: str
    source_operation: str
    status_code: int
    sequence: int


class ResourceRepositoryConfig:
    """Configuration for resource repository capacity and eviction behavior."""

    __slots__ = ("per_type_capacity", "max_total")

    def __init__(self, per_type_capacity: int = DEFAULT_PER_TYPE_CAPACITY, max_total: int | None = None) -> None:
        self.per_type_capacity = per_type_capacity
        self.max_total = max_total


class ResourceRepository:
    """Stores recently observed resources for reuse during test generation.

    Thread-safe cache that captures resources from API responses and makes them
    available for use in subsequent test case generation. Uses FIFO eviction
    with configurable capacity limits per resource type.
    """

    __slots__ = (
        "_config",
        "_descriptors_by_operation",
        "_resource_buckets",
        "_seen_fingerprints",
        "_lock",
        "_insertion_sequence",
        "_oldest_sequence",
    )

    def __init__(
        self, descriptors: Sequence[ResourceDescriptor], config: ResourceRepositoryConfig | None = None
    ) -> None:
        self._config = config or ResourceRepositoryConfig()
        self._descriptors_by_operation: dict[str, list[ResourceDescriptor]] = defaultdict(list)
        self._resource_buckets: dict[str, Deque[ResourceInstance]] = {}
        self._seen_fingerprints: dict[str, set[str]] = defaultdict(set)
        self._lock = threading.Lock()
        self._insertion_sequence = 0
        self._oldest_sequence = 0

        per_type_capacity = max(1, self._config.per_type_capacity)
        for descriptor in descriptors:
            self._descriptors_by_operation[descriptor.operation_label].append(descriptor)
            if descriptor.resource_name not in self._resource_buckets:
                self._resource_buckets[descriptor.resource_name] = deque(maxlen=per_type_capacity)

    def descriptors_for_operation(self, operation_label: str) -> Sequence[ResourceDescriptor]:
        return self._descriptors_by_operation.get(operation_label, ())

    def iter_instances(self, resource_name: str) -> Iterator[ResourceInstance]:
        """Iterate over cached instances of a resource type.

        Returns a snapshot to ensure thread safety - the returned iterator
        won't be affected by concurrent modifications to the repository.
        """
        bucket = self._resource_buckets.get(resource_name)
        if not bucket:
            return iter(())
        return iter(tuple(bucket))

    def ingest_response(self, *, operation_label: str, status_code: int, payload: Any) -> None:
        """Capture resources from an API response based on configured descriptors."""
        descriptors = self._descriptors_by_operation.get(operation_label)
        if not descriptors:
            return

        status_code_str = str(status_code)
        for descriptor in descriptors:
            if not self._status_matches(descriptor.status_code, status_code_str):
                continue
            for candidate in self._extract_payload(payload, descriptor):
                self._store(
                    resource_name=descriptor.resource_name,
                    data=candidate,
                    source_operation=operation_label,
                    status_code=status_code,
                )

    def _extract_payload(self, payload: Any, descriptor: ResourceDescriptor) -> Iterable[dict[str, Any]]:
        target = payload
        if descriptor.pointer:
            target = resolve_pointer(payload, descriptor.pointer)
            if target is UNRESOLVABLE:
                return ()

        if descriptor.cardinality == Cardinality.MANY and isinstance(target, list):
            values = target
        else:
            values = [target]

        collected: list[dict[str, Any]] = []
        for value in values:
            if isinstance(value, dict):
                if self._should_store(value):
                    collected.append(value)
        return collected

    def _should_store(self, value: dict[str, Any]) -> bool:
        if len(value) > MAX_STORED_FIELDS:
            return False
        try:
            serialized = json.dumps(value, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return False
        return len(serialized) <= MAX_SERIALIZED_CHARS

    def _store(self, *, resource_name: str, data: dict[str, Any], source_operation: str, status_code: int) -> None:
        """Store a resource instance, skipping duplicates based on fingerprint.

        Fingerprint is computed outside the critical section for better performance.
        """
        bucket = self._resource_buckets.get(resource_name)
        if bucket is None:
            return

        # Compute fingerprint outside lock for better concurrency
        fingerprint = self._fingerprint(data)

        with self._lock:
            seen = self._seen_fingerprints[resource_name]
            if fingerprint in seen:
                return

            self._insertion_sequence += 1
            instance = ResourceInstance(
                data=data,
                fingerprint=fingerprint,
                source_operation=source_operation,
                status_code=status_code,
                sequence=self._insertion_sequence,
            )

            # Handle per-type capacity eviction
            if bucket.maxlen is not None and len(bucket) == bucket.maxlen:
                evicted = bucket.popleft()
                seen.discard(evicted.fingerprint)
                # Track oldest sequence across all buckets
                if evicted.sequence == self._oldest_sequence:
                    self._update_oldest_sequence()

            bucket.append(instance)
            seen.add(fingerprint)

            # Update global oldest if this is the first item overall
            if self._oldest_sequence == 0:
                self._oldest_sequence = instance.sequence

            if self._config.max_total is not None:
                self._enforce_total_limit()

    def _enforce_total_limit(self) -> None:
        max_total = self._config.max_total
        if max_total is None:
            return
        while self._current_total() > max_total:
            self._evict_oldest()

    def _current_total(self) -> int:
        return sum(len(bucket) for bucket in self._resource_buckets.values())

    def _evict_oldest(self) -> None:
        """Evict the globally oldest instance across all resource types.

        Uses cached oldest_sequence for O(1) lookup instead of O(n) scan.
        """
        if self._oldest_sequence == 0:
            return

        # Find bucket containing the oldest instance
        for resource_name, bucket in self._resource_buckets.items():
            if not bucket:
                continue
            if bucket[0].sequence == self._oldest_sequence:
                evicted = bucket.popleft()
                self._seen_fingerprints[resource_name].discard(evicted.fingerprint)
                self._update_oldest_sequence()
                return

    def _update_oldest_sequence(self) -> None:
        """Update cached oldest sequence by scanning all bucket heads.

        Called only when the oldest item is evicted, not on every operation.
        """
        new_oldest = None
        for bucket in self._resource_buckets.values():
            if not bucket:
                continue
            candidate = bucket[0].sequence
            if new_oldest is None or candidate < new_oldest:
                new_oldest = candidate
        self._oldest_sequence = new_oldest if new_oldest is not None else 0

    def _fingerprint(self, value: dict[str, Any]) -> str:
        try:
            return json.dumps(value, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return repr(sorted(value.items()))

    @staticmethod
    def _status_matches(descriptor_status: str, response_status: str) -> bool:
        if descriptor_status == response_status:
            return True
        code = descriptor_status.upper()
        if code == "DEFAULT":
            return True
        if len(code) == 3 and code.endswith("XX") and response_status:
            return code[0].isdigit() and code[0] == response_status[0]
        return False
