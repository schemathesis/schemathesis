from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve


@dataclass(repr=False, slots=True)
class CacheConfig(DiffBase):
    enabled: bool
    directory: Path | None

    def __init__(self, *, enabled: bool = True, directory: str | None = None) -> None:
        self.enabled = enabled
        resolved = resolve(directory)
        self.directory = Path(resolved) if resolved is not None else None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CacheConfig:
        return cls(enabled=data.get("enabled", True), directory=data.get("directory"))
