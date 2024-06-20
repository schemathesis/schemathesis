from __future__ import annotations

from typing import Any


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
