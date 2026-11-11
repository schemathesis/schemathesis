from __future__ import annotations

from typing import Any

from schemathesis.specs.openapi.expressions import MultiMatch


def count_matches(value: Any) -> int:
    if isinstance(value, MultiMatch):
        return len(value.values)
    if isinstance(value, dict):
        return max(map(count_matches, value.values()), default=1)
    if isinstance(value, list):
        return max(map(count_matches, value), default=1)
    return 1


def select_match(value: Any, index: int) -> Any:
    # Values matched from the same array stay paired by their position in it.
    if isinstance(value, MultiMatch):
        return value.values[index % len(value.values)]
    if isinstance(value, dict):
        return {key: select_match(item, index) for key, item in value.items()}
    if isinstance(value, list):
        return [select_match(item, index) for item in value]
    return value
