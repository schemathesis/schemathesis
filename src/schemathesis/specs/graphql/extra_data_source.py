"""Runtime resource pool for GraphQL identifier values."""

from __future__ import annotations

import json
import threading
from collections import deque
from typing import TYPE_CHECKING, Any, Final, cast

import graphql

from schemathesis.specs.graphql._helpers import _root_type_for, _unwrap
from schemathesis.specs.graphql.inference import OperationRole, classify_operation
from schemathesis.specs.graphql.substitution import iter_operation_pool_values

if TYPE_CHECKING:
    from random import Random

    from schemathesis.core.parameters import ParameterLocation
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.graphql.schemas import GraphQLOperationDefinition

# Per-parent cap on captured values; older entries are evicted FIFO.
DEFAULT_MAX_PER_KEY: Final = 32


def _parse_response_data(body: bytes) -> dict[str, Any] | None:
    """Return `data` from a successful GraphQL response, or None if missing/errored."""
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def _is_cleanup(operation: APIOperation) -> bool:
    definition = cast("GraphQLOperationDefinition", operation.definition)
    return classify_operation(field_name=definition.field_name, root_type=definition.root_type) == OperationRole.CLEANUP


class GraphQLResourcePool:
    """Cross-test-case pool of GraphQL identifier values."""

    __slots__ = ("_data", "_lock", "_max_per_key", "_schema", "_tombstoned")

    def __init__(
        self,
        *,
        client_schema: graphql.GraphQLSchema,
        max_per_key: int = DEFAULT_MAX_PER_KEY,
    ) -> None:
        self._schema = client_schema
        self._max_per_key = max_per_key
        self._data: dict[str, deque[str]] = {}
        self._tombstoned: set[tuple[str, str]] = set()
        self._lock = threading.RLock()

    def should_record(self, *, operation: str) -> bool:
        return True

    def should_record_request(self, *, operation: str) -> bool:
        return False

    def record_response(
        self,
        *,
        operation: APIOperation,
        response: Response,
        case: Case,
    ) -> None:
        body = response.content
        if not body or not isinstance(case.body, str):
            return
        try:
            document = graphql.parse(case.body)
        except graphql.GraphQLSyntaxError:
            return
        operation_node = next(
            (d for d in document.definitions if isinstance(d, graphql.OperationDefinitionNode)),
            None,
        )
        if operation_node is None:
            return
        data = _parse_response_data(body)
        if data is None:
            return
        self.capture(operation_node=operation_node, response_data=data)
        if _is_cleanup(operation):
            for parent_type_name, value in iter_operation_pool_values(operation_node, self._schema):
                self.tombstone(parent_type_name=parent_type_name, value=value)

    def record_request(self, *, operation: APIOperation, case: Case, status_code: int) -> None:
        return None  # pragma: no cover

    def record_successful_delete(self, *, operation: APIOperation, case: Case) -> None:
        # Tombstoning runs from `record_response`; this engine hook has no extra work for GraphQL.
        return None  # pragma: no cover

    def pick_captured_value(
        self,
        *,
        operation: APIOperation,
        location: ParameterLocation,
        name: str,
    ) -> Any | None:
        # Required by Protocol; GraphQL substitution calls draw() directly.
        return None  # pragma: no cover

    def pick_correlated_values(
        self,
        *,
        operation: APIOperation,
    ) -> dict[tuple[ParameterLocation, str], Any]:
        # Required by Protocol; GraphQL substitution calls draw() directly.
        return {}  # pragma: no cover

    def capture(
        self,
        *,
        operation_node: graphql.OperationDefinitionNode,
        response_data: dict[str, Any],
    ) -> None:
        root_type = _root_type_for(self._schema, operation_node.operation)
        if root_type is None:
            return
        self._walk_selection(operation_node.selection_set, root_type, response_data)

    def capture_response(
        self,
        *,
        response_body: bytes,
        operation_node: graphql.OperationDefinitionNode,
    ) -> None:
        data = _parse_response_data(response_body)
        if data is not None:
            self.capture(operation_node=operation_node, response_data=data)

    def draw(self, *, parent_type_name: str, random: Random) -> str | None:
        with self._lock:
            entries = self._data.get(parent_type_name)
            if not entries:
                return None
            return random.choice(list(entries))

    def tombstone(self, *, parent_type_name: str, value: str) -> None:
        """Mark a value as deleted: evict it from the pool and prevent re-capture."""
        with self._lock:
            self._tombstoned.add((parent_type_name, value))
            entries = self._data.get(parent_type_name)
            if entries is not None and value in entries:
                self._data[parent_type_name] = deque((v for v in entries if v != value), maxlen=self._max_per_key)

    def _walk_selection(
        self,
        selection_set: graphql.SelectionSetNode | None,
        parent_type: graphql.GraphQLObjectType,
        data: Any,
    ) -> None:
        if selection_set is None or not isinstance(data, dict):
            return
        for selection in selection_set.selections:
            if isinstance(selection, graphql.FieldNode):
                self._walk_field(selection, parent_type, data)

    def _walk_field(
        self,
        field: graphql.FieldNode,
        parent_type: graphql.GraphQLObjectType,
        data: dict[str, Any],
    ) -> None:
        real_name = field.name.value
        response_key = field.alias.value if field.alias else real_name
        if response_key not in data:
            return
        field_def = parent_type.fields.get(real_name)
        if field_def is None:
            return
        value = data[response_key]
        unwrapped = _unwrap(field_def.type)

        if isinstance(unwrapped, graphql.GraphQLScalarType):
            if real_name == "id" and isinstance(value, str):
                self._store(parent_type.name, value)
            return

        if isinstance(unwrapped, graphql.GraphQLObjectType):
            if isinstance(value, list):
                for item in value:
                    self._walk_selection(field.selection_set, unwrapped, item)
            else:
                self._walk_selection(field.selection_set, unwrapped, value)

    def _store(self, parent_type_name: str, value: str) -> None:
        with self._lock:
            if (parent_type_name, value) in self._tombstoned:
                return
            entries = self._data.get(parent_type_name)
            if entries is None:
                entries = deque(maxlen=self._max_per_key)
                self._data[parent_type_name] = entries
            entries.append(value)
