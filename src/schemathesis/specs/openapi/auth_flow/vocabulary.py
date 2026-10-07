from __future__ import annotations

_SECRET = frozenset({"password", "passwd", "pwd", "pass", "secret", "passphrase", "passcode"})
_IDENTIFIER = frozenset(
    {
        "username",
        "user",
        "login",
        "account",
        "accountname",
        "phone",
        "phonenumber",
        "email",
        "mail",
        "emailaddress",
    }
)


def normalize(name: str) -> str:
    """Lowercase and strip `_` and `-` separators."""
    return name.lower().replace("_", "").replace("-", "")


def is_secret(name: str) -> bool:
    return normalize(name) in _SECRET


def is_credential(name: str) -> bool:
    normalized = normalize(name)
    return normalized in _SECRET or normalized in _IDENTIFIER
