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
