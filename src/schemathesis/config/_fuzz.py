from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemathesis.config._diff_base import DiffBase
from schemathesis.core import DEFAULT_MAX_SCENARIO_STEPS


@dataclass(repr=False)
class FuzzConfig(DiffBase):
    max_time: int | None
    max_steps: int

    __slots__ = ("max_time", "max_steps")

    def __init__(self, *, max_time: int | None = None, max_steps: int | None = None) -> None:
        self.max_time = max_time
        self.max_steps = max_steps or DEFAULT_MAX_SCENARIO_STEPS

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FuzzConfig:
        return cls(max_time=data.get("max-time"), max_steps=data.get("max-steps"))
