from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from schemathesis.config._env import resolve

# Define valid parameter locations from OpenAPI
ParameterLocation = Literal["path", "query", "header", "cookie", "body"]
VALID_LOCATIONS: list[ParameterLocation] = ["path", "query", "header", "cookie", "body"]


def load_parameters(data: dict[str, Any]) -> dict[str, ParameterOverride]:
    parameters = {}
    for key, value in data.get("parameters", {}).items():
        parameters[key] = ParameterOverride.from_key_value(key, value)
    return parameters


@dataclass
class ParameterOverride:
    """Configuration for parameter value overrides."""

    name: str
    value: Any
    location: ParameterLocation | None

    __slots__ = ("name", "value", "location")

    def __init__(self, name: str, value: Any, location: ParameterLocation | None = None) -> None:
        self.name = name
        self.value = value
        self.location = location

    @classmethod
    def from_key_value(cls, key: str, value: Any) -> ParameterOverride:
        if isinstance(value, str):
            value = resolve(value, None)
        if "." in key:
            location, name = key.split(".", 1)
            if location in VALID_LOCATIONS:
                _location = cast(ParameterLocation, location)
                return cls(name=name, value=value, location=_location)
            # It could be just a parameter with a dot
        return cls(name=key, value=value, location=None)
