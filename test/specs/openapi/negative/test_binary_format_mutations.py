from __future__ import annotations

from typing import Any

import jsonschema_rs
import pytest
from hypothesis import strategies as st

import schemathesis
from schemathesis.core.jsonschema import make_validator
from schemathesis.generation import GenerationMode
from test.utils import run_given


def is_structural_mutation(body: Any, required_field: str) -> bool:
    return not isinstance(body, dict) or required_field not in body or len(body) > 1


def is_type_mutation(body: Any, field: str, expected_type: type) -> bool:
    return isinstance(body, dict) and field in body and not isinstance(body.get(field), expected_type)


@pytest.mark.parametrize(
    "encoding",
    [
        pytest.param(None, id="without_encoding"),
        pytest.param({"file": {"contentType": "image/png"}}, id="with_encoding"),
    ],
)
def test_binary_format_negative_mutations(ctx, encoding):
    if encoding:
        # Register custom media type to test that it's skipped in negative mode
        schemathesis.openapi.media_type("image/png", st.just(b"\x89PNG\r\n\x1a\n"))

    content = {
        "schema": {
            "type": "object",
            "required": ["file"],
            "properties": {"file": {"type": "string", "format": "binary"}},
        }
    }
    if encoding:
        content["encoding"] = encoding

    operation = ctx.openapi.load_operation(
        "POST /upload",
        {
            "requestBody": {
                "content": {"multipart/form-data": content},
                "required": True,
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    strategy = operation.as_strategy(generation_mode=GenerationMode.NEGATIVE)

    @run_given(case=strategy, max_examples=10)
    def check(case):
        assert is_structural_mutation(case.body, "file") or is_type_mutation(case.body, "file", bytes)


def test_binary_format_negative_mutations_with_multiple_content_types(ctx):
    operation = ctx.openapi.load_operation(
        "POST /upload",
        {
            "requestBody": {
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "required": ["file"],
                            "properties": {"file": {"type": "string", "format": "binary"}},
                        },
                        "encoding": {"file": {"contentType": "image/jpeg, text/plain"}},
                    }
                },
                "required": True,
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    @run_given(case=operation.as_strategy(generation_mode=GenerationMode.NEGATIVE), max_examples=10)
    def check(case):
        assert is_structural_mutation(case.body, "file") or is_type_mutation(case.body, "file", bytes)


def test_negative_body_is_invalid_against_real_schema_when_only_field_is_optional_binary(ctx):
    # `format: binary` is permissive at runtime; mutations producing valid strings aren't actually negative.
    operation = ctx.openapi.load_operation(
        "PUT /upload",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "properties": {"captionfile": {"type": "string", "format": "binary"}},
                        }
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )
    real_validator = make_validator(operation.body[0].optimized_schema, jsonschema_rs.Draft4Validator)

    @run_given(case=operation.as_strategy(generation_mode=GenerationMode.NEGATIVE), max_examples=20)
    def check(case):
        if not isinstance(case.body, dict):
            return
        assert not real_validator.is_valid(case.body), f"NEGATIVE body still valid against real schema: {case.body!r}"


def test_custom_media_type_raw_binary_body_in_negative_mode(ctx):
    schemathesis.openapi.media_type("application/x-tar", st.just(b""))

    operation = ctx.openapi.load_operation(
        "POST /upload",
        {
            "parameters": [{"name": "limit", "in": "query", "required": True, "schema": {"type": "integer"}}],
            "requestBody": {
                "content": {"application/x-tar": {"schema": {"type": "string", "format": "binary"}}},
                "required": True,
            },
            "responses": {"200": {"description": "OK"}},
        },
        version="3.0.3",
    )

    strategy = operation.as_strategy(generation_mode=GenerationMode.NEGATIVE)

    @run_given(case=strategy, max_examples=10)
    def check(case):
        assert isinstance(case.body, bytes)
