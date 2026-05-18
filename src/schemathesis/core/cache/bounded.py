from __future__ import annotations

from collections import OrderedDict
from typing import Any, Final

# Sentinel returned by `BoundedCache.get` when the key is absent — lets callers distinguish
# "not cached" from "cached as `None`" without an extra membership check.
MISSING: Final = object()


class BoundedCache:
    """LRU cache returning `MISSING` for absent keys so `None` can be a valid value."""

    __slots__ = ("_data", "_maxsize")

    def __init__(self, maxsize: int) -> None:
        self._data: OrderedDict[Any, Any] = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: Any, default: Any = MISSING) -> Any:
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return default

    def clear(self) -> None:
        self._data.clear()

    def __setitem__(self, key: Any, value: Any) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)
