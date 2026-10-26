import re
import uuid
from collections.abc import Callable
from functools import lru_cache
from itertools import count
from typing import Any, cast

import jsonschema_rs

from schemathesis.core.cache import MISSING, BoundedCache
from schemathesis.core.jsonschema.bundler import (
    BUNDLE_STORAGE_KEY,
    REFERENCE_TO_BUNDLE_PREFIX,
    BundleCache,
    BundleError,
    Bundler,
    bundle,
    unbundle,
    unbundle_path,
)
from schemathesis.core.jsonschema.keywords import ALL_KEYWORDS
from schemathesis.core.jsonschema.types import JsonSchema, JsonValue, get_type

# Support ECMA-262 lookahead/lookbehind. The limit fits legit large quantifiers but fast-fails
# degenerate ones (e.g. `{0,10000000}` from a huge `maxLength`) instead of burning seconds + ~1GB.
FANCY_REGEX_OPTIONS = jsonschema_rs.FancyRegexOptions(size_limit=150_000_000)

# Draft 3 predates the keyword semantics every conversion here assumes and is rejected outright
DRAFT_03_DIALECT = "http://json-schema.org/draft-03/schema#"


@lru_cache(maxsize=256)
def matches_pattern(pattern: str) -> Callable[[str], bool]:
    """The validator's own `pattern` check, for the places where Python `re` reads the pattern differently."""
    try:
        return jsonschema_rs.Draft202012Validator({"pattern": pattern}, pattern_options=FANCY_REGEX_OPTIONS).is_valid
    except jsonschema_rs.ValidationError:
        # A pattern the validator cannot compile is not enforced by it either.
        return lambda _: True


def _is_valid_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return True
    return _is_uuid_string(value)


