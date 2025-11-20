from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias

from jsonschema_rs import (
    Draft4Validator,
    Draft6Validator,
    Draft7Validator,
    Draft201909Validator,
    Draft202012Validator,
    ValidationError,
    validate,
    validator_for,
)

if TYPE_CHECKING:
    import jsonschema.protocols

Validator: TypeAlias = Draft4Validator | Draft6Validator | Draft7Validator | Draft201909Validator | Draft202012Validator


def from_jsonschema(validator_cls: type[jsonschema.protocols.Validator]) -> type[Validator]:
    import jsonschema

    mapping = {
        jsonschema.Draft4Validator: Draft4Validator,
        jsonschema.Draft6Validator: Draft6Validator,
        jsonschema.Draft7Validator: Draft7Validator,
        jsonschema.Draft201909Validator: Draft201909Validator,
        jsonschema.Draft202012Validator: Draft202012Validator,
    }

    try:
        return mapping[validator_cls]
    except KeyError:
        raise ValueError(f"Unknown validator class: {validator_cls}") from None


__all__ = [
    "Draft4Validator",
    "Draft6Validator",
    "Draft7Validator",
    "Draft201909Validator",
    "Draft202012Validator",
    "Validator",
    "ValidationError",
    "validate",
    "validator_for",
    "from_jsonschema",
]
