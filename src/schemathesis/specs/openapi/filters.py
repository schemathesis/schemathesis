from __future__ import annotations

import re

from ...types import Filter


def should_skip_method(method: str, pattern: Filter | None) -> bool:
    if pattern is None:
        return False
    patterns = _ensure_tuple(pattern)
    return method.upper() not in map(str.upper, patterns)


def should_skip_endpoint(endpoint: str, pattern: Filter | None) -> bool:
    if pattern is None:
        return False
    return not _match_any_pattern(endpoint, pattern)


def should_skip_by_tag(tags: list[str] | None, pattern: Filter | None) -> bool:
    if pattern is None:
        return False
    if not tags:
        return True
    patterns = _ensure_tuple(pattern)
    return not any(re.search(item, tag) for item in patterns for tag in tags)


def should_skip_by_operation_id(operation_id: str | None, pattern: Filter | None) -> bool:
    if pattern is None:
        return False
    if not operation_id:
        return True
    return not _match_any_pattern(operation_id, pattern)


def should_skip_deprecated(is_deprecated: bool, skip_deprecated_operations: bool) -> bool:
    return skip_deprecated_operations and is_deprecated


def _match_any_pattern(target: str, pattern: Filter) -> bool:
    patterns = _ensure_tuple(pattern)
    return any(re.search(item, target) for item in patterns)


def _ensure_tuple(item: Filter) -> list | set | tuple:
    if not isinstance(item, (list, set, tuple)):
        return (item,)
    return item
