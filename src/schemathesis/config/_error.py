from __future__ import annotations

import datetime
import difflib
import json
import re
from typing import TYPE_CHECKING

from jsonschema_rs import ValidationErrorKind

from schemathesis.config._validator import CONFIG_SCHEMA, InstancePath
from schemathesis.core.errors import SchemathesisError
from schemathesis.core.transforms import resolve_path

if TYPE_CHECKING:
    from jsonschema_rs import ValidationError


class ConfigError(SchemathesisError):
    """Invalid configuration."""

    @classmethod
    def from_validation_error(cls, error: ValidationError, replaced: dict[InstancePath, object]) -> ConfigError:
        return cls(_format_validation_error(error, replaced))

    @classmethod
    def from_temporal_value(cls, path: InstancePath, value: object) -> ConfigError:
        section = path_to_section_name(list(path[:-1]))
        return cls(
            f"Error in {section} section:\n  Type error:\n\n"
            f"  - '{path[-1]}' -> Dates and times are not supported, but got {_type_name(value)}: "
            f"{_format_value(value)}. Quote the value to pass it as a string."
        )

    @classmethod
    def from_invalid_value(cls, *, section: str, name: str, value: object, valid: list[str]) -> ConfigError:
        suggestion = ""
        if isinstance(value, str):
            match = _find_closest_match(value, valid)
            if match:
                suggestion = f" Did you mean '{match}'?"
        return cls(
            f"Error in {section} section:\n  Invalid value:\n\n"
            f"  - '{name}' -> {_format_value(value)} is not a valid value.{suggestion}\n\n"
            f"Valid values are: {', '.join(_format_value(item) for item in valid)}."
        )

    @classmethod
    def from_invalid_type(cls, *, section: str, name: str, value: object, expected: str) -> ConfigError:
        return cls(
            f"Error in {section} section:\n  Type error:\n\n"
            f"  - '{name}' -> Must be {expected}, but got {_type_name(value)}: {_format_value(value)}"
        )


def _format_validation_error(error: ValidationError, replaced: dict[InstancePath, object]) -> str:
    if error.kind.name == "enum":
        return _format_enum_error(error, replaced)
    if error.kind.name in _BOUND_PREDICATES:
        return _format_bound_error(error, _BOUND_PREDICATES[error.kind.name])
    if error.kind.name == "required":
        return _format_required_error(error)
    if error.kind.name == "type":
        return _format_type_error(error, replaced)
    if error.kind.name == "pattern":
        return _format_pattern_error(error)
    if error.kind.name == "minProperties":
        return _format_min_properties_error(error)
    if error.kind.name == "additionalProperties":
        return _format_additional_properties_error(error)
    if error.kind.name == "anyOf":
        return _format_anyof_error(error, replaced)
    if error.kind.name == "oneOf":
        return _format_oneof_error(error, replaced)
    if error.kind.name == "uniqueItems":
        return _format_unique_items_error(error)
    return error.message


_BOUND_PREDICATES = {
    "minimum": "Must be at least",
    "exclusiveMinimum": "Must be greater than",
    "maximum": "Must be at most",
}


def _format_bound_error(error: ValidationError, predicate: str) -> str:
    assert error.instance_path
    section = path_to_section_name(error.instance_path[:-1])
    prop_name = error.instance_path[-1]
    return (
        f"Error in {section} section:\n  Value out of range:\n\n"
        f"  - '{prop_name}' -> {predicate} {error.kind.value}, but got {error.instance}."
    )


def _format_required_error(error: ValidationError) -> str:
    variants = resolve_path(CONFIG_SCHEMA, error.schema_path)
    assert isinstance(variants, list)
    assert isinstance(error.instance, dict)
    missing_keys = sorted(set(variants) - set(error.instance))

    section = path_to_section_name(error.instance_path)

    details = "\n".join(f"  - '{key}'" for key in missing_keys)
    return f"Error in {section} section:\n  Missing required properties:\n\n{details}\n\n"


