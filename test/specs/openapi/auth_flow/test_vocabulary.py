import pytest

from schemathesis.specs.openapi.auth_flow.models import CredentialRole
from schemathesis.specs.openapi.auth_flow.vocabulary import classify, normalize


@pytest.mark.parametrize(
    "name, expected",
    [
        ("username", CredentialRole.IDENTIFIER),
        ("user_name", CredentialRole.IDENTIFIER),
        ("userName", CredentialRole.IDENTIFIER),
        ("USER", CredentialRole.IDENTIFIER),
        ("login", CredentialRole.IDENTIFIER),
        ("phone", CredentialRole.IDENTIFIER),
        ("phoneNumber", CredentialRole.IDENTIFIER),
        ("account_name", CredentialRole.IDENTIFIER),
        ("password", CredentialRole.SECRET),
        ("passwd", CredentialRole.SECRET),
        ("pwd", CredentialRole.SECRET),
        ("pass", CredentialRole.SECRET),
        ("passcode", CredentialRole.SECRET),
        ("secret", CredentialRole.SECRET),
        ("passphrase", CredentialRole.SECRET),
        ("email", CredentialRole.EMAIL),
        ("emailAddress", CredentialRole.EMAIL),
        ("e_mail", CredentialRole.EMAIL),
        ("foo", None),
        ("body", None),
        ("address", None),
    ],
)
def test_classify(name, expected):
    assert classify(name) == expected


def test_normalize_strips_separators_and_lowercases():
    assert normalize("user_Name") == "username"
    assert normalize("E-Mail") == "email"
    assert normalize("AccountName") == "accountname"
