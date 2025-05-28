from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

import schemathesis
from schemathesis.transport.requests import REQUESTS_TRANSPORT
from schemathesis.transport.wsgi import WSGI_TRANSPORT

HERE = Path(__file__).absolute().parent


@pytest.fixture(autouse=True)
def cleanup():
    yield
    schemathesis.specs.openapi.media_types.unregister_all()
    assert MEDIA_TYPE not in schemathesis.specs.openapi.media_types.MEDIA_TYPES


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
