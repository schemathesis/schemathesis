import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from schemathesis.loaders import load_app


@pytest.mark.parametrize(
    "path, exception, match",
    (
        ("", ValueError, "Empty module name"),
        ("foo", ImportError, "No module named 'foo'"),
        ("schemathesis:foo", AttributeError, "module 'schemathesis' has no attribute 'foo'"),
    ),
)
def test_load_app(path, exception, match):
    with pytest.raises(exception, match=match):
        load_app(path)


@given(st.text())
@settings(deadline=None)
def test_load_app_no_unexpected_exceptions(path):
    # `load_app` should not raise anything else but `ImportError` or `AttributeError` or `ValueError`
    with pytest.raises((ImportError, AttributeError, ValueError)):
        load_app(path)
