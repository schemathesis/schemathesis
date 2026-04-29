from __future__ import annotations

from typing import Any

import jsonschema_rs
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import schemathesis
from schemathesis.core.jsonschema import make_validator
from schemathesis.generation import GenerationMode


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
def test_binary_format_negative_mutations(encoding):
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

    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.2",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/upload": {
                    "post": {
                        "requestBody": {
                            "content": {"multipart/form-data": content},
                            "required": True,
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
    )

    operation = schema["/upload"]["POST"]
    strategy = operation.as_strategy(generation_mode=GenerationMode.NEGATIVE)

    @given(case=strategy)
    @settings(max_examples=10)
    def check(case):
        assert is_structural_mutation(case.body, "file") or is_type_mutation(case.body, "file", bytes)

    check()


def test_negative_body_is_invalid_against_real_schema_when_only_field_is_optional_binary(ctx):
    # `format: binary` is permissive at runtime; mutations producing valid strings aren't actually negative.
    raw_schema = ctx.openapi.build_schema(
        {
            "/upload": {
                "put": {
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
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/upload"]["PUT"]
    real_validator = make_validator(operation.body[0].optimized_schema, jsonschema_rs.Draft4Validator)

    @given(case=operation.as_strategy(generation_mode=GenerationMode.NEGATIVE))
    @settings(max_examples=20, suppress_health_check=list(HealthCheck))
    def check(case):
        if not isinstance(case.body, dict):
            return
        assert not real_validator.is_valid(case.body), f"NEGATIVE body still valid against real schema: {case.body!r}"

    check()


def test_custom_media_type_raw_binary_body_in_negative_mode():
    schemathesis.openapi.media_type("application/x-tar", st.just(b""))

    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/upload": {
                    "post": {
                        "requestBody": {
                            "content": {"application/x-tar": {"schema": {"type": "string", "format": "binary"}}},
                            "required": True,
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
    )

    operation = schema["/upload"]["POST"]
    strategy = operation.as_strategy(generation_mode=GenerationMode.NEGATIVE)

    @given(case=strategy)
    @settings(max_examples=10)
    def check(case):
        assert isinstance(case.body, bytes)

    check()
