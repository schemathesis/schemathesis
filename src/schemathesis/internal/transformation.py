from __future__ import annotations

from ..constants import FALSE_VALUES, TRUE_VALUES


def convert_boolean_string(value: str) -> str | bool:
    if value.lower() in TRUE_VALUES:
        return True
    if value.lower() in FALSE_VALUES:
        return False
    return value