# Generated bodies repeat the same identifiers in every case, each checked again.
@lru_cache(maxsize=1024)
def _is_uuid_string(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


# RFC 3339 Appendix A `duration`, the grammar newer drafts check.
_DURATION_RE = re.compile(
    r"^P(?:\d+W"
    r"|(?:\d+Y(?:\d+M(?:\d+D)?)?|\d+M(?:\d+D)?|\d+D)(?:T(?:\d+H(?:\d+M(?:\d+S)?)?|\d+M(?:\d+S)?|\d+S))?"
    r"|T(?:\d+H(?:\d+M(?:\d+S)?)?|\d+M(?:\d+S)?|\d+S))$"
)


# Format checks only ever see strings - the validator skips them for every other instance type.
def _is_valid_duration(value: str) -> bool:
    return _DURATION_RE.match(value) is not None


# Formats that newer JSON Schema drafts validate natively but Draft 4 (used by
# OpenAPI 2.0 / 3.0) does not. Registered only for Draft4Validator so built-in
# implementations in newer drafts are not overridden.
DRAFT4_SUPPLEMENTAL_FORMATS: dict[str, Callable[[Any], bool]] = {
    "duration": _is_valid_duration,
    "uuid": _is_valid_uuid,
}


def supplemental_formats(validator_cls: type) -> dict[str, Callable[[Any], bool]] | None:
    return DRAFT4_SUPPLEMENTAL_FORMATS if validator_cls is jsonschema_rs.Draft4Validator else None


# Formats with a standard definition; servers validate them that way whatever draft the schema declares.
STANDARD_FORMATS = frozenset(
    {
        "date",
        "date-time",
        "duration",
        "email",
        "hostname",
        "idn-email",
        "idn-hostname",
        "ipv4",
        "ipv6",
        "iri",
        "iri-reference",
        "json-pointer",
        "regex",
        "relative-json-pointer",
        "time",
        "uri",
        "uri-reference",
        "uri-template",
        "uuid",
    }
)

# Format names that each `jsonschema_rs` validator class actually validates (after
# the supplemental `uuid` registration for Draft 4 above). Anything outside the
# matching set is annotation-only under that draft: negative-format generation
# cannot produce a value the validator considers wrong, so callers should skip.
VALIDATED_FORMATS_BY_DRAFT: dict[type[jsonschema_rs.Validator], frozenset[str]] = {
    jsonschema_rs.Draft4Validator: frozenset(
        {
            "date",
            "date-time",
            "duration",
            "email",
            "hostname",
            "idn-email",
            "ipv4",
            "ipv6",
            "regex",
            "time",
            "uri",
            "uuid",
        }
    ),
    jsonschema_rs.Draft6Validator: frozenset(
        {
            "date",
            "date-time",
            "email",
            "hostname",
            "idn-email",
            "ipv4",
            "ipv6",
            "json-pointer",
            "regex",
            "time",
            "uri",
            "uri-reference",
            "uri-template",
        }
    ),
    jsonschema_rs.Draft7Validator: frozenset(
        {
            "date",
            "date-time",
            "email",
            "hostname",
            "idn-email",
            "idn-hostname",
            "ipv4",
            "ipv6",
            "iri",
            "iri-reference",
            "json-pointer",
            "regex",
            "relative-json-pointer",
            "time",
            "uri",
            "uri-reference",
            "uri-template",
        }
    ),
    jsonschema_rs.Draft201909Validator: STANDARD_FORMATS,
    jsonschema_rs.Draft202012Validator: STANDARD_FORMATS,
}


validator_cache: BoundedCache = BoundedCache(maxsize=1024)
_validator_failure_cache: BoundedCache = BoundedCache(maxsize=1024)
_seeded_validator_cache: BoundedCache = BoundedCache(maxsize=16)
# Entries pin the bundle whose `id()` is part of the key so GC can't reuse the id.
_bundle_registry_cache: BoundedCache = BoundedCache(maxsize=16)
_bundle_uri_counter = count()
_REFERENCE_INTO_BUNDLE = f"{REFERENCE_TO_BUNDLE_PREFIX}/"


def _absolute_bundle_refs(node: JsonValue, uri: str) -> JsonValue:
    """Point `#/x-bundled/...` at `uri` so it resolves in the registry rather than in this document."""
    if isinstance(node, dict):
        result: dict[str, JsonValue] = {}
        changed = False
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(_REFERENCE_INTO_BUNDLE):
                result[key] = f"{uri}{value}"
                changed = True
            else:
                rewritten = _absolute_bundle_refs(value, uri)
                result[key] = rewritten
                changed = changed or rewritten is not value
        return result if changed else node
    if isinstance(node, list):
        items: list[JsonValue] = []
        changed = False
        for item in node:
            rewritten = _absolute_bundle_refs(item, uri)
            items.append(rewritten)
            changed = changed or rewritten is not item
        if changed:
            return items
    return node


def _bundle_registry(bundled: dict[str, Any], validator_cls: type) -> tuple[str, jsonschema_rs.Registry]:
    """The registry holding `bundled`, built once per bundle and draft."""
    cache_key = (id(bundled), validator_cls)
    cached = _bundle_registry_cache.get(cache_key)
    if cached is not MISSING:
        return cached[0], cached[1]
    uri = f"urn:schemathesis:bundle:{next(_bundle_uri_counter)}"
    registry = jsonschema_rs.Registry(
        [(uri, {BUNDLE_STORAGE_KEY: bundled})],
        draft=CANONICALIZE_DRAFT_BY_VALIDATOR[validator_cls],
    )
    _bundle_registry_cache[cache_key] = (uri, registry, bundled)
    return uri, registry


def _split_bundle(schema: JsonSchema, validator_cls: type) -> tuple[JsonSchema, jsonschema_rs.Registry | None]:
    """Move the bundle out of the schema, so compiling it does not copy the whole bundle in.

    Spliced in, every validator compiles the entire bundle whether or not it references it -
    a shared registry compiles it once for all of them.
    """
    schema, uri, registry = _detach_bundle(schema, validator_cls)
    if uri is None:
        return schema, registry
    return cast("JsonSchema", _absolute_bundle_refs(schema, uri)), registry


def _detach_bundle(
    schema: JsonSchema, validator_cls: type
) -> tuple[JsonSchema, str | None, jsonschema_rs.Registry | None]:
    """The schema without its bundle, with the URI its references into the bundle resolve against."""
    if not isinstance(schema, dict):
        return schema, None, None
    bundled = schema.get(BUNDLE_STORAGE_KEY)
    if not isinstance(bundled, dict) or validator_cls not in CANONICALIZE_DRAFT_BY_VALIDATOR:
        return schema, None, None
    uri, registry = _bundle_registry(bundled, validator_cls)
    without_bundle = {key: value for key, value in schema.items() if key != BUNDLE_STORAGE_KEY}
    return without_bundle, uri, registry


def _build_validator(
    schema: JsonSchema, validator_cls: type, registry: jsonschema_rs.Registry | None = None
) -> jsonschema_rs.Validator:
    # References arrive bundled, so a build never needs the network; a `$schema` URL would otherwise be fetched.
    kwargs: dict[str, Any] = {
        "validate_formats": True,
        "pattern_options": FANCY_REGEX_OPTIONS,
        "offline": True,
        "formats": supplemental_formats(validator_cls),
    }
    if registry is not None:
        kwargs["registry"] = registry
    return validator_cls(schema, **kwargs)


def make_validator(schema: JsonSchema, validator_cls: type) -> jsonschema_rs.Validator:
    """Build a validator with project-wide kwargs: format/pattern checks and Draft 4 supplements."""
    schema, uri, registry = _detach_bundle(schema, validator_cls)
    # The URI names one bundle, so the schema can be keyed before its references are rewritten to point there.
    try:
        cache_key: tuple[str, str | None, type] | None = (
            jsonschema_rs.canonical.json.to_string(schema),
            uri,
            validator_cls,
        )
    except (TypeError, ValueError):
        cache_key = None
    if cache_key is not None:
        cached = validator_cache.get(cache_key)
        if cached is not MISSING:
            return cached
        failure = _validator_failure_cache.get(cache_key)
        if failure is not MISSING:
            raise failure.with_traceback(None)
    if uri is not None:
        schema = cast("JsonSchema", _absolute_bundle_refs(schema, uri))
    try:
        validator = _build_validator(schema, validator_cls, registry)
    except jsonschema_rs.ValidationError as exc:
        if cache_key is not None:
            _validator_failure_cache[cache_key] = exc
        raise
    if cache_key is not None:
        validator_cache[cache_key] = validator
    return validator


def make_validator_with_seed(
    schema_builder: Callable[[], JsonSchema],
    validator_cls: type,
    seed: tuple[Any, ...],
    keep_alive: tuple[Any, ...] = (),
) -> jsonschema_rs.Validator:
    """Cache a validator by `seed` directly, skipping canonical-JSON serialization."""
    cache_key = (seed, validator_cls)
    cached = _seeded_validator_cache.get(cache_key)
    if cached is not MISSING:
        return cached[0]
    schema, registry = _split_bundle(schema_builder(), validator_cls)
    validator = _build_validator(schema, validator_cls, registry)
    _seeded_validator_cache[cache_key] = (validator, keep_alive)
    return validator


def make_validator_for(schema: JsonSchema) -> jsonschema_rs.Validator:
    """Like `make_validator`, but auto-detects the draft from `$schema` (defaults to Draft 2020-12)."""
    return make_validator(schema, jsonschema_rs.validator_cls_for(schema))


def build_validator_for(schema: JsonSchema) -> jsonschema_rs.Validator:
    """Like `make_validator_for`, but for callers already memoized on a cheaper key."""
    validator_cls = jsonschema_rs.validator_cls_for(schema)
    schema, registry = _split_bundle(schema, validator_cls)
    return _build_validator(schema, validator_cls, registry)


def schema_with_bundle(schema: JsonSchema, root_schema: JsonSchema) -> JsonSchema:
    """Splice `x-bundled` from `root_schema` into `schema` so nested `$ref`s resolve at the per-schema root."""
    if not isinstance(schema, dict) or not isinstance(root_schema, dict):
        return schema
    bundled = root_schema.get(BUNDLE_STORAGE_KEY)
    if bundled is None or BUNDLE_STORAGE_KEY in schema:
        return schema
    return {**schema, BUNDLE_STORAGE_KEY: bundled}


def maybe_resolve_bundled(schema: dict[str, Any]) -> dict[str, Any]:
    """Follow `$ref` into a sibling `x-bundled` map; return `schema` as-is when not a bundled-ref node."""
    return resolve_bundled_ref(schema, schema.get(BUNDLE_STORAGE_KEY))


def resolve_bundled_root(schema: dict[str, Any]) -> dict[str, Any]:
    """Follow a root `$ref` into `x-bundled`, keeping the bundle so `$ref`s inside the target still resolve."""
    # `schema_with_bundle` preserves shape: dict in, dict out.
    return cast("dict[str, Any]", schema_with_bundle(maybe_resolve_bundled(schema), schema))


def resolve_with_root_bundle(schema: dict[str, Any], root_schema: dict[str, Any]) -> dict[str, Any]:
    """Follow a bundled `$ref` (e.g. `#/x-bundled/Foo`) by splicing `root_schema`'s bundle into `schema`."""
    # `schema_with_bundle` preserves shape: dict in, dict out.
    return maybe_resolve_bundled(cast("dict[str, Any]", schema_with_bundle(schema, root_schema)))


def resolve_bundled_ref(schema: dict[str, Any], bundled: object) -> dict[str, Any]:
    """Follow `$ref` chains into the `bundled` map; return `schema` as-is when it does not point into it."""
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not isinstance(bundled, dict):
        return schema
    target = bundled.get(ref.rsplit("/", 1)[-1])
    if not isinstance(target, dict):
        return schema
    ref = target.get("$ref")
    if not isinstance(ref, str):
        return target
    # Alias components chain `$ref`s; the hop bound stops reference cycles.
    for _ in range(len(bundled)):
        next_target = bundled.get(ref.rsplit("/", 1)[-1])
        if not isinstance(next_target, dict):
            return target
        target = next_target
        ref = target.get("$ref")
        if not isinstance(ref, str):
            return target
    return target


def is_unsatisfiable(schema: object) -> bool:
    """True for a schema no value satisfies — `false`, or `not` against the schema that accepts everything."""
    return schema is False or (isinstance(schema, dict) and schema.get("not") == {})


def is_valid(value: object, schema: JsonSchema) -> bool:
    """Return True if value satisfies schema, False if it does not.

    Returns True on any validation error so that values that cannot be checked
    are passed through rather than silently dropped.
    """
    try:
        return make_validator_for(schema).is_valid(value)
    except Exception:
        return True


CANONICALIZE_DRAFT_BY_VALIDATOR: dict[type[jsonschema_rs.Validator], int] = {
    jsonschema_rs.Draft4Validator: jsonschema_rs.Draft4,
    jsonschema_rs.Draft6Validator: jsonschema_rs.Draft6,
    jsonschema_rs.Draft7Validator: jsonschema_rs.Draft7,
    jsonschema_rs.Draft201909Validator: jsonschema_rs.Draft201909,
    jsonschema_rs.Draft202012Validator: jsonschema_rs.Draft202012,
}


__all__ = [
    "ALL_KEYWORDS",
    "BundleCache",
    "Bundler",
    "BundleError",
    "CANONICALIZE_DRAFT_BY_VALIDATOR",
    "DRAFT4_SUPPLEMENTAL_FORMATS",
    "supplemental_formats",
    "DRAFT_03_DIALECT",
    "STANDARD_FORMATS",
    "VALIDATED_FORMATS_BY_DRAFT",
    "FANCY_REGEX_OPTIONS",
    "is_unsatisfiable",
    "is_valid",
    "make_validator",
    "make_validator_for",
    "maybe_resolve_bundled",
    "resolve_bundled_ref",
    "resolve_bundled_root",
    "resolve_with_root_bundle",
    "bundle",
    "schema_with_bundle",
    "REFERENCE_TO_BUNDLE_PREFIX",
    "BUNDLE_STORAGE_KEY",
    "get_type",
    "unbundle",
    "unbundle_path",
]
