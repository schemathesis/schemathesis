from __future__ import annotations

import pytest

from schemathesis.generation import GenerationMode
from schemathesis.generation import hypothesis as hypothesis_internals
from schemathesis.specs.openapi.coverage import _schema as coverage_internals
from tools.coverage.audit import (
    _KNOWN_UNSUPPORTED_MEDIA_TYPES,
    _KNOWN_UNSUPPORTED_PREFIXES,
    PhaseName,
    _strip_known_unsupported_media_types,
    audit_schema,
)
from tools.coverage.caches import clear_internal_caches

_PATHS = {
    "/users/{id}": {
        "get": {
            "parameters": [
                {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}},
            ],
            "responses": {"200": {"description": "OK"}},
        },
    },
    "/users": {
        "post": {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["name"],
                            "properties": {"name": {"type": "string"}},
                        }
                    }
                }
            },
            "responses": {"201": {"description": "Created"}},
        },
    },
}


@pytest.mark.parametrize("phase", [PhaseName.COVERAGE, PhaseName.FUZZING])
def test_audit_schema_drives_each_phase(ctx, phase):
    raw = ctx.openapi.build_schema(_PATHS)
    outcome = audit_schema(raw, api="t", corpus="external", phase=phase, fuzzing_max_examples=3)
    assert outcome.coverage_map is not None
    assert outcome.result.errors == []
    assert outcome.result.operations == 2
    assert outcome.result.cases_generated > 0
    assert outcome.result.phase == phase.value


def test_audit_schema_examples_phase_yields_no_cases_when_schema_has_no_examples(ctx):
    raw = ctx.openapi.build_schema(_PATHS)
    outcome = audit_schema(raw, api="t", corpus="external", phase=PhaseName.EXAMPLES)
    assert outcome.result.cases_generated == 0
    assert outcome.result.operations == 2
    assert outcome.result.errors == []


def test_audit_schema_records_load_failure_on_invalid_schema():
    outcome = audit_schema({"not": "valid"}, api="t", corpus="external", phase=PhaseName.COVERAGE)
    assert outcome.coverage_map is None
    assert outcome.result.operations == 0
    assert outcome.result.errors and outcome.result.errors[0].stage == "load_failed"


def test_audit_schema_records_unsatisfiable_modes_for_no_input_operation(ctx):
    raw = ctx.openapi.build_schema({"/healthcheck": {"get": {"responses": {"200": {"description": "OK"}}}}})
    outcome = audit_schema(raw, api="t", corpus="external", phase=PhaseName.FUZZING, fuzzing_max_examples=2)
    assert outcome.result.errors == []
    assert outcome.result.cases_generated >= 1
    assert outcome.result.unsatisfiable == [("GET /healthcheck", "negative")]


def test_audit_schema_does_not_synthesize_response_coverage(ctx):
    raw = ctx.openapi.build_schema(
        {
            "/users": {
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"201": {"description": "Created"}},
                }
            }
        }
    )
    outcome = audit_schema(raw, api="t", corpus="external", phase=PhaseName.COVERAGE)
    assert outcome.result.statistic is not None
    assert "responses" not in outcome.result.statistic
    assert all(not gap.get("kind", "").startswith("response_") for gap in outcome.result.gaps)


def test_audit_schema_records_required_invalid_for_form_urlencoded_body(ctx):
    # `requests` collapses an empty form body to None, which used to skip the body schema
    # in the recorder; the omit-required negative case must still register `/required` as
    # invalid so the keyword stops appearing as `needs_invalid`.
    raw = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/x-www-form-urlencoded": {
                                "schema": {
                                    "type": "object",
                                    "required": ["x"],
                                    "properties": {"x": {"type": "string"}},
                                },
                            },
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )
    outcome = audit_schema(raw, api="t", corpus="external", phase=PhaseName.COVERAGE)
    form_required = [
        u
        for u in outcome.result.uncovered_keywords
        if u.get("parameter") == "application/x-www-form-urlencoded" and u.get("schema_path", "").endswith("/required")
    ]
    assert form_required == [], outcome.result.uncovered_keywords


