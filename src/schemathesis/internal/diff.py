from __future__ import annotations

from typing import Any, Mapping


def diff(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate the difference between two dictionaries."""
    diff = {}
    for key, value in right.items():
        if key not in left or left[key] != value:
            diff[key] = value
    return diff
