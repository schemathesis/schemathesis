from __future__ import annotations

from typing import Any, Literal

from schemathesis.config._dictionaries import (
    SUPPORTED_LOCATION_PREFIXES,
    DictionaryDefinition,
    ParameterDictionaryBinding,
    require_known_dictionary,
)
from schemathesis.config._env import resolve
from schemathesis.config._error import ConfigError

# Define valid parameter locations from OpenAPI
ParameterLocation = Literal["path", "query", "header", "cookie", "body"]
VALID_LOCATIONS: list[ParameterLocation] = ["path", "query", "header", "cookie", "body"]


def load_parameters(
    data: dict[str, Any], *, dictionaries: dict[str, DictionaryDefinition] | None = None
) -> dict[str, Any]:
    dictionaries = dictionaries or {}
    parameters: dict[str, Any] = {}
    for key, value in data.get("parameters", {}).items():
        if isinstance(value, dict) and "dictionary" in value:
            _validate_dictionary_location_prefix(key)
            name: str = value["dictionary"]
            require_known_dictionary(f"Parameter `{key}`", name, dictionaries)
            parameters[key] = ParameterDictionaryBinding(
                dictionary=name, probability=float(value.get("probability", 1.0))
            )
        elif isinstance(value, str):
            parameters[key] = resolve(value)
        else:
            parameters[key] = value
    return parameters


def _validate_dictionary_location_prefix(key: str) -> None:
    if "." not in key:
        return
    prefix, _, rest = key.partition(".")
    if not rest or prefix in SUPPORTED_LOCATION_PREFIXES:
        return
    allowed = ", ".join(SUPPORTED_LOCATION_PREFIXES)
    raise ConfigError(
        f"Parameter `{key}` uses unknown location prefix `{prefix}` for dictionary binding; allowed: {allowed}"
    )
