from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

import schemathesis
from schemathesis.transport.requests import REQUESTS_TRANSPORT
from schemathesis.transport.wsgi import WSGI_TRANSPORT

HERE = Path(__file__).absolute().parent


SAMPLE_PDF = (HERE / "blank.pdf").read_bytes()
PDFS = st.sampled_from([SAMPLE_PDF])
MEDIA_TYPE = "application/pdf"
ALIAS = "application/x-pdf"


def test_pdf_generation(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/pdf": {
                "post": {
                    "requestBody": {
                        "content": {
                            MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}},
                            ALIAS: {"schema": {"type": "string", "format": "binary"}},
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    schemathesis.openapi.media_type(MEDIA_TYPE, PDFS, aliases=[ALIAS])
    schema = schemathesis.openapi.from_dict(schema)

    strategy = schema["/pdf"]["post"].as_strategy()

    @given(strategy)
    def test(case):
        assert case.body == SAMPLE_PDF
        assert case.media_type in (MEDIA_TYPE, ALIAS)
        for transport in (REQUESTS_TRANSPORT, WSGI_TRANSPORT):
            assert transport.serialize_case(case)["data"] == SAMPLE_PDF

    test()


def test_explicit_example_with_custom_media_type(ctx, cli, snapshot_cli, openapi3_base_url):
    schema_path = ctx.openapi.write_schema(
        {
            "/csv": {
                "post": {
                    "requestBody": {
                        "content": {
                            "text/csv": {
                                "schema": {"type": "string", "format": "binary"},
                                "example": [{"a": 1, "b": 2, "c": 3}, {"a": 3, "b": 4, "c": 5}],
                            },
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    schemathesis.openapi.media_type("text/csv", st.sampled_from([b"a,b,c\n2,3,4"]))

    assert cli.run(str(schema_path), f"--url={openapi3_base_url}", "--mode=positive") == snapshot_cli


def test_malformed_registered_media_type_is_skipped(ctx):
    # Register a malformed media type (no slash, so it can't be parsed)
    schemathesis.specs.openapi.media_types.MEDIA_TYPES["invalid"] = st.binary()

    # Create schema with valid content type that would trigger wildcard search
    schema = ctx.openapi.build_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"type": "object"}},
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    schema = schemathesis.openapi.from_dict(schema)

    # Should not crash when encountering the malformed registered type
    strategy = schema["/data"]["post"].as_strategy()

    @given(strategy)
    def test(case):
        assert case.media_type == "application/json"

    test()


def test_coverage_phase(testdir, openapi3_base_url):
    testdir.make_test(
        f"""
schemathesis.openapi.media_type("image/jpeg", st.just(b""))
schema.config.update(base_url="{openapi3_base_url}")
schema.config.phases.examples.enabled = False
schema.config.phases.fuzzing.enabled = False

@schema.include(path_regex="success").parametrize()
def test(case):
    case.call()
""",
        paths={
            "/success": {
                "post": {
                    "parameters": [
                        {"name": "key", "in": "query", "required": True, "schema": {"type": "integer"}, "example": 42}
                    ],
                    "requestBody": {
                        "content": {
                            "image/jpeg": {
                                "schema": {"format": "base64", "type": "string"},
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_non_serializable_example(testdir, openapi3_base_url):
    testdir.make_test(
        f"""
schema.config.update(base_url="{openapi3_base_url}")
schema.config.phases.examples.enabled = False
schema.config.phases.fuzzing.enabled = False

@schema.include(path_regex="success").parametrize()
@settings(phases=[Phase.explicit])
def test(case):
    case.call()
""",
        paths={
            "/success": {
                "post": {
                    "parameters": [
                        {"name": "key", "in": "query", "required": True, "schema": {"type": "integer"}, "example": 42}
                    ],
                    "requestBody": {
                        "content": {
                            "image/jpeg": {
                                "schema": {"format": "base64", "type": "string"},
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest()
    result.assert_outcomes(skipped=1)


@pytest.mark.openapi_version("3.0")
def test_multipart_encoding_multiple_content_types(ctx):
    PNG_DATA = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
    JPEG_DATA = b"\xff\xd8\xff\xe0" + b"\x00" * 10
    schemathesis.openapi.media_type("image/png", st.just(PNG_DATA))
    schemathesis.openapi.media_type("image/jpeg", st.just(JPEG_DATA))

    spec = ctx.openapi.build_schema(
        {
            "/upload": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"image": {"type": "string", "format": "binary"}},
                                    "required": ["image"],
                                },
                                "encoding": {"image": {"contentType": "image/png, image/jpeg"}},
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    schema = schemathesis.openapi.from_dict(spec)
    operation = schema["/upload"]["POST"]
    strategy = operation.as_strategy()

    content_types_seen = set()

    @given(strategy)
    def test(case):
        if isinstance(case.body, dict):
            files, _ = case.operation.prepare_multipart(case.body, case.multipart_content_types)

            if files:
                for file_tuple in files:
                    name = file_tuple[0]
                    if name == "image":
                        if len(file_tuple) > 1 and isinstance(file_tuple[1], tuple):
                            if len(file_tuple[1]) == 3:
                                _, _, content_type = file_tuple[1]
                                assert content_type in ["image/png", "image/jpeg"], (
                                    f"Got invalid content type: {content_type} (should be 'image/png' or 'image/jpeg', "
                                    f"not the literal 'image/png, image/jpeg')"
                                )
                                content_types_seen.add(content_type)

    test()

    assert len(content_types_seen) == 2, (
        f"Expected both content types to be selected, but only saw: {content_types_seen}"
    )


@pytest.mark.openapi_version("3.0")
def test_multipart_encoding_array_content_type_with_custom_strategy(ctx):
    pdf_data = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"
    schemathesis.openapi.media_type("application/pdf", st.just(pdf_data))

    spec = ctx.openapi.build_schema(
        {
            "/upload": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"attachment": {"type": "string", "format": "binary"}},
                                    "required": ["attachment"],
                                },
                                "encoding": {"attachment": {"contentType": ["application/pdf"]}},
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    schema = schemathesis.openapi.from_dict(spec)
    operation = schema["/upload"]["POST"]
    strategy = operation.as_strategy()

    @given(strategy)
    def test(case):
        assert isinstance(case.body, dict)
        assert case.body["attachment"] == pdf_data
        # Content type should match the value from the encoding array, not be omitted
        assert case.multipart_content_types["attachment"] == "application/pdf"

    test()


def test_custom_media_type_strategy_in_coverage_phase(testdir, openapi3_base_url):
    # Regression test for GH-3345
    testdir.make_test(
        f"""
schemathesis.openapi.media_type("application/pdf", st.just(b"%PDF-1.4"))
schema.config.update(base_url="{openapi3_base_url}")
schema.config.phases.examples.enabled = False
schema.config.phases.fuzzing.enabled = False

@schema.include(path_regex="upload").parametrize()
def test(case):
    assert case.body == b"%PDF-1.4", f"Expected PDF bytes, got: {{case.body!r}}"
""",
        paths={
            "/upload": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_multipart_encoding_with_custom_strategy_fuzzing_phase(ctx):
    schemathesis.openapi.media_type("application/pdf", st.just(b"%PDF-1.4"))

    spec = ctx.openapi.build_schema(
        {
            "/upload": {
                "post": {
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"file": {"type": "string", "format": "binary"}},
                                    "required": ["file"],
                                },
                                "encoding": {"file": {"contentType": "application/pdf"}},
                            },
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    schema = schemathesis.openapi.from_dict(spec)
    operation = schema["/upload"]["POST"]
    strategy = operation.as_strategy()

    @given(strategy)
    def test(case):
        if case.body is not None and isinstance(case.body, dict):
            if "file" in case.body:
                assert case.body["file"] == b"%PDF-1.4"

    test()


def test_multipart_encoding_with_custom_strategy_parametrize(testdir, openapi3_base_url):
    # Regression test for GH-3345 - positive mode cases should have consistent PDF bytes
    testdir.make_test(
        f"""
schemathesis.openapi.media_type("application/pdf", st.just(b"%PDF-1.4"))
schema.config.update(base_url="{openapi3_base_url}")

@schema.include(path_regex="upload").parametrize()
def test(case):
    # Negative mode generates intentionally invalid data
    if case.meta.generation.mode == GenerationMode.POSITIVE:
        if case.body is not None and isinstance(case.body, dict) and "file" in case.body:
            assert case.body["file"] == b"%PDF-1.4", f"Expected PDF bytes, got: {{case.body['file']!r}}"
""",
        paths={
            "/upload": {
                "post": {
                    "parameters": [{"name": "tag", "in": "query", "required": False, "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"file": {"type": "string", "format": "binary"}},
                                    "required": ["file"],
                                },
                                "encoding": {"file": {"contentType": "application/pdf"}},
                            },
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1)


def test_multipart_encoding_required_body_parameter_coverage(testdir, openapi3_base_url):
    # Regression test for GH-3345 - parameter coverage should still be generated
    testdir.make_test(
        f"""
schemathesis.openapi.media_type("application/pdf", st.just(b"%PDF-1.4"))
schema.config.update(base_url="{openapi3_base_url}")
schema.config.phases.fuzzing.enabled = False

param_values_seen = set()

@schema.include(path_regex="upload").parametrize()
def test(case):
    if case.query:
        param_values_seen.add(str(case.query.get("count")))

def test_parameter_coverage_generated():
    assert len(param_values_seen) > 0, f"No parameter coverage generated. Values seen: {{param_values_seen}}"
""",
        paths={
            "/upload": {
                "post": {
                    "parameters": [
                        {
                            "name": "count",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 1, "maximum": 10},
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"file": {"type": "string", "format": "binary"}},
                                    "required": ["file"],
                                },
                                "encoding": {"file": {"contentType": "application/pdf"}},
                            },
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        schema_name="simple_openapi.yaml",
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=2)
