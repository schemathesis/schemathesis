from __future__ import annotations

from typing import Any

from ..constants import FALSE_VALUES, TRUE_VALUES


def merge_recursively(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Merge two dictionaries recursively."""
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                merge_recursively(a[key], b[key])
            else:
                a[key] = b[key]
        else:
            a[key] = b[key]
    return a


def convert_boolean_string(value: str) -> str | bool:
    if value.lower() in TRUE_VALUES:
        return True
    if value.lower() in FALSE_VALUES:
        return False
    return value
