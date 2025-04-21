from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

from schemathesis.core.errors import SchemathesisError

if TYPE_CHECKING:
    from jsonschema import ValidationError


class ConfigError(SchemathesisError):
    """Invalid configuration."""

    @classmethod
    def from_validation_error(cls, error: ValidationError) -> ConfigError:
        message = error.message
        if error.validator == "enum":
            message = _format_enum_error(error)
        elif error.validator == "minimum":
            message = _format_minimum_error(error)
        elif error.validator == "required":
            message = _format_required_error(error)
        elif error.validator == "type":
            message = _format_type_error(error)
        elif error.validator == "additionalProperties":
            message = _format_additional_properties_error(error)
        elif error.validator == "anyOf":
            message = _format_anyof_error(error)
        return cls(message)


def _format_minimum_error(error: ValidationError) -> str:
    assert isinstance(error.validator_value, (int, float))
    section = path_to_section_name(list(error.path)[:-1] if error.path else [])
    assert error.path

    prop_name = error.path[-1]
    min_value = error.validator_value
    actual_value = error.instance

    return (
        f"Error in {section} section:\n  Value too low:\n\n"
        f"  - '{prop_name}' → Must be at least {min_value}, but got {actual_value}."
    )


def _format_required_error(error: ValidationError) -> str:
    assert isinstance(error.validator_value, list)
    missing_keys = sorted(set(error.validator_value) - set(error.instance))

    section = path_to_section_name(list(error.path))

    details = "\n".join(f"  - '{key}'" for key in missing_keys)
    return f"Error in {section} section:\n  Missing required properties:\n\n{details}\n\n"


def _format_enum_error(error: ValidationError) -> str:
    assert isinstance(error.validator_value, list)
    valid_values = sorted(error.validator_value)

    path = list(error.path)

    if path and isinstance(path[-1], int):
        idx = path[-1]
        prop_name = path[-2]
        section_path = path[:-2]
        description = f"Item #{idx} in the '{prop_name}' array"
    else:
        prop_name = path[-1] if path else "value"
        section_path = path[:-1]
        description = f"'{prop_name}'"

    suggestion = ""
    if isinstance(error.instance, str) and all(isinstance(v, str) for v in valid_values):
        match = _find_closest_match(error.instance, valid_values)
        if match:
            suggestion = f" Did you mean '{match}'?"

    section = path_to_section_name(section_path)
    valid_values_str = ", ".join(repr(v) for v in valid_values)
    return (
        f"Error in {section} section:\n  Invalid value:\n\n"
        f"  - {description} → '{error.instance}' is not a valid value.{suggestion}\n\n"
        f"Valid values are: {valid_values_str}."
    )


def _format_type_error(error: ValidationError) -> str:
    expected = error.validator_value
    assert isinstance(expected, (str, list))
    section = path_to_section_name(list(error.path)[:-1] if error.path else [])
    assert error.path

    type_phrases = {
        "object": "an object",
        "array": "an array",
        "number": "a number",
        "boolean": "a boolean",
        "string": "a string",
        "integer": "an integer",
        "null": "null",
    }
    message = f"Error in {section} section:\n  Type error:\n\n  - '{error.path[-1]}' → Must be "

    if isinstance(expected, list):
        message += f"one of: {' or '.join(expected)}"
    else:
        message += type_phrases[expected]
    actual = type(error.instance).__name__
    message += f", but got {actual}: {error.instance}"
    return message


def _format_additional_properties_error(error: ValidationError) -> str:
    valid = list(error.schema.get("properties", {}))
    unknown = sorted(set(error.instance) - set(valid))
    valid_list = ", ".join(f"'{prop}'" for prop in valid)
    section = path_to_section_name(list(error.path))

    details = []
    for prop in unknown:
        match = _find_closest_match(prop, valid)
        if match:
            details.append(f"- '{prop}' → Did you mean '{match}'?")
        else:
            details.append(f"- '{prop}'")

    return (
        f"Error in {section} section:\n  Unknown properties:\n\n"
        + "\n".join(f"  {detail}" for detail in details)
        + f"\n\nValid properties for {section} are: {valid_list}."
    )


def _format_anyof_error(error: ValidationError) -> str:
    if list(error.schema_path) == ["properties", "operations", "items", "anyOf"]:
        section = path_to_section_name(list(error.path))
        return (
            f"Error in {section} section:\n  At least one filter is required when defining [[operations]].\n\n"
            "Please specify at least one include or exclude filter property (e.g., include-path, exclude-tag, etc.)."
        )
    return error.message


def path_to_section_name(path: list[int | str]) -> str:
    """Convert a JSON path to a TOML-like section name."""
    if not path:
        return "root"

    return f"[{'.'.join(str(p) for p in path)}]"


def _find_closest_match(value: str, variants: list[str]) -> str | None:
    matches = difflib.get_close_matches(value, variants, n=1, cutoff=0.6)
    return matches[0] if matches else None
