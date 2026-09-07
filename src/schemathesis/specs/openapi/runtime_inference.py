"""Runtime schema synthesis from observed 2xx response bodies.

Recovers structural shape (`type`, `properties`, `items`) for ops whose declared
response schema is information-poor (e.g. Spring Data's `{type: object, title:
"PagedResponse"}`), so dependency inference produces correct wildcard-pointer
link extractors (`#/content/*/id`).
"""

from __future__ import annotations

import threading
from collections import deque
from typing import TYPE_CHECKING, Any

import jsonschema_rs

from schemathesis.core.jsonschema.resolver import resolve_reference

if TYPE_CHECKING:
    from schemathesis.core.jsonschema.resolver import Resolver


_INFO_POOR_REJECT_KEYS = frozenset({"properties", "items", "additionalProperties", "allOf", "oneOf", "anyOf"})


def _is_info_poor(schema: Any, resolver: Resolver, _seen_refs: frozenset[str] = frozenset()) -> bool:
    """Return True when `schema` declares an object container with no actionable shape.

    Follows `$ref` chains; an opaque schema parked behind a name still counts as info-poor.
    """
    if not isinstance(schema, dict):
        return False
    if "$ref" in schema:
        ref = schema["$ref"]
        # Cyclic `$ref` chains (e.g. `Foo -> Foo`) are malformed but should not crash.
        if ref in _seen_refs:
            return False
        try:
            resolved_resolver, resolved = resolve_reference(resolver, ref)
        except Exception:
            return False
        return _is_info_poor(resolved, resolved_resolver, _seen_refs | {ref})
    if schema.get("type") not in (None, "object"):
        return False
    if any(key in schema for key in _INFO_POOR_REJECT_KEYS):
        return False
    return "enum" not in schema and "const" not in schema


_PRIMITIVE_TYPE_BY_PYTHON = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    type(None): "null",
}


def _type_of(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return _PRIMITIVE_TYPE_BY_PYTHON.get(type(value), "string")


def synthesize_schema(samples: list[Any]) -> dict[str, Any] | None:
    """Genson-inspired structural merge over observed body samples.

    Returns a minimal `{type, properties|items}` schema. Returns `None` only for
    empty `samples` or all-null `samples`.
    """
    if not samples:
        return None
    return _merge_samples(samples)


def _merge_samples(values: list[Any]) -> dict[str, Any] | None:
    types = {_type_of(v) for v in values if v is not None}
    if not types or types == {"null"}:
        return None
    non_null = types - {"null"}
    if "array" in non_null and len(non_null) == 1:
        items_values = [item for v in values if isinstance(v, list) for item in v]
        item_schema = _merge_samples(items_values) if items_values else None
        return {"type": "array"} if item_schema is None else {"type": "array", "items": item_schema}
    if "object" in non_null and len(non_null) == 1:
        keys: set[str] = set()
        for obj in values:
            if isinstance(obj, dict):
                keys.update(obj.keys())
        properties: dict[str, Any] = {}
        # Sort keys: downstream nested-FK extraction keeps the first match per resource,
        # so the order of `properties` is observable. An unsorted set iteration would let
        # the synthesized overlay (and thus the inferred-link target) vary with PYTHONHASHSEED.
        for key in sorted(keys):
            sub_values = [obj[key] for obj in values if isinstance(obj, dict) and key in obj]
            sub = _merge_samples(sub_values)
            if sub is not None:
                properties[key] = sub
        return {"type": "object", "properties": properties} if properties else {"type": "object"}
    # Deterministic alphabetical-min over the set of seen leaf types: same samples in any
    # order produce the same schema.
    leaf = min(non_null)
    return {"type": leaf}


OBSERVED_BODY_CAPACITY = 20


class ObservedBodyStore:
    """Thread-safe per-`(operation_label, status_code)` ring of observed body samples.

    Deduplicates by shape (canonical-JSON of `synthesize_schema([body])`) so
    repeated identical-shape bodies don't consume the cap.
    """

    __slots__ = ("_lock", "_samples", "_shape_seen", "_dirty")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: dict[tuple[str, int], deque[Any]] = {}
        self._shape_seen: dict[tuple[str, int], set[str]] = {}
        self._dirty: set[tuple[str, int]] = set()

    def record(self, *, operation: str, status_code: int, body: Any) -> bool:
        shape = synthesize_schema([body])
        if shape is None:
            return False
        shape_key = jsonschema_rs.canonical.json.to_string(shape)
        key = (operation, status_code)
        with self._lock:
            seen = self._shape_seen.setdefault(key, set())
            if shape_key in seen:
                return False
            if len(seen) >= OBSERVED_BODY_CAPACITY:
                return False
            seen.add(shape_key)
            ring = self._samples.setdefault(key, deque(maxlen=OBSERVED_BODY_CAPACITY))
            ring.append(body)
            self._dirty.add(key)
            return True

    def samples(self, *, operation: str, status_code: int) -> list[Any]:
        with self._lock:
            return list(self._samples.get((operation, status_code), ()))

    def dirty(self) -> set[tuple[str, int]]:
        with self._lock:
            return set(self._dirty)

    def consume_dirty(self) -> set[tuple[str, int]]:
        with self._lock:
            out = self._dirty
            self._dirty = set()
            return out
