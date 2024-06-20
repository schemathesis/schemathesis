import pytest

from schemathesis.exceptions import prepare_response_payload, truncated_json


def test_truncate_simple_dict():
    simple_dict = {"name": "John", "age": 30, "city": "New York"}
    assert (
        truncated_json(simple_dict, max_lines=3, max_width=17)
        == """{
    "name": "J...
    // Output truncated...
}"""
    )


@pytest.mark.parametrize(
    "payload, expected",
    (
        ("ABCDEF\r\n", "ABCDEF"),
        ("ABCDEF\n", "ABCDEF"),
        ("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "ABCDEFGHIJ // Output truncated..."),
    ),
)
def test_prepare_response_payload(payload, expected):
    assert prepare_response_payload(payload, max_size=10) == expected
