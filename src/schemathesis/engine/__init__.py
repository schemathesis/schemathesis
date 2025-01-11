from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from schemathesis.engine.config import EngineConfig

if TYPE_CHECKING:
    from schemathesis.engine.core import Engine
    from schemathesis.schemas import BaseSchema


class Status(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    INTERRUPTED = "interrupted"
    SKIP = "skip"

    def __lt__(self, other: Status) -> bool:  # type: ignore[override]
        return _STATUS_ORDER[self] < _STATUS_ORDER[other]


_STATUS_ORDER = {Status.SUCCESS: 0, Status.FAILURE: 1, Status.ERROR: 2, Status.INTERRUPTED: 3, Status.SKIP: 4}


def from_schema(schema: BaseSchema, *, config: EngineConfig | None = None) -> Engine:
    from .core import Engine

    return Engine(schema=schema, config=config or EngineConfig())
