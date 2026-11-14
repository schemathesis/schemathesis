from __future__ import annotations

import threading
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import jsonschema_rs

from schemathesis.core.parameters import ParameterLocation, plain_str_values
from schemathesis.core.transforms import UNRESOLVABLE, Unresolvable, resolve_pointer, resolve_pointer_all
from schemathesis.core.transport import status_code_matches
from schemathesis.resources.descriptors import Cardinality, ResourceDescriptor, ResourceFieldRef

if TYPE_CHECKING:
    from schemathesis.generation.case import Case

# Instances kept per context (e.g. per owner of pets) within a resource type, so parent resources stay diverse.
PER_CONTEXT_CAPACITY = 500

MAX_CONTEXTS_PER_TYPE = 20

# Once real values exist, one draw in this many still offers the spec's example values.
EXAMPLE_DRAW_PERIOD = 5


@dataclass(slots=True)
class ResourceInstance:
    """Concrete resource captured from an API response."""

    data: dict[str, Any]
    source_operation: str
    status_code: int
    context: dict[str, Any]
    # Taken from a response example in the spec rather than from the API itself.
    from_example: bool = False
    # Identity whose request produced the resource; `None` outside multi-identity auth.
    identity: str | None = None
    # Query of that request, so it can be repeated as sent.
    query: dict[str, Any] = field(default_factory=dict)
    # Values that request sent to name existing objects, so returning them proves no ownership.
    referenced: frozenset[str] = frozenset()


