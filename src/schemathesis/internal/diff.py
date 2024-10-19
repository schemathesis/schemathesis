from __future__ import annotations

from typing import Any, Mapping


def diff(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate the difference between two dictionaries."""
    diff = {}
    for key, value in right.items():
        if key not in left or left[key] != value:
            diff[key] = value
    for key in left:
        if key not in right:
            diff[key] = None  # Mark deleted items as None
    return diff
