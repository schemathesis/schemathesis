import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from schemathesis.openapi.generation.filters import is_valid_path, is_valid_query


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
    # TODO: A better test would be to try to send values over network
    strategy = st.sampled_from([{"key": "1"}, {"key": "\udcff"}]).filter(is_valid_query)

    @given(strategy)
    @settings(max_examples=10)
    def test(value):
        assert value == {"key": "1"}

    test()


@pytest.mark.parametrize("value", ["/", "\udc9b"])
def test_filter_path_parameters(value):
    assert not is_valid_path({"foo": value})