def _format_enum_error(error: ValidationError, replaced: dict[InstancePath, object]) -> str:
    variants = resolve_path(CONFIG_SCHEMA, error.schema_path)
    assert isinstance(variants, list)
    valid_values = sorted(variants)

    description, section_path = _describe_instance_location(error.instance_path)

    suggestion = ""
    if isinstance(error.instance, str) and all(isinstance(v, str) for v in valid_values):
        match = _find_closest_match(error.instance, valid_values)
        if match:
            suggestion = f" Did you mean '{match}'?"

    section = path_to_section_name(section_path)
    valid_values_str = ", ".join(repr(v) for v in valid_values)
    return (
        f"Error in {section} section:\n  Invalid value:\n\n"
        f"  - {description} -> {_format_value(_original_instance(error, replaced))} is not a valid value.{suggestion}\n\n"
        f"Valid values are: {valid_values_str}."
    )


# A pattern that is a plain case-insensitive alternation, like `(?i)(?:GET|POST)`, lists its valid values.
_ALTERNATION_PATTERN = re.compile(r"\(\?i\)\(\?:([A-Z]+(?:\|[A-Z]+)*)\)")


def _valid_values_from_pattern(pattern: str) -> list[str]:
    match = _ALTERNATION_PATTERN.fullmatch(pattern)
    assert match is not None
    return sorted(match.group(1).split("|"))


def _format_pattern_error(error: ValidationError) -> str:
    description, section_path = _describe_instance_location(error.instance_path)
    section = path_to_section_name(section_path)
    pattern = error.kind.as_dict().get("pattern")
    assert isinstance(pattern, str)
    valid_values = _valid_values_from_pattern(pattern)
    suggestion = ""
    instance = error.instance
    if isinstance(instance, str):
        match = _find_closest_match(instance, valid_values)
        if match:
            suggestion = f" Did you mean '{match}'?"
    valid_values_str = ", ".join(repr(v) for v in valid_values)
    return (
        f"Error in {section} section:\n  Invalid value:\n\n"
        f"  - {description} -> {_format_value(instance)} is not a valid value.{suggestion}\n\n"
        f"Valid values are: {valid_values_str}."
    )


def _describe_instance_location(path: list[str | int]) -> tuple[str, list[str | int]]:
    if path and isinstance(path[-1], int):
        return f"Item #{path[-1]} in the '{path[-2]}' array", path[:-2]
    prop_name = path[-1] if path else "value"
    return f"'{prop_name}'", path[:-1]


_JSON_TYPES = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    list: "array",
    dict: "object",
    type(None): "null",
}

_TYPE_PHRASES = {
    "object": "an object",
    "array": "an array",
    "number": "a number",
    "boolean": "a boolean",
    "string": "a string",
    "integer": "an integer",
    "null": "null",
}


_TEMPORAL_TYPES = {
    datetime.date: "date",
    datetime.datetime: "datetime",
    datetime.time: "time",
}


def _original_instance(error: ValidationError, replaced: dict[InstancePath, object]) -> object:
    return replaced.get(tuple(error.instance_path), error.instance)


def _type_name(value: object) -> str:
    kind = type(value)
    # Values passed through the Python API may have types with no TOML counterpart.
    return _JSON_TYPES.get(kind) or _TEMPORAL_TYPES.get(kind) or kind.__name__


def _format_value(value: object) -> str:
    if isinstance(value, str):
        return f"'{value}'"
    if isinstance(value, datetime.date | datetime.time):
        # TOML spells a zero UTC offset as `Z`
        text = value.isoformat()
        if text.endswith("+00:00"):
            return text.removesuffix("+00:00") + "Z"
        return text
    # TOML spelling for non-string values, e.g. `true` rather than `True`.
    return json.dumps(value, default=str)


def _format_type_error(error: ValidationError, replaced: dict[InstancePath, object]) -> str:
    expected = resolve_path(CONFIG_SCHEMA, error.schema_path)
    assert isinstance(expected, str | list)
    if isinstance(expected, list):
        expectation = f"one of: {' or '.join(expected)}"
    else:
        expectation = _TYPE_PHRASES[expected]
    return _type_error_message(error, expectation, replaced)


