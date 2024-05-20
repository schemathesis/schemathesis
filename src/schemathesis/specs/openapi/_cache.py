from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Tuple

if TYPE_CHECKING:
    from ...models import APIOperation
    from ...schemas import APIOperationMap


@dataclass
class OperationCacheEntry:
    path: str
    method: str
    # The resolution scope of the operation
    scope: str
    # Parent path item
    path_item: dict[str, Any]
    # Unresolved operation definition
    operation: dict[str, Any]
    __slots__ = ("path", "method", "scope", "path_item", "operation")


# During traversal, we need to keep track of the scope, path, and method
TraversalKey = Tuple[str, str, str]
OperationId = str
Reference = str


@dataclass
class OperationCache:
    """Cache for Open API operations.

    This cache contains multiple levels to avoid unnecessary parsing of the schema.
    """

    # Cache to avoid schema traversal on every access
    _id_to_definition: dict[OperationId, OperationCacheEntry] = field(default_factory=dict)
    # Map map between 1st & 2nd level cache keys
    # Even though 1st level keys could be directly mapped to Python objects in memory, we need to keep them separate
    # to ensure a single owner of the operation instance.
    _id_to_operation: dict[OperationId, int] = field(default_factory=dict)
    _traversal_key_to_operation: dict[TraversalKey, int] = field(default_factory=dict)
    _reference_to_operation: dict[Reference, int] = field(default_factory=dict)
    # The actual operations
    _operations: list[APIOperation] = field(default_factory=list)
    # Cache for operation maps
    _maps: dict[str, APIOperationMap] = field(default_factory=dict)

    @property
    def known_operation_ids(self) -> list[str]:
        return list(self._id_to_definition)

    @property
    def has_ids_to_definitions(self) -> bool:
        return bool(self._id_to_definition)

    def _append_operation(self, operation: APIOperation) -> int:
        idx = len(self._operations)
        self._operations.append(operation)
        return idx

    def insert_definition_by_id(
        self,
        operation_id: str,
        path: str,
        method: str,
        scope: str,
        path_item: dict[str, Any],
        operation: dict[str, Any],
    ) -> None:
        """Insert a new operation definition into cache."""
        self._id_to_definition[operation_id] = OperationCacheEntry(
            path=path, method=method, scope=scope, path_item=path_item, operation=operation
        )

    def get_definition_by_id(self, operation_id: str) -> OperationCacheEntry:
        """Get an operation definition by its ID."""
        # TODO: Avoid KeyError in the future
        return self._id_to_definition[operation_id]

    def insert_operation(
        self,
        operation: APIOperation,
        *,
        traversal_key: TraversalKey,
        operation_id: str | None = None,
        reference: str | None = None,
    ) -> None:
        """Insert a new operation into cache by one or multiple keys."""
        idx = self._append_operation(operation)
        self._traversal_key_to_operation[traversal_key] = idx
        if operation_id is not None:
            self._id_to_operation[operation_id] = idx
        if reference is not None:
            self._reference_to_operation[reference] = idx

    def get_operation_by_id(self, operation_id: str) -> APIOperation | None:
        """Get an operation by its ID."""
        idx = self._id_to_operation.get(operation_id)
        if idx is not None:
            return self._operations[idx]
        return None

    def get_operation_by_reference(self, reference: str) -> APIOperation | None:
        """Get an operation by its reference."""
        idx = self._reference_to_operation.get(reference)
        if idx is not None:
            return self._operations[idx]
        return None

    def get_operation_by_traversal_key(self, key: TraversalKey) -> APIOperation | None:
        """Get an operation by its traverse key."""
        idx = self._traversal_key_to_operation.get(key)
        if idx is not None:
            return self._operations[idx]
        return None

    def get_map(self, key: str) -> APIOperationMap | None:
        return self._maps.get(key)

    def insert_map(self, key: str, value: APIOperationMap) -> None:
        self._maps[key] = value
