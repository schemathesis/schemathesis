from __future__ import annotations

import re
from hashlib import md5

_INVALID = re.compile(r"[^A-Za-z0-9_-]")
_MAX_LENGTH = 100


def operation_filename(label: str, seen: set[str]) -> str:
    """Deterministic, collision-safe filename stem for an operation page."""
    method, _, path = label.partition(" ")
    raw = f"{method.upper()}_{path}".replace("/", "_").replace("{", "").replace("}", "")
    base = _INVALID.sub("-", raw)[:_MAX_LENGTH]
    stem = base
    key = stem.lower()
    if key in seen:
        digest = md5(label.encode(), usedforsecurity=False).hexdigest()[:8]
        counter = 0
        # A hash suffix normally resolves the collision; keep counting up if even that stem is taken
        # so two operations can never share a page file.
        while key in seen:
            suffix = f"-{digest}" if counter == 0 else f"-{digest}-{counter}"
            stem = f"{base[: _MAX_LENGTH - len(suffix)]}{suffix}"
            key = stem.lower()
            counter += 1
    seen.add(key)
    return stem