def _type_error_message(error: ValidationError, expectation: str, replaced: dict[InstancePath, object]) -> str:
    assert error.instance_path
    section = path_to_section_name(list(error.instance_path)[:-1])
    instance = _original_instance(error, replaced)
    return (
        f"Error in {section} section:\n  Type error:\n\n"
        f"  - '{error.instance_path[-1]}' -> Must be {expectation}, but got {_type_name(instance)}: {_format_value(instance)}"
    )


def _format_unique_items_error(error: ValidationError) -> str:
    assert error.instance_path
    assert isinstance(error.instance, list)
    section = path_to_section_name(list(error.instance_path)[:-1])
    duplicates: list[object] = []
    for index, item in enumerate(error.instance):
        if item in error.instance[:index] and item not in duplicates:
            duplicates.append(item)
    details = "\n".join(
        f"  - '{error.instance_path[-1]}' -> {_format_value(item)} is listed more than once." for item in duplicates
    )
    return f"Error in {section} section:\n  Duplicate values:\n\n{details}"


def _format_additional_properties_error(error: ValidationError) -> str:
    schema = resolve_path(CONFIG_SCHEMA, error.schema_path[:-1])
    assert isinstance(schema, dict)
    valid = list(schema.get("properties", {}))
    assert isinstance(error.instance, dict)
    unknown = sorted(set(error.instance) - set(valid))
    valid_list = ", ".join(f"'{prop}'" for prop in valid)
    section = path_to_section_name(list(error.instance_path))

    details = []
    for prop in unknown:
        match = _find_closest_match(prop, valid)
        if match:
            details.append(f"- '{prop}' -> Did you mean '{match}'?")
        else:
            details.append(f"- '{prop}'")

    return (
        f"Error in {section} section:\n  Unknown properties:\n\n"
        + "\n".join(f"  {detail}" for detail in details)
        + f"\n\nValid properties for {section} are: {valid_list}."
    )


def _format_min_properties_error(error: ValidationError) -> str:
    if list(error.schema_path) == ["$defs", "AuthConfig", "properties", "openapi", "minProperties"]:
        section = path_to_section_name(error.instance_path)
        return f"Error in {section} section:\n  At least one Open API auth definition is required."
    return error.message  # pragma: no cover


def _format_anyof_error(error: ValidationError, replaced: dict[InstancePath, object]) -> str:
    if list(error.schema_path) == ["$defs", "OperationConfig", "anyOf"]:
        section = path_to_section_name(error.instance_path)
        return (
            f"Error in {section} section:\n  At least one filter is required when defining [[operations]].\n\n"
            "Please specify at least one include or exclude filter property (e.g., include-path, exclude-tag, etc.)."
        )
    elif list(error.schema_path) == ["properties", "workers", "anyOf"]:
        return (
            f"Invalid value for 'workers': {_format_value(_original_instance(error, replaced))}\n\n"
            f"Expected either:\n"
            f"  - A positive integer (e.g., workers = 4)\n"
            f'  - The string "auto" for automatic detection (workers = "auto")'
        )
    raw_branches = resolve_path(CONFIG_SCHEMA, error.schema_path)
    assert isinstance(raw_branches, list)
    branches = [_resolve_reference(branch) for branch in raw_branches]
    assert isinstance(error.kind, ValidationErrorKind.AnyOf)
    assert error.kind.context is not None
    instance_type = _JSON_TYPES[type(error.instance)]
    for branch, errors in zip(branches, error.kind.context, strict=True):
        # The value has the right type for this branch, so its nested error is the precise one
        if branch.get("type") == instance_type and errors:
            return _format_validation_error(errors[0], replaced)
    phrases: list[str] = []
    for branch in branches:
        branch_type = branch.get("type")
        if isinstance(branch_type, str):
            branch_phrases = [_TYPE_PHRASES[branch_type]]
        else:
            values = branch["enum"] if "enum" in branch else [branch["const"]]
            assert isinstance(values, list)
            branch_phrases = [_format_value(value) for value in values]
        phrases.extend(phrase for phrase in branch_phrases if phrase not in phrases)
    expectation = ", ".join(phrases[:-1]) + f" or {phrases[-1]}" if len(phrases) > 1 else phrases[0]
    return _type_error_message(error, expectation, replaced)


