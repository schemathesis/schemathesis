import pytest

from schemathesis.specs.openapi.auth_flow.vocabulary import is_credential, is_secret, normalize


@pytest.mark.parametrize(
    ("name", "credential", "secret"),
    [
        ("username", True, False),
        ("user_name", True, False),
        ("USER", True, False),
        ("phoneNumber", True, False),
        ("account_name", True, False),
        ("email", True, False),
        ("e_mail", True, False),
        ("password", True, True),
        ("passwd", True, True),
        ("passphrase", True, True),
        ("foo", False, False),
        ("address", False, False),
    ],
)
def test_credential_names(name, credential, secret):
    assert (is_credential(name), is_secret(name)) == (credential, secret)


def test_normalize_strips_separators_and_lowercases():
    assert [normalize(name) for name in ("user_Name", "E-Mail", "AccountName")] == ["username", "email", "accountname"]
