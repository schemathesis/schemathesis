import uuid
from hypothesis import find
import pytest
from schemathesis.service.extensions import apply
from schemathesis.service.models import extension_from_dict, UnknownExtension
from schemathesis.specs.openapi.formats import unregister_string_format, STRING_FORMATS


@pytest.fixture
def string_formats():
    extension = extension_from_dict(
        {
            "type": "string_formats",
            "formats": {
                # Simpler regex for faster search
                "_payment_card_regex": {"regex": "^[0-9]{2}$"},
                "_payment_card_regex_with_samples": {
                    "regex": "^[0-9]{2}$",
                    "samples": ["1234-5678-1234-5678"],
                },
                "_payment_card_samples": {"samples": ["1234-5678-1234-5679"]},
                "_uuid": {"builtin": "uuids"},
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
    "format, message",
    (
        ({"regex": "[a-z"}, "Invalid regex: `[a-z`"),
        ({"builtin": "wrong"}, "Unknown builtin strategy: `wrong`"),
        ({"samples": []}, "Cannot sample from a length-zero sequence"),
        ({"regex": r"\d", "samples": []}, "Cannot sample from a length-zero sequence"),
        ({"unknown": 42}, "Unsupported string format extension"),
    ),
)
def test_invalid_regex(format, message, openapi_30):
    extension = extension_from_dict(
        {
            "type": "string_formats",
            "formats": {"_invalid": format},
        }
    )
    apply([extension], openapi_30)
    assert str(extension.state) == "Error"
    assert extension.state.message == message
    assert "_invalid" not in STRING_FORMATS


def test_unknown_extension(openapi_30):
    extension = extension_from_dict({"type": "unknown", "custom": 42})
    assert isinstance(extension, UnknownExtension)
    apply([extension], openapi_30)
    assert str(extension.state) == "Not Applied"


def test_schema_extension(openapi_30):
    custom_schema = {"custom": 42}
    extension = extension_from_dict({"type": "schema", "schema": custom_schema})
    apply([extension], openapi_30)
    assert str(extension.state) == "Success"
    assert openapi_30.raw_schema == custom_schema
