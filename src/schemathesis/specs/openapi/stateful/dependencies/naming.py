from __future__ import annotations


def from_parameter(parameter: str, path: str) -> str | None:
    # TODO: support other naming patterns
    # Named like "userId" -> look for "User" resource
    if parameter.endswith("Id"):
        return to_pascal_case(parameter[:-2])
    # Named like "user_id" -> look for "User" resource
    elif parameter.endswith("_id"):
        return to_pascal_case(parameter[:-3])
    # Just "id" -> infer from path context
    elif parameter == "id":
        return from_path(path)
    return None


def from_path(path: str) -> str | None:
    segments = [s for s in path.split("/") if s and "{" not in s]

    if not segments:
        # API Root
        return None

    singular = to_singular(segments[-1])
    return to_pascal_case(singular)


def to_singular(word: str) -> str:
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


def to_plural(word: str) -> str:
    # party -> parties (inverse of ies -> y)
    if word.endswith("y"):
        return word[:-1] + "ies"
    # class -> classes
    if word.endswith("ss"):
        return word + "es"
    # words that normally take -es: box -> boxes
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    # just add 's' (car -> cars)
    return word + "s"


def to_pascal_case(text: str) -> str:
    parts = text.replace("-", "_").split("_")
    return "".join(word.capitalize() for word in parts if word)


def to_snake_case(text: str) -> str:
    text = text.replace("-", "_")
    # Insert underscores before uppercase letters
    result = []
    for i, char in enumerate(text):
        # Add underscore before uppercase (except at start)
        if i > 0 and char.isupper():
            result.append("_")
        result.append(char.lower())
    return "".join(result)


def find_matching_field(*, parameter: str, resource: str, fields: list[str]) -> str | None:
    """Find which resource field matches the parameter name."""
    if not fields:
        return None

    # Exact match
    if parameter in fields:
        return parameter

    # Normalize for fuzzy matching
    parameter_normalized = _normalize_for_matching(parameter)
    resource_normalized = _normalize_for_matching(resource)

    # Normalized exact match
    # `brandId` -> `Brand.BrandId`
    for field in fields:
        if _normalize_for_matching(field) == parameter_normalized:
            return field

    # Extract parameter components
    parameter_prefix, param_suffix = _split_parameter_name(parameter)
    parameter_prefix_normalized = _normalize_for_matching(parameter_prefix)

    # Parameter has resource prefix, field might not
    # Example: `channelId` - `Channel.id`
    if parameter_prefix and parameter_prefix_normalized == resource_normalized:
        suffix_normalized = _normalize_for_matching(param_suffix)

        for field in fields:
            field_normalized = _normalize_for_matching(field)
            if field_normalized == suffix_normalized:
                return field

    # Parameter has no prefix, field might have resource prefix
    # Example: `id` - `Channel.channelId`
    if not parameter_prefix and param_suffix:
        expected_field_normalized = resource_normalized + _normalize_for_matching(param_suffix)

        for field in fields:
            field_normalized = _normalize_for_matching(field)
            if field_normalized == expected_field_normalized:
                return field

    return None


def _normalize_for_matching(text: str) -> str:
    """Normalize text for case-insensitive, separator-insensitive matching.

    Examples:
        "channelId" -> "channelid"
        "channel_id" -> "channelid"
        "ChannelId" -> "channelid"
        "Channel" -> "channel"

    """
    return text.lower().replace("_", "").replace("-", "")


def _split_parameter_name(param_name: str) -> tuple[str, str]:
    """Split parameter into (prefix, suffix) components.

    Examples:
        "channelId" -> ("channel", "Id")
        "userId" -> ("user", "Id")
        "user_id" -> ("user", "_id")
        "id" -> ("", "id")
        "channel_id" -> ("channel", "_id")

    """
    if param_name.endswith("Id") and len(param_name) > 2:
        return (param_name[:-2], "Id")

    if param_name.endswith("_id") and len(param_name) > 3:
        return (param_name[:-3], "_id")

    return ("", param_name)


def strip_affixes(name: str, prefixes: list[str], suffixes: list[str]) -> str:
    """Remove common prefixes and suffixes from a name (case-insensitive)."""
    result = name.strip()
    name_lower = result.lower()

    # Remove one matching prefix
    for prefix in prefixes:
        if name_lower.startswith(prefix):
            result = result[len(prefix) :]
            break

    # Remove one matching suffix
    for suffix in suffixes:
        if name_lower.endswith(suffix):
            result = result[: -len(suffix)]
            break

    return result.strip()
