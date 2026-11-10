import json

import pytest
import requests
from werkzeug.test import Client

from schemathesis.core.transport import Response, StatusClass, expand_status_code, http_status_class


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (500, [500]),
        ("500", [500]),
        ("50X", list(range(500, 510))),
        ("50x", list(range(500, 510))),
        ("0-", []),
        ("", []),
        ("2XXX", []),
        ("default", []),
    ],
)
def test_expand_status_code(value, expected):
    assert expand_status_code(value) == expected


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (101, StatusClass.OTHER),
        (204, StatusClass.SUCCESS),
        (302, StatusClass.REDIRECT),
        (422, StatusClass.CLIENT_ERROR),
        (429, StatusClass.RATE_LIMITED),
        (500, StatusClass.INTERNAL_ERROR),
        (503, StatusClass.SERVER_ERROR),
        (600, StatusClass.OTHER),
    ],
    ids=["informational", "success", "redirect", "client-error", "rate-limited", "internal", "server-error", "beyond"],
)
def test_http_status_class(status_code, expected):
    assert http_status_class(status_code) is expected


@pytest.mark.parametrize(
    ("headers", "expected"),
    [([("Content-Type", "text/plain; charset=latin-1")], "latin-1"), ([], "utf-8")],
    ids=["declared-charset", "no-charset"],
)
def test_empty_wsgi_response_encoding(headers, expected):
    def app(environ, start_response):
        start_response("204 No Content", headers)
        return []

    response = Response.from_any(Client(app).get("/"))
    assert (response.status_code, response.content, response.encoding) == (204, b"", expected)


def test_wsgi_response_encoding(response_factory):
    raw = response_factory.wsgi(content="café".encode("latin-1"), content_type="text/plain; charset=latin-1")
    response = Response.from_wsgi(raw)
    assert (response.encoding, response.text) == ("latin-1", "café")


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


def test_truncated_copy_drops_values_derived_from_full_body(response_factory):
    response = response_factory.core(content=b'{"a": 1}')
    assert (response.json(), response.encoded_body) == ({"a": 1}, "eyJhIjogMX0=")
    truncated = response.truncated(4)
    with pytest.raises(json.JSONDecodeError):
        truncated.json()
    assert truncated.encoded_body == "eyJhIg=="
