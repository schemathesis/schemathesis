from types import SimpleNamespace

import click
import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from schemathesis.cli import callbacks

from ..utils import SIMPLE_PATH


@given(value=st.text().filter(lambda x: "//" not in x))
@example("0" * 1000)
def test_validate_schema(value):
    with pytest.raises(click.UsageError):
        callbacks.validate_schema(SimpleNamespace(params={}), None, value)


def test_validate_schema_path_without_base_url():
    with pytest.raises(click.UsageError):
        callbacks.validate_schema(SimpleNamespace(params={}), None, SIMPLE_PATH)


@given(value=st.text().filter(lambda x: x.count(":") != 1))
@example(":")
def test_validate_auth(value):
    with pytest.raises(click.BadParameter):
        callbacks.validate_auth(None, None, value)


@given(value=st.text())
def test_validate_app(value):
    with pytest.raises(click.BadParameter):
        callbacks.validate_app(None, None, value)


@given(value=st.lists(st.text(), min_size=1).map(tuple))
@example((":",))
def test_validate_header(value):
    with pytest.raises(click.BadParameter):
        callbacks.validate_headers(None, None, value)


def test_reraise_format_error():
    with pytest.raises(click.BadParameter, match="Should be in KEY:VALUE format. Got: bla"):
        with callbacks.reraise_format_error("bla"):
            raise ValueError
