from __future__ import annotations

from typing import Any, Protocol, TypeVar

T = TypeVar("T", covariant=True)


class ResponsesContainer(Protocol[T]):
    def find_by_status_code(self, status_code: int) -> T | None: ...  # pragma: no cover
    def add(self, status_code: str, definition: dict[str, Any]) -> T: ...  # pragma: no cover
