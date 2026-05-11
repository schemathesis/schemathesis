from __future__ import annotations

import pytest

from schemathesis.generation import GenerationMode
from tools.coverage.audit import PhaseName, audit_schema

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
    assert outcome.result.errors and outcome.result.errors[0].startswith("load_failed:")


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
    assert "form-data" in outcome.result.errors[0]


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
