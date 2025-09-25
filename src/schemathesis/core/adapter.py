from __future__ import annotations

from typing import Protocol, TypeVar

T = TypeVar("T", covariant=True)


class ResponsesContainer(Protocol[T]):
    def find_by_status_code(self, status_code: int) -> T | None: ...  # pragma: no cover
