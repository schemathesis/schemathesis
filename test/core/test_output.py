import string

import pytest
import requests

from schemathesis.config import OutputConfig, TruncationConfig
from schemathesis.core.output import decode_response_text, prepare_response_payload, truncate_json
from schemathesis.core.transport import Response

SIMPLE_DICT = {
    "name": "John",
    "age": 30,
    "city": "New York",
    "state": "NY",
    "country": "USA",
    "email": "very-long-long-long-long-long-long-email@very-long-long-long-long-long.com",
    "phone": "1234567890",
    "website": "https://www.example.com",
    "company": "Example Inc",
    "address": "123 Example St",
    "zip": "12345",
    "is_active": True,
}


def test_truncate_simple_dict():
    assert (
        truncate_json(SIMPLE_DICT, config=OutputConfig())
        == """{
    "name": "John",
    "age": 30,
    "city": "New York",
    "state": "NY",
    "country": "USA",
    "email": "very-long-long-long-long-long-long-email@very-long-long-long-lo...
    "phone": "1234567890",
    "website": "https://www.example.com",
    // Output truncated...
}"""
    )


def test_no_dict_truncation():
    assert (
        truncate_json(SIMPLE_DICT, config=OutputConfig(truncation=TruncationConfig(enabled=False)))
        == """{
    "name": "John",
    "age": 30,
    "city": "New York",
    "state": "NY",
    "country": "USA",
    "email": "very-long-long-long-long-long-long-email@very-long-long-long-long-long.com",
    "phone": "1234567890",
    "website": "https://www.example.com",
    "company": "Example Inc",
    "address": "123 Example St",
    "zip": "12345",
    "is_active": true
}"""
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("ABCDEF\r\n", "ABCDEF"),
        ("ABCDEF\n", "ABCDEF"),
    ],
)
def test_prepare_response_payload(payload, expected):
    assert prepare_response_payload(payload, config=OutputConfig()) == expected


def test_prepare_response_payload_truncated():
    value = string.ascii_uppercase * 30
    assert prepare_response_payload(value, config=OutputConfig()).endswith(" // Output truncated...")


def test_prepare_response_payload_no_truncation():
    value = string.ascii_uppercase
    assert prepare_response_payload(value, config=OutputConfig(truncation=TruncationConfig(enabled=False))) == value


@pytest.mark.parametrize(
    ("content", "charset", "expected"),
    [
        (b"boom", "bogus-xyz", "boom"),
        (b"boom", "undefined", "boom"),
        (b"boom", "ab\x00cd", "boom"),
        (b"\xff\xfe", "utf-8", None),
    ],
    ids=["unknown-charset", "undefined-codec", "nul-in-charset", "binary-body"],
)
def test_decode_response_text(response_factory, content, charset, expected):
    raw = response_factory.requests(content=content, content_type=f"text/plain; charset={charset}")
    raw.encoding = requests.utils.get_encoding_from_headers(raw.headers)
    assert decode_response_text(Response.from_requests(raw, verify=False)) == expected
