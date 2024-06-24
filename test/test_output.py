import pytest

from schemathesis.internal.output import OutputConfig, prepare_response_payload, truncate_json

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
        truncate_json(SIMPLE_DICT)
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
        truncate_json(SIMPLE_DICT, config=OutputConfig(truncate=False))
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
    "payload, expected",
    (
        ("ABCDEF\r\n", "ABCDEF"),
        ("ABCDEF\n", "ABCDEF"),
    ),
)
def test_prepare_response_payload(payload, expected):
    assert prepare_response_payload(payload) == expected


def test_prepare_response_payload_truncated():
    value = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 30
    assert prepare_response_payload(value).endswith(" // Output truncated...")


def test_prepare_response_payload_no_truncation():
    value = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    assert prepare_response_payload(value, config=OutputConfig(truncate=False)) == value
