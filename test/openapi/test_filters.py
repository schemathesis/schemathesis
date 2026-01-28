import pytest
import requests
from hypothesis import given, settings
from hypothesis import strategies as st

from schemathesis.core import NOT_SET
from schemathesis.openapi.generation.filters import is_valid_path, is_valid_query, is_valid_urlencoded


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"key": "1"}, True),
        ({"key": 1}, True),
        ({"key": "\udcff"}, False),
        ({"key": ["1", "abc", "\udcff"]}, False),
    ],
)
def test_is_valid_query(value, expected):
    assert is_valid_query(value) == expected


@pytest.mark.hypothesis_nested
def test_is_valid_query_strategy():
    strategy = st.sampled_from([{"key": "1"}, {"key": "\udcff"}]).filter(is_valid_query)

    @given(strategy)
    @settings(max_examples=10)
    def test(value):
        assert value == {"key": "1"}

    test()


@pytest.mark.parametrize(
    "valid_params",
    [
        {"key": "1"},
        {"key": 1},
        {"a": "b", "c": "d"},
    ],
    ids=["string-value", "int-value", "multiple-params"],
)
def test_valid_query_can_be_sent_by_requests(valid_params):
    assert is_valid_query(valid_params)
    req = requests.Request("GET", "http://example.com", params=valid_params)
    prepared = req.prepare()
    assert "?" in prepared.url


@pytest.mark.parametrize(
    "invalid_params",
    [
        {"key": "\udcff"},
        {"\udcff": "value"},
    ],
    ids=["surrogate-in-value", "surrogate-in-key"],
)
def test_invalid_query_fails_with_requests(invalid_params):
    assert not is_valid_query(invalid_params)
    req = requests.Request("GET", "http://example.com", params=invalid_params)
    with pytest.raises(UnicodeEncodeError):
        req.prepare()


@pytest.mark.parametrize("value", ["/", "\udc9b"])
def test_filter_path_parameters(value):
    assert not is_valid_path({"foo": value})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Valid cases - can be sent via requests
        ({"key": "value"}, True),
        ({"a": 1, "b": "2"}, True),
        ({}, True),
        ([("key", "value")], True),
        ([("a", "1"), ("b", "2")], True),
        (NOT_SET, True),
        # Invalid cases - cannot be URL-encoded by requests
        ([1, 2, 3], False),
        ([("a",)], False),
        ([("a", "b", "c")], False),
        (None, False),
    ],
    ids=[
        "dict",
        "dict-mixed-values",
        "empty-dict",
        "list-of-tuples",
        "list-of-multiple-tuples",
        "not-set",
        "list-of-ints",
        "list-of-1-tuples",
        "list-of-3-tuples",
        "none",
    ],
)
def test_is_valid_urlencoded(value, expected):
    assert is_valid_urlencoded(value) == expected


@pytest.mark.parametrize(
    "valid_data",
    [
        {"key": "value"},
        {"a": "1", "b": "2"},
        [("key", "value")],
        [("a", "1"), ("b", "2")],
    ],
    ids=["dict", "dict-multiple", "list-tuples", "list-tuples-multiple"],
)
def test_valid_urlencoded_can_be_sent_by_requests(valid_data):
    assert is_valid_urlencoded(valid_data)
    req = requests.Request("POST", "http://example.com", data=valid_data)
    prepared = req.prepare()
    assert prepared.body is not None


@pytest.mark.parametrize(
    "invalid_data",
    [
        [1, 2, 3],
        [("a",)],
        [("a", "b", "c")],
    ],
    ids=["list-of-ints", "1-tuple", "3-tuple"],
)
def test_invalid_urlencoded_fails_with_requests(invalid_data):
    assert not is_valid_urlencoded(invalid_data)
    req = requests.Request("POST", "http://example.com", data=invalid_data)
    with pytest.raises((TypeError, ValueError)):
        req.prepare()
