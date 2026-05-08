"""Cross-operation semantic value pool.

Index 2xx response leaves by ``(type, format)``, with pattern-hash and normalized-name
fallbacks, so values harvested from one operation feed slots on any other operation with
a matching shape. Inspired by the dynamic-dictionary mechanism in RESTler
[Godefroid et al., FSE 2020, "Intelligent REST API Data Fuzzing"].
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, NamedTuple, cast

from schemathesis.core.jsonschema import get_type, maybe_resolve_bundled, schema_with_bundle
from schemathesis.core.jsonschema.types import JsonSchemaObject
from schemathesis.specs.openapi.formats import HEADER_FORMAT
from schemathesis.specs.openapi.headers import KNOWN_HEADER_FORMATS
from schemathesis.specs.openapi.stateful.dependencies.naming import normalize_for_matching

# Format tokens injected by the parameter adapter rather than declared by users. Slots tagged with these bypass
# wire-level filters, so the consumer walker drops them and the ingestion walker treats them as "no format".
_INTERNAL_FORMATS: frozenset[str] = frozenset({HEADER_FORMAT, *KNOWN_HEADER_FORMATS.values()})

MAX_VALUES_PER_KEY = 100

# Per-leaf substitution probability; balances pool reuse against Hypothesis exploration.
SEMANTIC_OVERLAY_PROBABILITY = 0.5

# `(type, format)` pairs eligible for ingestion. Identity-shaped formats (`uuid`, opaque tokens) are excluded
# to keep one resource's identifiers from leaking into another's slots. Numeric pooling additionally requires
# explicit `minimum`/`maximum` bounds (see ``_is_numeric_bounded``).
ALLOWED_FORMATS: dict[str, frozenset[str]] = {
    "string": frozenset(
        {
            "email",
            "uri",
            "url",
            "hostname",
            "ipv4",
            "ipv6",
            "date",
            "date-time",
            "time",
            "duration",
            "phone",
            "color",
            "currency",
        }
    ),
    "integer": frozenset({"int32", "int64"}),
}


PoolValue = str | int | float


@dataclass(slots=True)
class BoundedValues:
    """Dedup-on-insert ordered set, bounded by ``max_size``, with per-value draw recency."""

    max_size: int = MAX_VALUES_PER_KEY
    _values: dict[PoolValue, None] = field(default_factory=dict)
    _last_drawn: dict[PoolValue, int] = field(default_factory=dict)
    _step: int = 0

    def add(self, value: PoolValue) -> None:
        if value in self._values:
            return
        self._values[value] = None
        if len(self._values) > self.max_size:
            evicted = next(iter(self._values))
            del self._values[evicted]
            self._last_drawn.pop(evicted, None)

    def record_draw(self, value: PoolValue) -> None:
        self._step += 1
        self._last_drawn[value] = self._step

    def values(self) -> tuple[PoolValue, ...]:
        return tuple(self._values)

    def __len__(self) -> int:
        return len(self._values)


@dataclass(slots=True)
class SemanticValueIndex:
    """Three-tier index of typed values harvested from 2xx responses; lookup tries format, pattern, then name."""

    by_format: dict[tuple[str, str], BoundedValues] = field(default_factory=dict)
    by_pattern: dict[tuple[str, str], BoundedValues] = field(default_factory=dict)
    by_name: dict[tuple[str, str], BoundedValues] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(
        self,
        *,
        type_token: str,
        format_token: str | None,
        pattern_hash: str | None,
        normalized_name: str | None,
        value: PoolValue,
    ) -> None:
        with self._lock:
            if format_token is not None:
                self.by_format.setdefault((type_token, format_token), BoundedValues()).add(value)
            elif pattern_hash is not None:
                self.by_pattern.setdefault((type_token, pattern_hash), BoundedValues()).add(value)
            elif normalized_name:
                self.by_name.setdefault((type_token, normalized_name), BoundedValues()).add(value)

    def lookup(
        self,
        *,
        type_token: str,
        format_token: str | None,
        pattern_hash: str | None,
        normalized_name: str | None,
    ) -> tuple[PoolValue, ...]:
        # Short-circuit excluded formats so pattern/name fallbacks can't smuggle in an identity-shaped value
        # (e.g. a UUID stored under a name bucket because the producer omitted `format`).
        if format_token is not None and not is_pool_eligible(type_token=type_token, format_token=format_token):
            return ()
        with self._lock:
            if format_token is not None:
                bucket = self.by_format.get((type_token, format_token))
                if bucket is not None and len(bucket) > 0:
                    return bucket.values()
            if pattern_hash is not None:
                bucket = self.by_pattern.get((type_token, pattern_hash))
                if bucket is not None and len(bucket) > 0:
                    return bucket.values()
            if normalized_name:
                bucket = self.by_name.get((type_token, normalized_name))
                if bucket is not None and len(bucket) > 0:
                    return bucket.values()
            return ()

    def record_draw(
        self,
        *,
        type_token: str,
        format_token: str | None,
        pattern_hash: str | None,
        normalized_name: str | None,
        value: PoolValue,
    ) -> None:
        with self._lock:
            if format_token is not None:
                bucket = self.by_format.get((type_token, format_token))
            elif pattern_hash is not None:
                bucket = self.by_pattern.get((type_token, pattern_hash))
            elif normalized_name:
                bucket = self.by_name.get((type_token, normalized_name))
            else:
                bucket = None
            if bucket is not None:
                bucket.record_draw(value)


@dataclass(frozen=True, slots=True)
class LeafDescriptor:
    """Lookup keys, body path, and slot schema for one substitutable leaf; the schema gates candidates."""

    path: tuple[str, ...]
    type: str
    format: str | None
    pattern_hash: str | None
    normalized_name: str
    schema: JsonSchemaObject = field(default_factory=dict)


def pattern_hash(regex: str) -> str:
    return hashlib.sha256(regex.encode("utf-8")).hexdigest()[:16]


def is_pool_eligible(*, type_token: str, format_token: str | None) -> bool:
    if format_token is None:
        return True
    allowed = ALLOWED_FORMATS.get(type_token)
    return allowed is not None and format_token in allowed


def _is_numeric_bounded(schema: JsonSchemaObject) -> bool:
    # Unbounded numerics are kept out of the pool: their domain (user ID vs counter, etc.) can't be told apart.
    return any(key in schema for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"))


DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_NODES = 10_000


# Order matters: more-specific patterns first so a date-time string is not classified as a date.
_SHAPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("uuid", re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)),
    ("date-time", re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")),
    ("date", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("email", re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")),
    ("ipv4", re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")),
    ("ipv6", re.compile(r"^[0-9a-fA-F:]+$")),
    ("uri", re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)),
)


def _infer_string_format(value: str) -> str | None:
    for fmt, pattern in _SHAPE_PATTERNS:
        if pattern.match(value):
            return fmt
    return None


class IngestionLeaf(NamedTuple):
    type_token: str
    format_token: str | None
    pattern_hash: str | None
    normalized_name: str | None
    value: PoolValue


def _resolve_ref(schema: JsonSchemaObject, root: JsonSchemaObject) -> JsonSchemaObject:
    """Follow a bundled ``$ref`` (e.g. ``#/x-bundled/Foo``) by splicing ``root``'s bundle into the fragment."""
    # `schema_with_bundle` preserves shape: dict in, dict out.
    return maybe_resolve_bundled(cast("JsonSchemaObject", schema_with_bundle(schema, root)))


def _resolve_combinator(schema: JsonSchemaObject, root: JsonSchemaObject) -> JsonSchemaObject:
    """Merge ``allOf`` branches; for ``oneOf``/``anyOf``, pick the first branch with declared ``properties``."""
    if "allOf" in schema:
        merged: JsonSchemaObject = {}
        merged_properties: dict[str, Any] = {}
        for branch in schema["allOf"]:
            if not isinstance(branch, dict):
                continue
            resolved_branch = _resolve_combinator(_resolve_ref(branch, root), root)
            for key, value in resolved_branch.items():
                if key == "properties" and isinstance(value, dict):
                    merged_properties.update(value)
                else:
                    merged[key] = value
        for key, value in schema.items():
            if key == "allOf":
                continue
            if key == "properties" and isinstance(value, dict):
                merged_properties.update(value)
            else:
                merged[key] = value
        if merged_properties:
            merged["properties"] = merged_properties
        return merged
    for combinator in ("oneOf", "anyOf"):
        branches = schema.get(combinator)
        if not isinstance(branches, list):
            continue
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            resolved = _resolve_ref(branch, root)
            if isinstance(resolved.get("properties"), dict):
                return _resolve_combinator(resolved, root)
        for branch in branches:
            if isinstance(branch, dict):
                return _resolve_combinator(_resolve_ref(branch, root), root)
    return schema


def _normalize_type(schema: JsonSchemaObject) -> str | None:
    # Walker uses None for "no declaration" / "malformed `type`" to fall through; `get_type` assumes str|list.
    if not isinstance(schema.get("type"), (str, list)):
        return None
    return next((t for t in get_type(schema) if t != "null"), None)


def _is_primitive_value(value: object, type_token: str) -> bool:
    # `isinstance(True, int)` is True, so filter bools out before numeric checks.
    if isinstance(value, bool):
        return False
    if type_token == "string":
        return isinstance(value, str)
    if type_token == "integer":
        return isinstance(value, int)
    # Caller restricts `type_token` to one of "string", "integer", "number".
    return isinstance(value, (int, float))


@dataclass(slots=True)
class _WalkBudget:
    nodes_left: int


def iter_ingestion_leaves(
    schema: JsonSchemaObject | None,
    body: object,
    *,
    excluded_names: frozenset[str] = frozenset(),
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    root: JsonSchemaObject | None = None,
) -> Iterator[IngestionLeaf]:
    """Yield pool-eligible leaves from a 2xx response body, optionally guided by its schema.

    Without ``schema``, only string leaves classify (by shape inference). ``excluded_names`` blocks named
    properties (caller passes the path-parameter set to prevent identity-shaped cross-pooling). Arrays are
    not recursed.
    """
    budget = _WalkBudget(nodes_left=max_nodes)
    effective_root = root if root is not None else schema
    if isinstance(schema, dict) and effective_root is not None:
        yield from _walk_ingestion(
            schema,
            body,
            name=None,
            depth=0,
            excluded=excluded_names,
            budget=budget,
            max_depth=max_depth,
            root=effective_root,
        )
        return
    # Match the schema-aware path's one-node entry cost so budgets are comparable either way.
    budget.nodes_left -= 1
    yield from _walk_ingestion_schemaless(
        body, name=None, depth=0, excluded=excluded_names, budget=budget, max_depth=max_depth
    )


def _walk_ingestion(
    schema: JsonSchemaObject | None,
    body: object,
    *,
    name: str | None,
    depth: int,
    excluded: frozenset[str],
    budget: _WalkBudget,
    max_depth: int,
    root: JsonSchemaObject,
) -> Iterator[IngestionLeaf]:
    if depth > max_depth or budget.nodes_left <= 0:
        return
    budget.nodes_left -= 1
    if body is None:
        return
    if isinstance(schema, dict):
        schema = _resolve_ref(schema, root)
        schema = _resolve_combinator(schema, root)
        type_token = _normalize_type(schema)
        if type_token == "object" or (type_token is None and isinstance(body, dict)):
            properties = schema.get("properties")
            if not isinstance(properties, dict) or not isinstance(body, dict):
                return
            for property_name, property_schema in properties.items():
                if not isinstance(property_name, str) or normalize_for_matching(property_name) in excluded:
                    continue
                if property_name not in body:
                    continue
                yield from _walk_ingestion(
                    property_schema if isinstance(property_schema, dict) else None,
                    body[property_name],
                    name=property_name,
                    depth=depth + 1,
                    excluded=excluded,
                    budget=budget,
                    max_depth=max_depth,
                    root=root,
                )
            return
        if type_token in ("string", "integer", "number"):
            if name is None or not _is_primitive_value(body, type_token):
                return
            if type_token in ("integer", "number") and not _is_numeric_bounded(schema):
                return
            format_token = schema.get("format") if isinstance(schema.get("format"), str) else None
            if format_token in _INTERNAL_FORMATS:
                format_token = None
            if format_token is not None and not is_pool_eligible(type_token=type_token, format_token=format_token):
                return
            pattern = schema.get("pattern") if isinstance(schema.get("pattern"), str) else None
            pattern_key = pattern_hash(pattern) if pattern else None
            normalized = normalize_for_matching(name) if name else None
            # `body` is `object`; `_is_primitive_value` above narrows it but mypy can't track the bridge.
            yield IngestionLeaf(type_token, format_token, pattern_key, normalized, body)  # type: ignore[arg-type]
            return
        return
    yield from _walk_ingestion_schemaless(
        body, name=name, depth=depth, excluded=excluded, budget=budget, max_depth=max_depth
    )


def _walk_ingestion_schemaless(
    body: object,
    *,
    name: str | None,
    depth: int,
    excluded: frozenset[str],
    budget: _WalkBudget,
    max_depth: int,
) -> Iterator[IngestionLeaf]:
    if depth > max_depth or budget.nodes_left <= 0:
        return
    if isinstance(body, dict):
        for key, value in body.items():
            if not isinstance(key, str) or normalize_for_matching(key) in excluded:
                continue
            budget.nodes_left -= 1
            if budget.nodes_left < 0:
                return
            yield from _walk_ingestion_schemaless(
                value, name=key, depth=depth + 1, excluded=excluded, budget=budget, max_depth=max_depth
            )
        return
    if isinstance(body, list):
        return
    if name is None:
        return
    if not isinstance(body, str):
        return
    inferred = _infer_string_format(body)
    if inferred is not None:
        if not is_pool_eligible(type_token="string", format_token=inferred):
            return
        yield IngestionLeaf("string", inferred, None, normalize_for_matching(name), body)
        return
    yield IngestionLeaf("string", None, None, normalize_for_matching(name), body)


def iter_consumer_leaves(
    schema: JsonSchemaObject,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> list[LeafDescriptor]:
    """Return one ``LeafDescriptor`` per substitutable slot. Format-allowlist gating happens at lookup, not here."""
    budget = _WalkBudget(nodes_left=max_nodes)
    descriptors: list[LeafDescriptor] = []
    _walk_consumer(
        schema,
        path=(),
        name=None,
        depth=0,
        descriptors=descriptors,
        budget=budget,
        max_depth=max_depth,
        root=schema,
    )
    return descriptors


def _walk_consumer(
    schema: JsonSchemaObject | None,
    *,
    path: tuple[str, ...],
    name: str | None,
    depth: int,
    descriptors: list[LeafDescriptor],
    budget: _WalkBudget,
    max_depth: int,
    root: JsonSchemaObject,
) -> None:
    if depth > max_depth or budget.nodes_left <= 0:
        return
    budget.nodes_left -= 1
    if not isinstance(schema, dict):
        return
    schema = _resolve_ref(schema, root)
    schema = _resolve_combinator(schema, root)
    type_token = _normalize_type(schema)
    properties = schema.get("properties")
    if type_token == "object" or (type_token is None and isinstance(properties, dict)):
        if not isinstance(properties, dict):
            return
        for property_name, property_schema in properties.items():
            if not isinstance(property_name, str):
                continue
            _walk_consumer(
                property_schema if isinstance(property_schema, dict) else None,
                path=(*path, property_name),
                name=property_name,
                depth=depth + 1,
                descriptors=descriptors,
                budget=budget,
                max_depth=max_depth,
                root=root,
            )
        return
    if type_token in ("string", "integer", "number") and name is not None:
        if type_token in ("integer", "number") and not _is_numeric_bounded(schema):
            return
        format_token = schema.get("format") if isinstance(schema.get("format"), str) else None
        if format_token in _INTERNAL_FORMATS:
            return
        pattern = schema.get("pattern") if isinstance(schema.get("pattern"), str) else None
        pattern_key = pattern_hash(pattern) if pattern else None
        descriptors.append(
            LeafDescriptor(
                path=path,
                type=type_token,
                format=format_token,
                pattern_hash=pattern_key,
                normalized_name=normalize_for_matching(name),
                schema=schema,
            )
        )
