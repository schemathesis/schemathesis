from hypothesis import given, settings
from hypothesis import strategies as st

import schemathesis
from schemathesis.generation import GenerationMode


def test_binary_format_skips_type_mutation():
    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.2",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/upload": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "multipart/form-data": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["file"],
                                        "properties": {"file": {"type": "string", "format": "binary"}},
                                    }
                                }
                            },
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
        # Binary format accepts any bytes, so type mutations are ineffective.
        # We should get structural mutations instead: wrong body type, missing required field, or extra fields
        is_structural_mutation = (
            not isinstance(case.body, dict) or "file" not in case.body or len(case.body) > 1  # Extra fields
        )
        assert is_structural_mutation, "Expected structural mutations for overly permissive binary schema"

    check()


def test_binary_format_with_custom_media_type_avoids_false_negatives():
    PNG_DATA = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    schemathesis.openapi.media_type("image/png", st.just(PNG_DATA))

    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.2",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/upload": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "multipart/form-data": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["image"],
                                        "properties": {"image": {"type": "string", "format": "binary"}},
                                    },
                                    "encoding": {"image": {"contentType": "image/png"}},
                                }
                            },
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
        # Custom strategies should be skipped in negative mode to avoid false negatives
        # Should NOT get valid PNG data
        if isinstance(case.body, dict) and "image" in case.body:
            data = case.body.get("image")
            if isinstance(data, bytes) and len(data) >= 8:
                assert data[:8] != PNG_DATA[:8], "Custom strategy should not be used in negative mode"

        # Should get structural mutations instead
        is_structural_mutation = (
            not isinstance(case.body, dict) or "image" not in case.body or len(case.body) > 1  # Extra fields
        )
        assert is_structural_mutation, "Expected structural mutations when custom strategy is skipped"

    check()
