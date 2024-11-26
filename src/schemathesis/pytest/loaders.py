from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.pytest.lazy import LazySchema


def from_fixture(name: str) -> LazySchema:
    from schemathesis.pytest.lazy import LazySchema

    return LazySchema(name)
