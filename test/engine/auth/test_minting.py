import re

import pytest

from schemathesis.core.parameters import ParameterLocation
from schemathesis.engine.auth.minting import MintingError, mint_credential_value, mint_credentials
from schemathesis.specs.openapi.auth_flow.models import CredentialField, CredentialRole


def _field(name, role, schema):
    return CredentialField(name=name, location=ParameterLocation.BODY, schema=schema, role=role)


def test_mint_identifier_default_unconstrained():
    field = _field("username", CredentialRole.IDENTIFIER, {"type": "string"})
    value = mint_credential_value(field)
    assert isinstance(value, str)
    assert 8 <= len(value) <= 16
    assert re.fullmatch(r"[A-Za-z0-9]+", value)


def test_mint_email_default_unconstrained():
    field = _field("email", CredentialRole.EMAIL, {"type": "string"})
    value = mint_credential_value(field)
    assert "@" in value
    assert value.endswith(".test")


def test_mint_secret_strong_default():
    field = _field("password", CredentialRole.SECRET, {"type": "string"})
    value = mint_credential_value(field)
    assert len(value) == 16
    assert any(c.isupper() for c in value)
    assert any(c.islower() for c in value)
    assert any(c.isdigit() for c in value)
    assert any(c in "!@#$%" for c in value)


def test_mint_respects_minLength():
    field = _field(
        "password",
        CredentialRole.SECRET,
        {"type": "string", "minLength": 32},
    )
    value = mint_credential_value(field)
    assert len(value) >= 32


def test_mint_respects_pattern():
    field = _field(
        "username",
        CredentialRole.IDENTIFIER,
        {"type": "string", "pattern": "^[a-z]+$"},
    )
    value = mint_credential_value(field)
    assert re.fullmatch(r"[a-z]+", value)


def test_mint_unsatisfiable_raises():
    field = _field(
        "password",
        CredentialRole.SECRET,
        {"type": "string", "pattern": "^\\d+$", "maxLength": 0},
    )
    with pytest.raises(MintingError, match="password"):
        mint_credential_value(field)


def test_mint_credentials_assembles_dict():
    fields = [
        _field("username", CredentialRole.IDENTIFIER, {"type": "string"}),
        _field("password", CredentialRole.SECRET, {"type": "string"}),
    ]
    creds = mint_credentials(fields)
    assert set(creds.keys()) == {"username", "password"}


def test_mint_credentials_propagates_minting_error():
    fields = [
        _field("password", CredentialRole.SECRET, {"type": "string", "maxLength": 0}),
    ]
    with pytest.raises(MintingError):
        mint_credentials(fields)
