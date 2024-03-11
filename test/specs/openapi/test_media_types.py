from pathlib import Path

from hypothesis import strategies as st, given


import schemathesis
import pytest

HERE = Path(__file__).absolute().parent


@pytest.fixture(autouse=True)
def cleanup():
    yield
    schemathesis.openapi.media_types.unregister_all()
    assert MEDIA_TYPE not in schemathesis.openapi.media_types.MEDIA_TYPES
    assert MEDIA_TYPE not in schemathesis.serializers.SERIALIZERS


SAMPLE_PDF = (HERE / "blank.pdf").read_bytes()
PDFS = st.sampled_from([SAMPLE_PDF])
MEDIA_TYPE = "application/pdf"
ALIAS = "application/x-pdf"


def test_pdf_generation(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
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
    schemathesis.openapi.media_type(MEDIA_TYPE, PDFS, aliases=[ALIAS])
    schema = schemathesis.from_dict(empty_open_api_3_schema)

    strategy = schema["/pdf"]["post"].as_strategy()

    @given(strategy)
    def test(case):
        assert case.body == SAMPLE_PDF
        assert case.media_type in (MEDIA_TYPE, ALIAS)
        assert case.as_requests_kwargs()["data"] == SAMPLE_PDF
        assert case.as_werkzeug_kwargs()["data"] == SAMPLE_PDF

    test()
