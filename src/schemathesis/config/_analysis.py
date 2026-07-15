from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemathesis.config._diff_base import DiffBase


@dataclass(repr=False)
class ConstantsConfig(DiffBase):
    """Reuse literal values extracted from the application's Python source during generation."""

    enabled: bool

    __slots__ = ("enabled", "_is_default")

    def __init__(self, *, enabled: bool | None = None) -> None:
        self._is_default = enabled is None
        object.__setattr__(self, "enabled", True if enabled is None else enabled)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "enabled":
            object.__setattr__(self, "_is_default", False)
        object.__setattr__(self, name, value)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConstantsConfig:
        return cls(enabled=data.get("enabled"))

    @classmethod
    def from_hierarchy(cls, configs: list[ConstantsConfig]) -> ConstantsConfig:  # type: ignore[override]
        if len(configs) == 1:
            return configs[0]
        return next((cls(enabled=config.enabled) for config in configs if not config._is_default), cls())


@dataclass(repr=False)
class AnalysisConfig(DiffBase):
    """One-shot inspection of the application under test that feeds generation."""

    constants: ConstantsConfig

    __slots__ = ("constants",)

    def __init__(self, *, constants: ConstantsConfig | None = None) -> None:
        self.constants = constants or ConstantsConfig()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalysisConfig:
        return cls(constants=ConstantsConfig.from_dict(data["constants"]) if "constants" in data else None)
