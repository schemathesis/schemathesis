from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from schemathesis.config._diff_base import DiffBase


@dataclass(repr=False)
class FuzzConfig(DiffBase):
    max_time: int | None

    __slots__ = ("max_time",)

    def __init__(self, *, max_time: int | None = None) -> None:
        self.max_time = max_time

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> FuzzConfig:
        return cls(max_time=cast("int | None", data.get("max-time")))
