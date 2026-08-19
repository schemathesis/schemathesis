from __future__ import annotations

from schemathesis.core import jsonschema
from schemathesis.core.jsonschema import bundler, resolver
from schemathesis.generation import hypothesis
from schemathesis.specs.openapi import patterns
from schemathesis.specs.openapi.coverage import _schema


def clear_internal_caches() -> None:
    _schema._draw_outcome.cache_clear()
    _schema._pattern_strategy.cache_clear()
    _schema._FORMAT_VALIDATORS.clear()
    _schema._REMOVE_EXAMPLES_CACHE.clear()
    _schema._READY_BUNDLE_CACHE.clear()
    patterns.normalize_regex.cache_clear()
    patterns.is_valid_jsonschema_rs_regex.cache_clear()
    patterns._parse_regex.cache_clear()
    patterns.pattern_length_bounds.cache_clear()
    patterns.matches_every_string.cache_clear()
    patterns.pattern_requires_literal.cache_clear()
    patterns.pattern_requires_char_outside.cache_clear()
    patterns.update_quantifier.cache_clear()
    patterns._pattern_lengths.cache_clear()
    patterns.pattern_length_is_unreachable.cache_clear()
    patterns.pin_pattern_length.cache_clear()
    resolver._resolve_reference_uri_with_document.cache_clear()
    bundler._UNBUNDLED_COMPONENTS_CACHE.clear()
    jsonschema.validator_cache.clear()
    jsonschema._validator_failure_cache.clear()
    jsonschema._seeded_validator_cache.clear()
    jsonschema._bundle_registry_cache.clear()
    hypothesis.schema_generation_cache.clear()
    hypothesis.custom_formats_cache.clear()
    hypothesis.canonical_strategy_cache.clear()
    hypothesis.canonical_form_cache.clear()
    hypothesis._first_param_cache.clear()
