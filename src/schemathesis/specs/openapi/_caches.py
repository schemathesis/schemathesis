from __future__ import annotations

from hypothesis.internal import reflection
from hypothesis.stateful import RuleBasedStateMachine, RuleStrategy

from schemathesis.core import jsonschema
from schemathesis.core.jsonschema import bundler, patterns, resolver
from schemathesis.generation import hypothesis
from schemathesis.generation.coverage import DEFAULT_GENERATION_SESSION
from schemathesis.generation.jsonschema import context, strategy
from schemathesis.specs.openapi import negative
from schemathesis.specs.openapi.coverage import _schema


def clear_all() -> None:
    """Empty every process-wide cache that generation fills per schema."""
    DEFAULT_GENERATION_SESSION.close()
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
    patterns.character_rules.cache_clear()
    resolver._resolve_reference_uri_with_document.cache_clear()
    bundler._UNBUNDLED_COMPONENTS_CACHE.clear()
    jsonschema.matches_pattern.cache_clear()
    jsonschema.validator_cache.clear()
    jsonschema._validator_failure_cache.clear()
    jsonschema._seeded_validator_cache.clear()
    jsonschema._bundle_registry_cache.clear()
    hypothesis.custom_formats_cache.clear()
    hypothesis.canonical_strategy_cache.clear()
    hypothesis.canonical_form_cache.clear()
    hypothesis._first_param_cache.clear()
    context._sampled.cache_clear()
    strategy._covers.cache_clear()
    strategy._narrowed.cache_clear()
    strategy._validator.cache_clear()
    strategy._finite_values.cache_clear()
    strategy._string_domain.cache_clear()
    strategy._facet_check.cache_clear()
    strategy._anything_for.cache_clear()
    negative.get_validator.cache_clear()
    negative.get_real_validator.cache_clear()
    negative.split_schema.cache_clear()
    _schema._pattern_implies_format.cache_clear()
    RuleBasedStateMachine._setup_state_per_class.clear()
    RuleStrategy._setup_for.cache_clear()
    reflection.eval_cache.clear()
