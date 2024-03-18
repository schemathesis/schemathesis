import uuid

import pytest
from hypothesis import find

from schemathesis.service.extensions import apply, strategy_from_definitions
from schemathesis.service.models import StrategyDefinition, UnknownExtension, extension_from_dict
from schemathesis.specs.openapi.formats import STRING_FORMATS, unregister_string_format
from schemathesis.specs.openapi._hypothesis import Binary


@pytest.fixture
def string_formats():
    extension = extension_from_dict(
        {
            "type": "string_formats",
            "items": {
                # Simpler regex for faster search
                "_payment_card_regex": [{"name": "from_regex", "arguments": {"regex": "^[0-9]{2}$"}}],
                "_payment_card_regex_with_samples": [
                    {"name": "from_regex", "arguments": {"regex": "^[0-9]{2}$"}},
                    {"name": "sampled_from", "arguments": {"elements": ["1234-5678-1234-5678"]}},
                ],
                "_payment_card_samples": [
                    {"name": "sampled_from", "arguments": {"elements": ["1234-5678-1234-5679"]}},
                ],
                "_uuid": [{"name": "uuids", "transforms": [{"kind": "map", "name": "str"}]}],
            },
        }
    )
    yield extension
    for format in extension.formats:
        unregister_string_format(format)


def is_uuid(value):
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def test_string_formats_success(string_formats, openapi_30):
    assert str(string_formats.state) == "Not Applied"
    apply([string_formats], openapi_30)
    find(STRING_FORMATS["_payment_card_regex"], "42".__eq__)
    find(STRING_FORMATS["_payment_card_regex_with_samples"], "42".__eq__)
    find(STRING_FORMATS["_payment_card_regex_with_samples"], "1234-5678-1234-5678".__eq__)
    find(STRING_FORMATS["_payment_card_samples"], "1234-5678-1234-5679".__eq__)
    find(STRING_FORMATS["_uuid"], is_uuid)
    assert str(string_formats.state) == "Success"


@pytest.mark.parametrize(
    "definition, expected_type",
    (
        ([{"name": "uuids", "transforms": [{"kind": "map", "name": "str"}]}], str),
        ([{"name": "ip_addresses", "transforms": [{"kind": "map", "name": "str"}]}], str),
        ([{"name": "ip_addresses", "transforms": [{"kind": "map", "name": "str"}], "arguments": {"v": 6}}], str),
        ([{"name": "binary", "transforms": [{"kind": "map", "name": "base64_encode"}]}], Binary),
        ([{"name": "binary", "transforms": [{"kind": "map", "name": "urlsafe_base64_encode"}]}], Binary),
        (
            [
                {
                    "name": "integers",
                    "transforms": [{"kind": "map", "name": "str"}],
                    "arguments": {"min_value": 1, "max_value": 65535},
                }
            ],
            str,
        ),
        (
            [
                {
                    "name": "dates",
                    "transforms": [{"kind": "map", "name": "strftime", "arguments": {"format": "%Y-%m-%d"}}],
                }
            ],
            str,
        ),
        ([{"name": "timezone_keys"}], str),
        (
            [
                {
                    "name": "from_type",
                    "arguments": {"thing": "IPv4Network"},
                    "transforms": [{"kind": "map", "name": "str"}],
                }
            ],
            str,
        ),
        (
            [
                {"name": "timezone_keys"},
                {
                    "name": "dates",
                    "transforms": [{"kind": "map", "name": "strftime", "arguments": {"format": "%Y-%m-%d"}}],
                },
            ],
            str,
        ),
        ([{"name": "timezone_keys"}], str),
        ([{"name": "timezone_keys"}], str),
    ),
)
def test_strategy_from_definition(definition, expected_type):
    strategy = strategy_from_definitions([StrategyDefinition(**item) for item in definition])
    find(strategy.ok(), lambda x: isinstance(x, expected_type))


@pytest.mark.parametrize(
    "strategies, errors",
    (
        ([{"name": "from_regex", "arguments": {"regex": "[a-z"}}], ["Invalid regex: `[a-z`"]),
        ([{"name": "wrong"}], ["Unknown built-in strategy: `wrong`"]),
        (
            [{"name": "sampled_from", "arguments": {"elements": []}}],
            ["Invalid input for `sampled_from`: Cannot sample from a length-zero sequence"],
        ),
        (
            [
                {"name": "from_regex", "arguments": {"regex": r"\d"}},
                {"name": "sampled_from", "arguments": {"elements": []}},
            ],
            ["Invalid input for `sampled_from`: Cannot sample from a length-zero sequence"],
        ),
        ([{"unknown": 42}], ["Unsupported string format extension"]),
    ),
)
def test_invalid_string_format_extension(strategies, errors, openapi_30):
    extension = extension_from_dict({"type": "string_formats", "items": {"invalid": strategies}})
    apply([extension], openapi_30)
    assert str(extension.state) == "Error"
    assert extension.state.errors == errors
    assert "_invalid" not in STRING_FORMATS


def test_unknown_extension(openapi_30):
    extension = extension_from_dict({"type": "unknown", "custom": 42})
    assert isinstance(extension, UnknownExtension)
    apply([extension], openapi_30)
    assert str(extension.state) == "Not Applied"