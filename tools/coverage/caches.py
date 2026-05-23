from __future__ import annotations

from schemathesis.generation import hypothesis as hypothesis_internals
from schemathesis.specs.openapi import patterns
from schemathesis.specs.openapi.coverage import _schema as coverage_internals


def clear_internal_caches() -> None:
    coverage_internals.cached_draw.cache_clear()
    coverage_internals._FORMAT_VALIDATORS.clear()
    coverage_internals._REMOVE_EXAMPLES_CACHE.clear()
    patterns.normalize_regex.cache_clear()
    patterns.pattern_length_bounds.cache_clear()
    patterns.update_quantifier.cache_clear()
    hypothesis_internals.schema_generation_cache.clear()
    hypothesis_internals.custom_formats_cache.clear()
    hypothesis_internals._resolve_result_cache.clear()
    hypothesis_internals._merged_result_cache.clear()
    hypothesis_internals._canonicalish_result_cache.clear()
    hypothesis_internals._from_schema_result_cache.clear()
    hypothesis_internals._merged_as_strategies_result_cache.clear()
