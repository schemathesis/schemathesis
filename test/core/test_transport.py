import pytest
import requests

from schemathesis.core.transport import Response, expand_status_code


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (500, [500]),
        ("500", [500]),
        ("50X", list(range(500, 510))),
        ("50x", list(range(500, 510))),
    ],
)
def test_expand_status_code(value, expected):
    assert expand_status_code(value) == expected


@pytest.mark.parametrize(
    ("content", "charset", "expected"),
    [
        (b"hello world", "bogus-xyz", "hello world"),
        (b"hello world", "undefined", "hello world"),
        (b"hello world", "ab\x00cd", "hello world"),
        (b"\xff\xfe", "utf-8", "��"),
    ],
    ids=["unknown-charset", "undefined-codec", "nul-in-charset", "undecodable-bytes"],
)
def test_text_lossy_never_raises(response_factory, content, charset, expected):
    raw = response_factory.requests(content=content, content_type=f"text/plain; charset={charset}")
    # Derive `encoding` from headers exactly like requests' adapter does for real responses.
    raw.encoding = requests.utils.get_encoding_from_headers(raw.headers)
    response = Response.from_requests(raw, verify=False)
    assert response.encoding == charset
    assert response.text_lossy() == expected
