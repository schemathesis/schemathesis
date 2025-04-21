import pytest

from schemathesis.config import OutputConfig, TruncationConfig
from schemathesis.core.output import prepare_response_payload, truncate_json

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
    value = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 30
    assert prepare_response_payload(value, config=OutputConfig()).endswith(" // Output truncated...")


def test_prepare_response_payload_no_truncation():
    value = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    assert prepare_response_payload(value, config=OutputConfig(truncation=TruncationConfig(enabled=False))) == value
