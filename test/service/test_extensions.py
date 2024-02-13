import uuid
from hypothesis import find
import pytest
from schemathesis.service.extensions import apply
from schemathesis.service.models import extension_from_dict
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


def test_string_formats_success(string_formats):
    apply([string_formats])
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
def test_invalid_regex(format, message):
    extension = extension_from_dict(
        {
            "type": "string_formats",
            "formats": {"_invalid": format},
        }
    )
    apply([extension])
    assert str(extension.state) == "Error"
    assert extension.state.message == message
    assert "_invalid" not in STRING_FORMATS