def _format_oneof_error(error: ValidationError, replaced: dict[InstancePath, object]) -> str:
    """Format oneOf validation errors, particularly for auth.openapi."""
    schema_path = list(error.schema_path)
    if schema_path[:2] == ["$defs", "DictionaryDefinition"]:
        return _format_dictionary_definition_oneof(error)
    if list(error.instance_path)[:2] == ["auth", "openapi"] and len(error.instance_path) == 3:
        section = path_to_section_name(error.instance_path)

        # Try to find the most relevant context error
        if (
            isinstance(error.kind, (ValidationErrorKind.OneOfMultipleValid, ValidationErrorKind.OneOfNotValid))
            and error.kind.context
            and isinstance(error.instance, dict)
            and error.instance
        ):
            # A wrong value type is more precise than any scheme mismatch
            for errors in error.kind.context:
                for one_of_error in errors:
                    if one_of_error.kind.name == "type" and len(one_of_error.instance_path) > len(error.instance_path):
                        return _format_type_error(one_of_error, replaced)
            # Each subschema in `oneOf` may have multiple errors
            for errors in error.kind.context:
                for one_of_error in errors:
                    if one_of_error.kind.name == "required":
                        variants = resolve_path(CONFIG_SCHEMA, one_of_error.schema_path)
                        assert isinstance(variants, list)
                        required_fields = set(variants)
                        # HTTP Basic auth is the only case where we can provide a helpful hint
                        if ("username" in error.instance or "password" in error.instance) and required_fields == {
                            "username",
                            "password",
                        }:
                            missing = sorted(required_fields - set(error.instance.keys()))
                            missing_list = "\n".join(f"  - '{field}'" for field in missing)
                            return f"Error in {section} section:\n  Missing required property (HTTP Basic):\n\n{missing_list}\n"
                    elif one_of_error.kind.name == "additionalProperties":
                        # Find the invalid property
                        valid = {"api_key", "username", "password", "bearer"}
                        invalid = [k for k in error.instance if k not in valid]
                        if invalid:
                            return (
                                f"Error in {section} section:\n  Invalid property:\n\n"
                                f"  - '{invalid[0]}' is not allowed\n\n"
                                "Valid properties: 'api_key', 'username', 'password', 'bearer'"
                            )

        return (
            f"Error in {section} section:\n  Configuration does not match any valid auth scheme.\n\n"
            "Valid schemes:\n"
            "  - apiKey: requires 'api_key'\n"
            "  - http+basic: requires 'username', 'password'\n"
            "  - http+bearer: requires 'bearer'"
        )

    if list(error.instance_path)[-1:] == ["request-retries"]:
        if isinstance(error.instance, int):
            return "'request-retries' must be a non-negative integer"
        if isinstance(error.instance, dict):
            return "'request-retries' table requires 'max-attempts'"

    return error.message  # pragma: no cover


def _format_dictionary_definition_oneof(error: ValidationError) -> str:
    section = path_to_section_name(error.instance_path)
    instance = error.instance if isinstance(error.instance, dict) else {}
    if "values" in instance and "from-file" in instance:
        return f"Error in {section} section:\n  `values` and `from-file` are mutually exclusive - pick one."
    return (
        f"Error in {section} section:\n"
        "  Must define either `values` (inline entries) "
        "or `from-file` (path to a libFuzzer/AFL-format file)."
    )


def _resolve_reference(schema: dict[str, object]) -> dict[str, object]:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        resolved = resolve_path(CONFIG_SCHEMA, reference.removeprefix("#/").split("/"))
        assert isinstance(resolved, dict)
        return resolved
    return schema


def path_to_section_name(path: list[int | str]) -> str:
    """Convert a JSON path to a TOML-like section name."""
    if not path:
        return "root"

    return f"[{'.'.join(str(p) for p in path)}]"


def _find_closest_match(value: str, variants: list[str]) -> str | None:
    matches = difflib.get_close_matches(value, variants, n=1, cutoff=0.6)
    return matches[0] if matches else None
