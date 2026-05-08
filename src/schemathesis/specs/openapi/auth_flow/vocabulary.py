from __future__ import annotations

from schemathesis.specs.openapi.auth_flow.models import CredentialRole

_IDENTIFIER = frozenset(
    {
        "username",
        "user",
        "login",
        "account",
        "accountname",
        "phone",
        "phonenumber",
    }
)
_SECRET = frozenset({"password", "passwd", "pwd", "pass", "secret", "passphrase", "passcode"})
_EMAIL = frozenset({"email", "mail", "emailaddress"})


def normalize(name: str) -> str:
    """Lowercase and strip `_` and `-` separators."""
    return name.lower().replace("_", "").replace("-", "")


def classify(name: str) -> CredentialRole | None:
    """Return the `CredentialRole` for a property name, or `None` when unrecognised."""
    norm = normalize(name)
    if norm in _SECRET:
        return CredentialRole.SECRET
    if norm in _EMAIL:
        return CredentialRole.EMAIL
    if norm in _IDENTIFIER:
        return CredentialRole.IDENTIFIER
    return None
