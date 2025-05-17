from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from schemathesis.config._env import resolve

# Define valid parameter locations from OpenAPI
ParameterLocation = Literal["path", "query", "header", "cookie", "body"]
VALID_LOCATIONS: list[ParameterLocation] = ["path", "query", "header", "cookie", "body"]


def load_parameters(data: dict[str, Any]) -> dict[str, ParameterOverride]:
    parameters = {}
    for key, value in data.get("parameters", {}).items():
        parameters[key] = ParameterOverride.from_value(value)
    return parameters


@dataclass
class ParameterOverride:
    """Configuration for parameter value overrides."""

    value: Any

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value

    @classmethod
    def from_value(cls, value: Any) -> ParameterOverride:
        if isinstance(value, str):
            value = resolve(value)
        return cls(value=value)
