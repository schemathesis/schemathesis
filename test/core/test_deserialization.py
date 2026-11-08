import pytest
from requests import Request

from schemathesis.core.deserialization import _deserialize_sse, _parse_sse_events, deserialize_yaml
from schemathesis.core.transport import Response as TransportResponse


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("'1': foo", {"1": "foo"}),
        ("1: foo", {"1": "foo"}),
        ("1: 1", {"1": 1}),
        ("on: off", {"on": "off"}),
        ("yes: no", {"yes": "no"}),
        ("true: false", {"true": False}),
    ],
    ids=[
        "string-key-string-value",
        "int-key-string-value",
        "int-key-int-value",
        "yaml-1.1-bool-key-and-value",
        "yaml-1.1-bool-word-key-and-value",
        "bool-key-bool-value",
    ],
)
def test_deserialize_yaml(value, expected):
    assert deserialize_yaml(value) == expected


@pytest.mark.parametrize(
    "content, expected",
    [
        pytest.param(
            b'event: update\ndata: {"value": 42}\nid: 1\n\nevent: ping\ndata: {}\n\n',
            [
                {"event": "update", "data": '{"value": 42}', "id": "1"},
                {"event": "ping", "data": "{}", "id": "1"},
            ],
            id="multiple-events",
        ),
        pytest.param(
            b'data: {"a":\ndata:  1}\n\n',
            [{"event": "message", "data": '{"a":\n 1}'}],
            id="multiline-data",
        ),
        pytest.param(
            b': this is a comment\ndata: {"x": 1}\n\n',
            [{"event": "message", "data": '{"x": 1}'}],
            id="comments-skipped",
        ),
        pytest.param(
            b"data: hello world\n\n",
            [{"event": "message", "data": "hello world"}],
            id="plain-text-data",
        ),
        pytest.param(
            b"retry: 3000\ndata: {}\n\n",
            [{"event": "message", "retry": 3000, "data": "{}"}],
            id="retry-field",
        ),
        pytest.param(
            b"retry: 0\ndata: {}\n\n",
            [{"event": "message", "retry": 0, "data": "{}"}],
            id="retry-zero",
        ),
        pytest.param(
            b"retry: not-a-number\ndata: {}\n\n",
            [{"event": "message", "data": "{}"}],
            id="invalid-retry-field-ignored",
        ),
        pytest.param(
            b'id: 1\n\ndata: {"x": 1}\n\n',
            [{"event": "message", "id": "1", "data": '{"x": 1}'}],
            id="last-event-id-carry-over",
        ),
        pytest.param(
            b"event:\ndata: hello\n\n",
            [{"event": "message", "data": "hello"}],
            id="empty-event-field-defaults-to-message",
        ),
        pytest.param(
            b"id: 1\nretry: 3000\n\n",
            [],
            id="metadata-only-block",
        ),
        pytest.param(
            b'data: {"x": 1}',
            [{"event": "message", "data": '{"x": 1}'}],
            id="no-trailing-blank-line",
        ),
        pytest.param(
            b'event: update\r\ndata: {"v": 1}\r\n\r\n',
            [{"event": "update", "data": '{"v": 1}'}],
            id="crlf-line-endings",
        ),
        pytest.param(
            b'\xef\xbb\xbfdata: {"x": 1}\n\n',
            [{"event": "message", "data": '{"x": 1}'}],
            id="utf8-bom",
        ),
        pytest.param(
            b'data: {"v": 2}\r\r',
            [{"event": "message", "data": '{"v": 2}'}],
            id="cr-line-endings",
        ),
        pytest.param(
            b"data\n\n",
            [{"event": "message", "data": ""}],
            id="bare-field-name",
        ),
        pytest.param(
            b"data:  two spaces\n\n",
            [{"event": "message", "data": " two spaces"}],
            id="single-leading-space-stripped",
        ),
        pytest.param(
            b"id: bad\x00id\ndata: hello\n\n",
            [{"event": "message", "data": "hello"}],
            id="id-with-null-char-ignored",
        ),
        pytest.param(
            b"id: first\n\nid: bad\x00id\ndata: hello\n\n",
            [{"event": "message", "data": "hello", "id": "first"}],
            id="id-null-char-falls-back-to-last-event-id",
        ),
        pytest.param(
            "data: a\x0bb\x0cc\x1cd\x1de\x1ef\x85g\u2028h\u2029i\n\n".encode(),
            [{"event": "message", "data": "a\x0bb\x0cc\x1cd\x1de\x1ef\x85g\u2028h\u2029i"}],
            id="non-sse-line-separators-kept-in-data",
        ),
        pytest.param(
            b"",
            [],
            id="empty-stream",
        ),
    ],
)
def test_parse_sse_events(content, expected):
    assert _parse_sse_events(content) == expected


def test_deserialize_sse_uses_utf8_ignoring_response_encoding():
    response = TransportResponse(
        status_code=200,
        headers={"content-type": ["text/event-stream"]},
        content="data: café\n\n".encode(),
        request=Request("GET", "http://example.com/sse").prepare(),
        elapsed=0.0,
        verify=True,
        encoding="iso-8859-1",
    )

    assert _deserialize_sse(None, response) == [{"event": "message", "data": "café"}]
