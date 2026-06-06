import jsonschema_rs
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from schemathesis.generation.jsonschema.formats import FormatRegistry

BUILTIN = ["ipv4", "ipv6", "date", "date-time", "time", "email", "uri", "uri-reference", "hostname"]


@pytest.mark.parametrize("fmt", BUILTIN)
def test_builtin_format_only_yields_valid_values(fmt):
    strategy = FormatRegistry().get(fmt)
    assert strategy is not None
    validator = jsonschema_rs.Draft7Validator({"type": "string", "format": fmt})

    # High count + derandomize: the guarantee is that the strategy NEVER yields a format-invalid
    # value, so the sweep must be wide and deterministic (random seeds masked a real failure).
    @given(strategy)
    @settings(max_examples=200, derandomize=True)
    def check(value):
        assert isinstance(value, str)
        assert validator.is_valid(value), f"{fmt} produced invalid {value!r}"

    check()


def test_register_overrides_and_adds_formats():
    registry = FormatRegistry()
    registry.register("even-digit", st.sampled_from("02468"))
    assert registry.get("even-digit") is not None


def test_unknown_format_returns_none():
    assert FormatRegistry().get("definitely-not-a-format") is None
