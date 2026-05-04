import uuid
from collections.abc import Callable
from typing import Any

import jsonschema_rs

from .bundler import (
    BUNDLE_STORAGE_KEY,
    REFERENCE_TO_BUNDLE_PREFIX,
    BundleCache,
    BundleError,
    Bundler,
    bundle,
    bundle_for_generation,
    bundle_for_validation,
    unbundle,
    unbundle_path,
)
from .keywords import ALL_KEYWORDS
from .types import get_type

# Support lookahead/lookbehind assertions common in ECMA-262 patterns,
# with a large size limit to handle schemas with large quantifiers (e.g., {1,51200})
FANCY_REGEX_OPTIONS = jsonschema_rs.FancyRegexOptions(size_limit=1_000_000_000)


def _is_valid_uuid(value: Any) -> bool:
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


def make_validator(schema: Any, validator_cls: type) -> jsonschema_rs.Validator:
    """Build a validator with project-wide kwargs: format/pattern checks and Draft 4 supplements."""
    kwargs: dict[str, Any] = {"validate_formats": True, "pattern_options": FANCY_REGEX_OPTIONS}
    if validator_cls is jsonschema_rs.Draft4Validator:
        kwargs["formats"] = DRAFT4_SUPPLEMENTAL_FORMATS
    return validator_cls(schema, **kwargs)


def make_validator_for(schema: Any) -> jsonschema_rs.Validator:
    """Like `make_validator`, but auto-detects the draft from `$schema` (defaults to Draft 2020-12)."""
    return make_validator(schema, jsonschema_rs.validator_cls_for(schema))


def schema_with_bundle(schema: Any, root_schema: Any) -> Any:
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


def is_valid(value: Any, schema: dict[str, Any]) -> bool:
    """Return True if value satisfies schema, False if it does not.

    Returns True on any validation error so that values that cannot be checked
    are passed through rather than silently dropped.
    """
    try:
        return make_validator_for(schema).is_valid(value)
    except Exception:
        return True


__all__ = [
    "ALL_KEYWORDS",
    "bundle",
    "BundleCache",
    "Bundler",
    "BundleError",
    "DRAFT4_SUPPLEMENTAL_FORMATS",
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
