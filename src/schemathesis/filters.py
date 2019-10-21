import re
from typing import List, Optional

from .types import Filter
from .utils import force_tuple


def should_skip_method(method: str, pattern: Optional[Filter]) -> bool:
    if pattern is None:
        return False
    patterns = force_tuple(pattern)
    return method.upper() not in map(str.upper, patterns)


def should_skip_endpoint(endpoint: str, pattern: Optional[Filter]) -> bool:
    if pattern is None:
        return False
    patterns = force_tuple(pattern)
    return not any(re.search(item, endpoint) for item in patterns)


def should_skip_by_tag(tags: Optional[List[str]], pattern: Optional[Filter]) -> bool:
    if pattern is None:
        return False
    if not tags:
        return True
    patterns = force_tuple(pattern)
    return not any(re.search(item, tag) for item in patterns for tag in tags)
