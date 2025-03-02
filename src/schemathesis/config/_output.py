from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemathesis.config._diff_base import DiffBase


@dataclass(repr=False)
class OutputConfig(DiffBase):
    sanitize: bool
    truncate: bool

    __slots__ = ("sanitize", "truncate")

    def __init__(
        self,
        *,
        sanitize: bool = True,
        truncate: bool = True,
    ) -> None:
        self.sanitize = sanitize
        self.truncate = truncate

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OutputConfig:
        return cls(
            sanitize=data.get("sanitize", True),
            truncate=data.get("truncate", True),
        )
