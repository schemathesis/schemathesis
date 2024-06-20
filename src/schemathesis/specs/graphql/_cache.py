from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...models import APIOperation
    from ...schemas import APIOperationMap


@dataclass
class OperationCache:
    _maps: dict[str, APIOperationMap] = field(default_factory=dict)
    _operations: dict[str, APIOperation] = field(default_factory=dict)

    def get_map(self, key: str) -> APIOperationMap | None:
        return self._maps.get(key)

    def insert_map(self, key: str, value: APIOperationMap) -> None:
        self._maps[key] = value

    def get_operation(self, key: str) -> APIOperation | None:
        return self._operations.get(key)

    def insert_operation(self, key: str, value: APIOperation) -> None:
        self._operations[key] = value
