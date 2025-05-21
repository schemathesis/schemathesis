from __future__ import annotations

from typing import Any, Literal

from schemathesis.config._env import resolve

# Define valid parameter locations from OpenAPI
ParameterLocation = Literal["path", "query", "header", "cookie", "body"]
VALID_LOCATIONS: list[ParameterLocation] = ["path", "query", "header", "cookie", "body"]


def load_parameters(data: dict[str, Any]) -> dict[str, Any]:
    parameters = {}
    for key, value in data.get("parameters", {}).items():
        if isinstance(value, str):
            parameters[key] = resolve(value)
        else:
            parameters[key] = value
    return parameters
