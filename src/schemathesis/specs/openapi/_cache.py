from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...models import APIOperation


@dataclass
class OperationCacheEntry:
    path: str
    method: str
    # The resolution scope of the operation
    scope: str
    # Parameters shared among all operations in the path
    shared_parameters: list[dict[str, Any]]
    # Unresolved operation definition
    operation: dict[str, Any]
    __slots__ = ("path", "method", "scope", "shared_parameters", "operation")


OperationId = str


@dataclass
class OperationCache:
    """Cache for Open API operations.

    This cache contains multiple levels to avoid unnecessary parsing of the schema.

    The first level cache contains operation IDs and their metadata. The second level cache contains
    initialized operation instances.

    The first level is populated eagerly because it is cheap. It is mostly a dict traversal and
    a bit of reference resolving. The entries there does not own the data, they are just references to the schema.

    The second level is populated lazily because it is more expensive. It requires parsing the schema, its parameters
    and some more elaborate reference resolution.
    """

    _ids_to_definitions: dict[OperationId, OperationCacheEntry] = field(default_factory=dict)
    _ids_to_operations: dict[OperationId, APIOperation] = field(default_factory=dict)

    @property
    def known_operation_ids(self) -> list[str]:
        return list(self._ids_to_definitions)

    @property
    def has_ids_to_definitions(self) -> bool:
        return bool(self._ids_to_definitions)

    def insert_definition_by_id(
        self,
        operation_id: str,
        path: str,
        method: str,
        scope: str,
        shared_parameters: list[dict[str, Any]],
        operation: dict[str, Any],
    ) -> None:
        """Insert a new operation definition into cache."""
        self._ids_to_definitions[operation_id] = OperationCacheEntry(
            path=path, method=method, scope=scope, shared_parameters=shared_parameters, operation=operation
        )

    def get_definition_by_id(self, operation_id: str) -> OperationCacheEntry:
        """Get an operation definition by its ID."""
        # TODO: Avoid KeyError in the future
        return self._ids_to_definitions[operation_id]

    def insert_operation_by_id(self, operation_id: str, operation: APIOperation) -> None:
        """Insert a new operation into cache."""
        self._ids_to_operations[operation_id] = operation

    def get_operation_by_id(self, operation_id: str) -> APIOperation | None:
        """Get an operation by its ID."""
        return self._ids_to_operations.get(operation_id)
