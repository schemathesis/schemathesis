from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...schemas import APIOperation, APIOperationMap


@dataclass
class OperationCache:
    _maps: dict[str, APIOperationMap]
    _operations: dict[str, APIOperation]

    __slots__ = ("_maps", "_operations")

    def __init__(
        self,
        _maps: dict[str, APIOperationMap] | None = None,
        _operations: dict[str, APIOperation] | None = None,
    ) -> None:
        self._maps = _maps or {}
        self._operations = _operations or {}

    def get_map(self, key: str) -> APIOperationMap | None:
        return self._maps.get(key)

    def insert_map(self, key: str, value: APIOperationMap) -> None:
        self._maps[key] = value

    def get_operation(self, key: str) -> APIOperation | None:
        return self._operations.get(key)

    def insert_operation(self, key: str, value: APIOperation) -> None:
        self._operations[key] = value
