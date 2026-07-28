from base64 import b64decode

import jsonschema_rs
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from schemathesis.config import GenerationConfig
from schemathesis.core.validation import check_header_name
from schemathesis.generation.modes import GenerationMode
from schemathesis.specs.openapi._hypothesis import _build_custom_formats, _canonical_strategy_or_none
from schemathesis.specs.openapi.coverage._schema import is_valid_header_value
from schemathesis.specs.openapi.formats import register_string_format
from schemathesis.transport.serialization import Binary

FORMATS = _build_custom_formats(GenerationConfig(), GenerationMode.POSITIVE)
# Formats named by JSON Schema; the `_`-prefixed rest are internal and carry their own invariants.
PUBLIC_FORMATS = sorted(name for name in FORMATS if not name.startswith("_"))
DRAFTS = {
    "draft4": jsonschema_rs.Draft4Validator,
    "draft7": jsonschema_rs.Draft7Validator,
    "2020-12": jsonschema_rs.Draft202012Validator,
}
SETTINGS = settings(max_examples=50, deadline=None, database=None, suppress_health_check=list(HealthCheck))


# The character set generation runs with by default; a codec-free one admits lone surrogates,
# which no JSON document can carry.
ALPHABET = st.characters(codec="utf-8")


def _resolve(name):
    strategy = FORMATS[name]
    if not isinstance(strategy, st.SearchStrategy):
        strategy = strategy(ALPHABET)
    return strategy


def _asserted():
    """Every (format, draft) pair where the draft checks the format rather than annotating it."""
    for name in PUBLIC_FORMATS:
        for label, validator_cls in DRAFTS.items():
            validator = validator_cls({"type": "string", "format": name}, validate_formats=True)
            if not validator.is_valid("!! not a valid anything !!"):
                yield pytest.param(name, validator, id=f"{name}-{label}")


@pytest.mark.parametrize(("name", "validator"), list(_asserted()))
def test_registered_format_satisfies_its_own_validator(name, validator):
    # A generator whose values its own validator rejects turns every use of that format into a
    # false positive.
    @given(_resolve(name))
    @SETTINGS
    def test(value):
        assert validator.is_valid(value), value

    test()


@pytest.mark.parametrize("name", PUBLIC_FORMATS)
def test_registered_format_generates_strings(name):
    @given(_resolve(name))
    @SETTINGS
    def test(value):
        assert isinstance(value, str), value

    test()


@given(st.data())
@SETTINGS
def test_byte_format_is_base64(data):
    b64decode(data.draw(FORMATS["byte"]), validate=True)


@given(st.data())
@SETTINGS
def test_binary_format_carries_bytes(data):
    assert isinstance(data.draw(FORMATS["binary"]), Binary)


@pytest.mark.parametrize("name", ["_header_value", "_basic_auth", "_bearer_auth", "_if_match_header"])
def test_header_formats_are_transmittable(name):
    @given(FORMATS[name])
    @SETTINGS
    def test(value):
        assert is_valid_header_value(value), value

    test()


@given(FORMATS["_header_name"])
@SETTINGS
def test_header_name_format_is_a_token(value):
    check_header_name(value)


def test_registering_a_format_invalidates_built_strategies():
    # Strategies are cached across operations, and the cache key cannot see the format registry.
    schema = {"type": "string", "format": "test-digits"}
    assert _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator) is not None

    register_string_format("test-digits", st.text(alphabet="0123456789", min_size=1))
    built = _canonical_strategy_or_none(schema, GenerationConfig(), jsonschema_rs.Draft202012Validator)

    @given(built)
    @SETTINGS
    def test(value):
        assert value.isdigit(), value

    test()


def test_regex_format_respects_generation_alphabet():
    built = _canonical_strategy_or_none(
        {"type": "string", "format": "regex"},
        GenerationConfig(allow_x00=False, codec="ascii"),
        jsonschema_rs.Draft202012Validator,
    )

    @given(built)
    @SETTINGS
    def test(value):
        assert "\x00" not in value
        value.encode("ascii")

    test()


def test_canonical_strategy_cache_respects_header_exclusions():
    schema = {
        "type": "object",
        "properties": {"X-Foo": {"type": "string", "format": "_header_value", "minLength": 1, "maxLength": 1}},
        "required": ["X-Foo"],
        "additionalProperties": False,
    }

    def generation_config(allowed: str) -> GenerationConfig:
        excluded = "".join(chr(codepoint) for codepoint in range(128) if chr(codepoint) != allowed)
        return GenerationConfig(codec="ascii", exclude_header_characters=excluded)

    first_config = generation_config("A")
    second_config = generation_config("B")
    _canonical_strategy_or_none(schema, first_config, jsonschema_rs.Draft4Validator)
    built = _canonical_strategy_or_none(schema, second_config, jsonschema_rs.Draft4Validator)

    @given(built)
    @SETTINGS
    def test(value):
        assert value["X-Foo"] == "B"

    test()