def test_audit_schema_skips_unserializable_media_types_without_aborting_operation(ctx):
    # An unserializable body alternative (no built-in serializer for `application/x-msgpack`)
    # must not abort coverage for sibling media types that *are* serializable.
    raw = ctx.openapi.build_schema(
        {
            "/messages": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"type": "object"}},
                            "application/x-msgpack": {"schema": {"type": "object"}},
                            "application/x-www-form-urlencoded": {"schema": {"type": "object"}},
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )
    outcome = audit_schema(raw, api="t", corpus="external", phase=PhaseName.COVERAGE)
    assert outcome.result.errors == []
    assert outcome.result.cases_generated > 0


def test_audit_schema_preserves_error_message_for_malformed_media_type(ctx):
    # Recording only the exception class name forces re-running the audit to learn what was wrong.
    raw = ctx.openapi.build_schema(
        {
            "/x": {
                "post": {
                    "requestBody": {"required": True, "content": {"form-data": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    outcome = audit_schema(raw, api="t", corpus="external", phase=PhaseName.COVERAGE)
    assert outcome.result.errors
    assert "form-data" in outcome.result.errors[0].message


def test_audit_schema_fuzzing_uses_only_requested_generation_modes(ctx):
    raw = ctx.openapi.build_schema(_PATHS)
    outcome = audit_schema(
        raw,
        api="t",
        corpus="external",
        phase=PhaseName.FUZZING,
        generation_modes=[GenerationMode.POSITIVE],
        fuzzing_max_examples=2,
    )
    assert outcome.result.cases_generated == 4  # 2 ops * 2 examples * 1 mode


@pytest.mark.parametrize(
    ("content_types", "expected_kept", "expected_unknown"),
    [
        (["application/json", "application/x-msgpack"], {"application/json"}, []),
        (["application/json", "image/png", "video/mp4"], {"application/json"}, []),
        (["application/json", "text/html"], {"application/json", "text/html"}, ["text/html"]),
        (
            ["application/json", "text/html", "text/csv"],
            {"application/json", "text/html", "text/csv"},
            ["text/csv", "text/html"],
        ),
        (
            ["application/json", "application/x-msgpack", "image/png", "text/html"],
            {"application/json", "text/html"},
            ["text/html"],
        ),
    ],
    ids=["explicit-deny", "prefix-deny", "unknown-surfaced", "multiple-unknowns-sorted", "mixed"],
)
def test_strip_known_unsupported_openapi3_content(ctx, content_types, expected_kept, expected_unknown):
    schema = ctx.openapi.build_schema(
        {
            "/x": {
                "post": {
                    "requestBody": {"content": {mt: {"schema": {"type": "string"}} for mt in content_types}},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    filtered, unknown = _strip_known_unsupported_media_types(schema)
    assert set(filtered["paths"]["/x"]["post"]["requestBody"]["content"]) == expected_kept
    assert unknown == expected_unknown


def test_strip_known_unsupported_drops_empty_request_body(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/x": {
                "post": {
                    "requestBody": {"content": {"application/x-msgpack": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    filtered, _ = _strip_known_unsupported_media_types(schema)
    assert "requestBody" not in filtered["paths"]["/x"]["post"]


@pytest.mark.parametrize(
    ("consumes", "expected_kept", "expected_unknown"),
    [
        (
            ["application/json", "application/x-msgpack", "image/png", "text/html"],
            ["application/json", "text/html"],
            ["text/html"],
        ),
        (["application/json"], ["application/json"], []),
        (["application/x-msgpack", "image/png"], [], []),
    ],
    ids=["mixed", "all-supported", "all-denied"],
)
def test_strip_known_unsupported_swagger2_per_op_consumes(ctx, consumes, expected_kept, expected_unknown):
    schema = ctx.openapi.build_schema(
        {
            "/x": {
                "post": {"consumes": consumes, "responses": {"200": {"description": "OK"}}},
            }
        },
        version="2.0",
    )
    filtered, unknown = _strip_known_unsupported_media_types(schema)
    assert filtered["paths"]["/x"]["post"]["consumes"] == expected_kept
    assert unknown == expected_unknown


@pytest.mark.parametrize("media_type", sorted(_KNOWN_UNSUPPORTED_MEDIA_TYPES))
def test_known_unsupported_explicit_entry_is_stripped(ctx, media_type):
    schema = ctx.openapi.build_schema(
        {
            "/x": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"type": "object"}},
                            media_type: {"schema": {"type": "string"}},
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    filtered, unknown = _strip_known_unsupported_media_types(schema)
    assert set(filtered["paths"]["/x"]["post"]["requestBody"]["content"]) == {"application/json"}
    assert unknown == []


@pytest.mark.parametrize("prefix", _KNOWN_UNSUPPORTED_PREFIXES)
def test_known_unsupported_prefix_is_stripped(ctx, prefix):
    sample = f"{prefix}sample"
    schema = ctx.openapi.build_schema(
        {
            "/x": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"type": "object"}},
                            sample: {"schema": {"type": "string", "format": "binary"}},
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    filtered, unknown = _strip_known_unsupported_media_types(schema)
    assert set(filtered["paths"]["/x"]["post"]["requestBody"]["content"]) == {"application/json"}
    assert unknown == []


def test_strip_known_unsupported_swagger2_global_consumes(ctx):
    schema = ctx.openapi.build_schema(
        {"/x": {"get": {"responses": {"200": {"description": "OK"}}}}},
        version="2.0",
        consumes=["application/json", "application/x-msgpack", "text/csv"],
    )
    filtered, unknown = _strip_known_unsupported_media_types(schema)
    assert filtered["consumes"] == ["application/json", "text/csv"]
    assert unknown == ["text/csv"]


def test_audit_schema_filters_uncovered_keywords_under_errored_operations(ctx):
    raw = ctx.openapi.build_schema(
        {
            "/items": {
                "get": {"responses": {"200": {"description": "OK"}}},
            },
            "/broken": {
                "post": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Missing"}}}
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    outcome = audit_schema(raw, api="t", corpus="external", phase=PhaseName.COVERAGE)
    assert outcome.result.errors, "the broken op should produce an operation_build_failed error"
    broken_errors = [
        error for error in outcome.result.errors if error.path == "/broken" and (error.method or "").lower() == "post"
    ]
    assert broken_errors, outcome.result.errors
    assert all("/broken" not in (u.get("schema_path") or "") for u in outcome.result.uncovered_keywords)
    assert all("/broken" not in (g.get("schema_path") or "") for g in outcome.result.gaps)
    assert outcome.result.excluded_by_errors > 0


def test_audit_schema_records_rss_jumps_per_operation(ctx):
    raw = ctx.openapi.build_schema(_PATHS)
    outcome = audit_schema(raw, api="t", corpus="external", phase=PhaseName.COVERAGE)
    if outcome.result.rss_jumps is None:
        pytest.skip("RSS sampling unavailable on this platform")
    assert len(outcome.result.rss_jumps) == 2
    seen = {(jump["method"], jump["path"]) for jump in outcome.result.rss_jumps}
    assert seen == {("GET", "/users/{id}"), ("POST", "/users")}
    assert all(isinstance(jump["delta_bytes"], int) for jump in outcome.result.rss_jumps)


def test_clear_internal_caches_drains_known_caches(ctx):
    # An audit pass populates the schemathesis internal caches; the shared helper must
    # empty them so audit workers don't carry per-schema state forward to the next task.
    raw = ctx.openapi.build_schema(_PATHS)
    audit_schema(raw, api="t", corpus="external", phase=PhaseName.COVERAGE)
    populated_caches = [
        len(coverage_internals._FORMAT_VALIDATORS),
        len(coverage_internals._REMOVE_EXAMPLES_CACHE._data),
        len(hypothesis_internals.schema_generation_cache._data),
        len(hypothesis_internals._canonicalish_result_cache._data),
    ]
    assert any(populated_caches), populated_caches
    clear_internal_caches()
    assert coverage_internals._FORMAT_VALIDATORS == {}
    assert coverage_internals._REMOVE_EXAMPLES_CACHE._data == {}
    assert hypothesis_internals.schema_generation_cache._data == {}
    assert hypothesis_internals.custom_formats_cache._data == {}
    assert hypothesis_internals._resolve_result_cache._data == {}
    assert hypothesis_internals._merged_result_cache._data == {}
    assert hypothesis_internals._canonicalish_result_cache._data == {}
    assert hypothesis_internals._from_schema_result_cache._data == {}
    assert hypothesis_internals._merged_as_strategies_result_cache._data == {}


def test_audit_schema_records_unknown_unsupported(ctx):
    raw = ctx.openapi.build_schema(
        {
            "/x": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"type": "object", "properties": {"a": {"type": "string"}}}},
                            "text/html": {"schema": {"type": "string"}},
                            "application/x-msgpack": {"schema": {"type": "object"}},
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    outcome = audit_schema(raw, api="t", corpus="external", phase=PhaseName.COVERAGE)
    assert outcome.result.unknown_unsupported_media_types == ["text/html"]
