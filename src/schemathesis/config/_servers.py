from __future__ import annotations

from dataclasses import dataclass

from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve


@dataclass(repr=False, slots=True)
class ServersConfig(DiffBase):
    variables: dict[str, str]

    def __init__(self, *, variables: dict[str, str] | None = None) -> None:
        self.variables = variables or {}

    @classmethod
    def from_dict(cls, data: dict) -> ServersConfig:
        raw_variables = data.get("variables", {})
        return cls(variables={key: resolve(value) for key, value in raw_variables.items()})