class ResourceRepository:
    """Thread-safe cache of resources from API responses.

    Eviction per context keeps parent resources diverse, e.g. pets of many owners rather than one.
    """

    __slots__ = (
        "_descriptors_by_operation",
        "_resource_buckets",
        "_example_turns",
        "_context_order",
        "_lock",
    )

    def __init__(self, descriptors: Sequence[ResourceDescriptor]) -> None:
        self._descriptors_by_operation: dict[str, list[ResourceDescriptor]] = defaultdict(list)
        # resource_name -> context_key -> instances
        self._resource_buckets: dict[str, dict[str, deque[ResourceInstance]]] = {}
        self._example_turns: dict[str, int] = {}
        # Context insertion order, for FIFO eviction
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
        """Cached instances of a resource type across all contexts."""
        # Under the lock: workers record responses into these buckets while others draw from them.
        with self._lock:
            context_buckets = self._resource_buckets.get(resource_name)
            if not context_buckets:
                return ()
            instances: list[ResourceInstance] = []
            for bucket in context_buckets.values():
                instances.extend(bucket)
            # Spec examples stand in until the API returns real values, then still come up every few draws:
            # a plausible id the API never issued reaches error paths that real ones do not.
            live = tuple(instance for instance in instances if not instance.from_example)
            if not live or len(live) == len(instances):
                return tuple(instances)
            turn = self._example_turns.get(resource_name, 0) + 1
            self._example_turns[resource_name] = turn
            if turn % EXAMPLE_DRAW_PERIOD == 0:
                return tuple(instances)
            return live

    def remove_by_value(self, resource_name: str, resource_field: str, value: object) -> int:
        """Drop instances whose `resource_field` equals `value`, e.g. after a DELETE; returns the number removed."""
        context_buckets = self._resource_buckets.get(resource_name)
        if not context_buckets:
            return 0
        removed = 0
        with self._lock:
            for bucket in context_buckets.values():
                kept = deque(
                    (instance for instance in bucket if instance.data.get(resource_field) != value),
                    maxlen=bucket.maxlen,
                )
                removed += len(bucket) - len(kept)
                bucket.clear()
                bucket.extend(kept)
        return removed

    def record_response(
        self,
        *,
        operation: str,
        status_code: int,
        payload: Any,
        context: dict[str, Any] | None = None,
        from_example: bool = False,
        identity: str | None = None,
        query: dict[str, Any] | None = None,
        referenced: frozenset[str] = frozenset(),
        documented_status: str | None = None,
    ) -> None:
        """Capture resources from a response with the operation's descriptors.

        `documented_status` is the schema's response key for `status_code`; without it, or with no descriptor for it,
        any 2xx descriptor matches.
        """
        descriptors = self._descriptors_by_operation.get(operation, [])
        if documented_status is not None and all(
            descriptor.status_code != documented_status for descriptor in descriptors
        ):
            # A status schema nothing reads is often the operation's resource under another name, e.g. `OrderCreated`.
            documented_status = None

        for descriptor in descriptors:
            if documented_status is not None:
                # Another documented 2xx response may describe a different resource.
                if descriptor.status_code != documented_status:
                    continue
            # Any 2xx matches a 2xx descriptor: a schema may say "200" while the server returns "201"
            elif not status_code_matches(descriptor.status_code, status_code) and not (
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
                    from_example=from_example,
                    identity=identity,
                    query=query or {},
                    referenced=referenced,
                )

    def seed_input_values(self, by_resource: dict[str, dict[str, Any]], *, source: str) -> None:
        """Store external identifier values in the buckets the caller picks, so unrelated buckets stay clean."""
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
        path_parameters: Mapping[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        identity: str | None = None,
        query: dict[str, Any] | None = None,
        referenced: frozenset[str] = frozenset(),
    ) -> None:
        """Capture identifier values from the path parameters and JSON body of a 2xx request.

        `path_parameters` holds the case's path parameters in their declared types, when the caller knows them.
        """
        if not (200 <= status_code < 300):
            return
        for slot in inputs:
            if slot.resource_field is None:
                continue
            value = _extract_request_value(
                slot, case, case.path_parameters if path_parameters is None else path_parameters
            )
            if value is None:
                continue
            self._store(
                resource_name=slot.resource.name,
                data={slot.resource_field: value},
                source_operation=operation,
                status_code=status_code,
                context=context or {},
                identity=identity,
                query=query or {},
                referenced=referenced,
            )

    def _extract_payload(self, payload: object, descriptor: ResourceDescriptor) -> Iterable[dict[str, Any]]:
        pointer = descriptor.pointer
        if pointer in ("", None, "/"):
            target = payload
        elif "/*" in pointer:
            result = resolve_pointer_all(payload, pointer)
            if isinstance(result, Unresolvable):
                return ()
            # MANY yields a list per wildcard branch; flatten so each instance is one pool entry
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
            # A map keyed by identifier, e.g. `{"a1": {...}, "b2": {...}}`
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
        from_example: bool = False,
        identity: str | None = None,
        query: dict[str, Any] | None = None,
        referenced: frozenset[str] = frozenset(),
    ) -> None:
        """Store an instance, capping instances per context and evicting the oldest context when full."""
        # Delimited markers carry transport-only data; resource identifiers use the plain form.
        data = plain_str_values(data)
        context = plain_str_values(context) if context else context
        context_key = jsonschema_rs.canonical.json.to_string(context) if context else ""

        instance = ResourceInstance(
            data=data,
            source_operation=source_operation,
            status_code=status_code,
            context=context,
            from_example=from_example,
            identity=identity,
            query=query or {},
            referenced=referenced,
        )

        with self._lock:
            context_buckets = self._resource_buckets.get(resource_name)
            if context_buckets is None:
                # A resource without descriptors, e.g. one captured only from request slots
                context_buckets = {}
                self._resource_buckets[resource_name] = context_buckets
                self._context_order[resource_name] = deque()
            context_order = self._context_order[resource_name]

            if context_key not in context_buckets:
                if len(context_buckets) >= MAX_CONTEXTS_PER_TYPE:
                    oldest_key = context_order.popleft()
                    del context_buckets[oldest_key]
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


def _extract_request_value(slot: ResourceFieldRef, case: Case, path_parameters: Mapping[str, Any]) -> Any:
    """The slot's value from path parameters or a top-level body field; `None` for other locations or absent fields."""
    if slot.parameter_location == ParameterLocation.PATH:
        return path_parameters.get(slot.parameter_name) if isinstance(slot.parameter_name, str) else None
    if slot.parameter_location == ParameterLocation.BODY:
        if not isinstance(slot.parameter_name, str):
            return None
        body = case.body
        if not isinstance(body, dict):
            return None
        return body.get(slot.parameter_name)
    return None
