import uuid
from collections.abc import Callable
from typing import Any

import jsonschema_rs

from schemathesis.core.cache import MISSING, BoundedCache
from schemathesis.core.jsonschema.bundler import (
    BUNDLE_STORAGE_KEY,
    REFERENCE_TO_BUNDLE_PREFIX,
    BundleCache,
    BundleError,
    Bundler,
    bundle_for_generation,
    bundle_for_validation,
    unbundle,
    unbundle_path,
)
from schemathesis.core.jsonschema.keywords import ALL_KEYWORDS
from schemathesis.core.jsonschema.types import JsonSchema, get_type

# Support ECMA-262 lookahead/lookbehind. The limit fits legit large quantifiers but fast-fails
# degenerate ones (e.g. `{0,10000000}` from a huge `maxLength`) instead of burning seconds + ~1GB.
FANCY_REGEX_OPTIONS = jsonschema_rs.FancyRegexOptions(size_limit=150_000_000)

# Draft 3 predates the keyword semantics every conversion here assumes and is rejected outright
DRAFT_03_DIALECT = "http://json-schema.org/draft-03/schema#"


def _is_valid_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return True
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


# Formats that newer JSON Schema drafts validate natively but Draft 4 (used by
# OpenAPI 2.0 / 3.0) does not. Registered only for Draft4Validator so built-in
# implementations in newer drafts are not overridden.
DRAFT4_SUPPLEMENTAL_FORMATS: dict[str, Callable[[Any], bool]] = {"uuid": _is_valid_uuid}


# Format names that each `jsonschema_rs` validator class actually validates (after
# the supplemental `uuid` registration for Draft 4 above). Anything outside the
# matching set is annotation-only under that draft: negative-format generation
# cannot produce a value the validator considers wrong, so callers should skip.
VALIDATED_FORMATS_BY_DRAFT: dict[type[jsonschema_rs.Validator], frozenset[str]] = {
    jsonschema_rs.Draft4Validator: frozenset(
        {"date", "date-time", "email", "hostname", "idn-email", "ipv4", "ipv6", "regex", "time", "uri", "uuid"}
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
    jsonschema_rs.Draft201909Validator: frozenset(
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
    ),
    jsonschema_rs.Draft202012Validator: frozenset(
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
    ),
}


# Negative-property recursion in the coverage phase builds a validator per sub-schema per call;
# without this LRU, the same schema shape recompiles thousands of times on schemas like k8s Pods.
validator_cache: BoundedCache = BoundedCache(maxsize=1024)
# Builds fail deterministically but slowly (fancy-regex compiles giant quantifiers before
# rejecting them); cache the failure so the same schema isn't recompiled on every call.
_validator_failure_cache: BoundedCache = BoundedCache(maxsize=1024)
# Entries pin the schemas whose `id()` is part of the cache key so GC can't reuse the id.
_seeded_validator_cache: BoundedCache = BoundedCache(maxsize=1024)


def _build_validator(schema: JsonSchema, validator_cls: type) -> jsonschema_rs.Validator:
    kwargs: dict[str, Any] = {"validate_formats": True, "pattern_options": FANCY_REGEX_OPTIONS}
    if validator_cls is jsonschema_rs.Draft4Validator:
        kwargs["formats"] = DRAFT4_SUPPLEMENTAL_FORMATS
    return validator_cls(schema, **kwargs)


def make_validator(schema: JsonSchema, validator_cls: type) -> jsonschema_rs.Validator:
    """Build a validator with project-wide kwargs: format/pattern checks and Draft 4 supplements."""
    try:
        cache_key: tuple[str, type] | None = (jsonschema_rs.canonical.json.to_string(schema), validator_cls)
    except (TypeError, ValueError):
        cache_key = None
    if cache_key is not None:
        cached = validator_cache.get(cache_key)
        if cached is not MISSING:
            return cached
        failure = _validator_failure_cache.get(cache_key)
        if failure is not MISSING:
            raise failure.with_traceback(None)
    try:
        validator = _build_validator(schema, validator_cls)
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
    validator = _build_validator(schema_builder(), validator_cls)
    _seeded_validator_cache[cache_key] = (validator, keep_alive)
    return validator


def make_validator_for(schema: JsonSchema) -> jsonschema_rs.Validator:
    """Like `make_validator`, but auto-detects the draft from `$schema` (defaults to Draft 2020-12)."""
    return make_validator(schema, jsonschema_rs.validator_cls_for(schema))


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
    ref = schema.get("$ref")
    bundled = schema.get(BUNDLE_STORAGE_KEY)
    if not isinstance(ref, str) or not isinstance(bundled, dict):
        return schema
    target = bundled.get(ref.rsplit("/", 1)[-1])
    return target if isinstance(target, dict) else schema


def is_valid(value: object, schema: JsonSchema) -> bool:
    """Return True if value satisfies schema, False if it does not.

    Returns True on any validation error so that values that cannot be checked
    are passed through rather than silently dropped.
    """
    try:
        return make_validator_for(schema).is_valid(value)
    except Exception:
        return True


# Draft number `canonicalize` should interpret a schema under, keyed by the schema's validator class.
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
    "DRAFT_03_DIALECT",
    "VALIDATED_FORMATS_BY_DRAFT",
    "FANCY_REGEX_OPTIONS",
    "is_valid",
    "make_validator",
    "make_validator_for",
    "maybe_resolve_bundled",
    "bundle_for_generation",
    "bundle_for_validation",
    "schema_with_bundle",
    "REFERENCE_TO_BUNDLE_PREFIX",
    "BUNDLE_STORAGE_KEY",
    "get_type",
    "unbundle",
    "unbundle_path",
]
