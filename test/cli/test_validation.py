import click
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from schemathesis.cli.commands.run import validation
from schemathesis.core.validation import is_latin_1_encodable


@pytest.mark.parametrize("value", ["//test", "//ÿ["])
def test_parse_schema_kind(value):
    with pytest.raises(click.UsageError):
        validation.validate_schema_location(None, None, value)


@given(value=st.text().filter(lambda x: x.count(":") != 1))
@example(":")
@example("0:Ā")
@example("Ā:0")
@settings(deadline=None)
def test_validate_auth(value):
    with pytest.raises(click.BadParameter):
        validation.validate_auth(None, None, value)


def is_invalid_header(header):
    try:
        # We need to avoid generating known valid headers
        key, _ = header.split(":", maxsplit=1)
        return not (key.strip() and is_latin_1_encodable(key))
    except ValueError:
        return True


@given(value=st.lists(st.text().filter(is_invalid_header), min_size=1).map(tuple))
@example((":",))
@example(("0:Ā",))
@example(("Ā:0",))
@example((" :test",))
@settings(deadline=None)
def test_validate_header(value):
    with pytest.raises(click.BadParameter):
        validation.validate_headers(None, None, value)


def test_reraise_format_error():
    with pytest.raises(click.BadParameter, match="Expected KEY:VALUE format, received bla."):
        with validation.reraise_format_error("bla"):
            raise ValueError


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("On", True),
        ("F", False),
        ("/tmp/cert.pem", "/tmp/cert.pem"),
    ],
)
def test_convert_request_tls_verify(value, expected):
    assert validation.convert_boolean_string(None, None, value) == expected


@pytest.mark.parametrize(("value", "expected"), [("2", 2), ("auto", validation.get_workers_count())])
def test_convert_workers(value, expected):
    assert validation.convert_workers(None, None, value) == expected


@pytest.mark.parametrize("value", ["1", "1/g", "f/g"])
def test_validate_rate_limit_invalid(value):
    with pytest.raises(click.UsageError) as exc:
        validation.validate_rate_limit(None, None, value)
    assert (
        str(exc.value) == f"Invalid rate limit value: `{value}`. Should be in form `limit/interval`. "
        "Example: `10/m` for 10 requests per minute."
    )


def test_validate_rate_limit_valid():
    assert validation.validate_rate_limit(None, None, "10/m") == "10/m"
