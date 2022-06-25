from types import SimpleNamespace

import click
import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from schemathesis import utils
from schemathesis.cli import callbacks
from schemathesis.cli.callbacks import SchemaInputKind

from ..utils import SIMPLE_PATH


@pytest.mark.parametrize("value", ("//test", "//ÿ["))
def test_parse_schema_kind(value):
    with pytest.raises(click.UsageError):
        kind = callbacks.parse_schema_kind(value, app=None)
        callbacks.validate_schema(value, kind, base_url=None, dry_run=False, app=None, api_name=None)


def test_validate_schema_path_without_base_url():
    with pytest.raises(click.UsageError):
        callbacks.validate_schema(
            SIMPLE_PATH, SchemaInputKind.PATH, base_url=None, dry_run=False, app=None, api_name=None
        )


@given(value=st.text().filter(lambda x: x.count(":") != 1))
@example(":")
@example("0:Ā")
@example("Ā:0")
def test_validate_auth(value):
    with pytest.raises(click.BadParameter):
        callbacks.validate_auth(None, None, value)


@given(value=st.text())
def test_validate_app(value):
    with pytest.raises(click.exceptions.Exit):
        callbacks.validate_app(SimpleNamespace(params={"show_errors_tracebacks": False}), None, value)


def is_invalid_header(header):
    try:
        # We need to avoid generating known valid headers
        key, _ = header.split(":", maxsplit=1)
        return not (key.strip() and utils.is_latin_1_encodable(key))
    except ValueError:
        return True


@given(value=st.lists(st.text().filter(is_invalid_header), min_size=1).map(tuple))
@example((":",))
@example(("0:Ā",))
@example(("Ā:0",))
@example((" :test",))
def test_validate_header(value):
    with pytest.raises(click.BadParameter):
        callbacks.validate_headers(None, None, value)


def test_reraise_format_error():
    with pytest.raises(click.BadParameter, match="Should be in KEY:VALUE format. Got: bla"):
        with callbacks.reraise_format_error("bla"):
            raise ValueError


@pytest.mark.parametrize(
    "value",
    ("+", "\\", "[", r"0EEE|[>:>\UEEEEEEEEEEEEEEEEEEEEEEEE>", "(?(8))"),
)
def test_validate_regex(value):
    with pytest.raises(click.BadParameter, match="Invalid regex: "):
        callbacks.validate_regex(None, None, (value,))


@pytest.mark.parametrize(
    "value, expected",
    (
        ("On", True),
        ("F", False),
        ("/tmp/cert.pem", "/tmp/cert.pem"),
    ),
)
def test_convert_request_tls_verify(value, expected):
    assert callbacks.convert_boolean_string(None, None, value) == expected


@pytest.mark.parametrize("value, expected", (("2", 2), ("auto", callbacks.get_workers_count())))
def test_convert_workers(value, expected):
    assert callbacks.convert_workers(None, None, value) == expected
