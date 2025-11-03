from pathlib import Path

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
