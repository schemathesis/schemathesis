from __future__ import annotations

import threading
from collections import OrderedDict

import jsonschema_rs

from schemathesis.core.cache import BoundedCache

# Distinct format/pattern registries alive per session; churn past this is registry-heavy enough
# that cached values carry little worth.
MAX_PINNED_REGISTRIES = 64


class GenerationSession:
    """Owns the caches one coverage run draws from; closing it releases them all."""

    __slots__ = ("_lock", "_pinned", "format_validators", "ready_bundles", "removed_examples", "values")

    def __init__(self) -> None:
        self.values: BoundedCache = BoundedCache(maxsize=2048)
        self.format_validators: dict[tuple[str, type[jsonschema_rs.Validator]], jsonschema_rs.Validator] = {}
        self.removed_examples: BoundedCache = BoundedCache(maxsize=4096)
        self.ready_bundles: BoundedCache = BoundedCache(maxsize=64)
        # Pinning a registry keeps its `id` from being recycled into a colliding token.
        self._pinned: OrderedDict[int, object] = OrderedDict()
        self._lock = threading.Lock()

    def token_for(self, obj: object) -> int:
        """A cache-key token for this registry, stable and unique while the session pins it."""
        key = id(obj)
        with self._lock:
            if key in self._pinned:
                self._pinned.move_to_end(key)
                return key
            self._pinned[key] = obj
            if len(self._pinned) > MAX_PINNED_REGISTRIES:
                self._pinned.popitem(last=False)
                # The dropped registry may be freed and its `id` recycled; no value keyed with it may survive.
                self.values.clear()
        return key

    def close(self) -> None:
        self.values.clear()
        self.format_validators.clear()
        self.removed_examples.clear()
        self.ready_bundles.clear()
        with self._lock:
            self._pinned.clear()


# One process-wide session until callers construct run-scoped ones.
DEFAULT_GENERATION_SESSION = GenerationSession()
