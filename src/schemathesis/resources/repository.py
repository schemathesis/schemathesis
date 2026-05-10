from __future__ import annotations

import threading
from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import jsonschema_rs

from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import UNRESOLVABLE, Unresolvable, resolve_pointer, resolve_pointer_all
from schemathesis.core.transport import status_code_matches
from schemathesis.resources.descriptors import Cardinality, ResourceDescriptor, ResourceFieldRef

if TYPE_CHECKING:
    from schemathesis.generation.case import Case

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
        self._resource_buckets: dict[str, dict[str, deque[ResourceInstance]]] = {}
        # Track context insertion order for FIFO eviction of contexts
        self._context_order: dict[str, deque[str]] = {}
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

    def remove_by_value(self, resource_name: str, value: object) -> int:
        """Drop instances whose data carries the given value; returns the number removed.

        Called after a successful DELETE so subsequent draws don't re-feed a server-side gone id.
        """
        context_buckets = self._resource_buckets.get(resource_name)
        if not context_buckets:
            return 0
        removed = 0
        with self._lock:
            for bucket in context_buckets.values():
                kept = deque(
                    (instance for instance in bucket if value not in instance.data.values()),
                    maxlen=bucket.maxlen,
                )
                removed += len(bucket) - len(kept)
                bucket.clear()
                bucket.extend(kept)
        return removed

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

    def seed_input_values(self, by_resource: dict[str, dict[str, Any]], *, source: str) -> None:
        """Store externally-provided identifier values under specific resource buckets.

        Caller decides which buckets each value belongs to (typically by intersecting claim
        fields with each resource's queried fields) so unrelated buckets aren't contaminated.
        """
        for resource_name, data in by_resource.items():
            if data:
                self._store(
                    resource_name=resource_name,
                    data=dict(data),
                    source_operation=source,
                    status_code=200,
                    context={},
                )

    def record_request(
        self,
        *,
        operation: str,
        inputs: Sequence[ResourceFieldRef],
        case: Case,
        status_code: int,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Capture identifier values from a successful request.

        Mirrors `record_response` but reads from the case (path parameters and
        JSON body fields) instead of the response payload. Only fires on 2xx.
        """
        if not (200 <= status_code < 300):
            return
        for slot in inputs:
            if slot.resource_field is None:
                continue
            value = _extract_request_value(slot, case)
            if value is None:
                continue
            self._store(
                resource_name=slot.resource.name,
                data={slot.resource_field: value},
                source_operation=operation,
                status_code=status_code,
                context=context or {},
            )

    def _extract_payload(self, payload: object, descriptor: ResourceDescriptor) -> Iterable[dict[str, Any]]:
        pointer = descriptor.pointer
        if pointer in ("", None, "/"):
            target = payload
        elif "/*" in pointer:
            result = resolve_pointer_all(payload, pointer)
            if isinstance(result, Unresolvable):
                return ()
            # MANY descriptors yield one list per wildcard branch; flatten so each
            # resource instance becomes a single pool entry.
            if descriptor.cardinality == Cardinality.MANY:
                values: list[Any] = []
                for item in result:
                    if isinstance(item, list):
                        values.extend(item)
                    else:
                        values.append(item)
            else:
                values = result
            return _wrap_extracted_values(values, descriptor)
        else:
            target = resolve_pointer(payload, pointer)
            if target is UNRESOLVABLE:
                return ()

        if descriptor.extract_object_keys and descriptor.identifier_field is not None and isinstance(target, dict):
            # Map-by-id payload: keys ARE the identifier values.
            # Example: GET /teams/statuses -> {"frc1": {...}, "frc2": {...}}
            return [{descriptor.identifier_field: key} for key in target]

        if descriptor.cardinality == Cardinality.MANY and isinstance(target, list):
            values = target
        else:
            values = [target]

        return _wrap_extracted_values(values, descriptor)

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
        # Create a stable key for the context
        context_key = jsonschema_rs.canonical.json.to_string(context) if context else ""

        instance = ResourceInstance(
            data=data, source_operation=source_operation, status_code=status_code, context=context
        )

        with self._lock:
            context_buckets = self._resource_buckets.get(resource_name)
            if context_buckets is None:
                # Resource was not registered via descriptors (e.g. a request-only resource
                # captured from path/body slots whose response side has no extractable shape).
                context_buckets = {}
                self._resource_buckets[resource_name] = context_buckets
                self._context_order[resource_name] = deque()
            context_order = self._context_order[resource_name]

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


def _wrap_extracted_values(values: Iterable[Any], descriptor: ResourceDescriptor) -> list[dict[str, Any]]:
    """Wrap raw values as resource-instance dicts, lifting primitives onto `identifier_field`."""
    results: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, dict):
            results.append(value)
        elif descriptor.is_primitive_identifier and descriptor.identifier_field is not None:
            results.append({descriptor.identifier_field: value})
    return results


def _extract_request_value(slot: ResourceFieldRef, case: Case) -> Any:
    """Pull the value for a resource-field reference out of a generated Case.

    Returns None when the slot's location isn't supported (query, header,
    body-array-index) or when the named field is absent from the case.
    """
    if slot.parameter_location == ParameterLocation.PATH:
        return case.path_parameters.get(slot.parameter_name) if isinstance(slot.parameter_name, str) else None
    if slot.parameter_location == ParameterLocation.BODY:
        if not isinstance(slot.parameter_name, str):
            return None
        body = case.body
        if not isinstance(body, dict):
            return None
        return body.get(slot.parameter_name)
    return None
