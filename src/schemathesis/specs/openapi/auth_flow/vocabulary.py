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
    }
)
_EMAIL = frozenset({"email", "mail", "emailaddress"})
_PRIVILEGED = frozenset({"admin", "administrator", "superadmin", "superuser", "root", "owner"})


def normalize(name: str) -> str:
    """Lowercase and strip `_` and `-` separators."""
    return name.lower().replace("_", "").replace("-", "")


def is_secret(name: str) -> bool:
    return normalize(name) in _SECRET


def is_email(name: str) -> bool:
    return normalize(name) in _EMAIL


def is_credential(name: str) -> bool:
    normalized = normalize(name)
    return normalized in _SECRET or normalized in _IDENTIFIER or normalized in _EMAIL


def privileged_value(values: list[object]) -> object | None:
    """The value granting the most access, such as `ADMIN` among the roles a sign-up offers."""
    return next((value for value in values if isinstance(value, str) and normalize(value) in _PRIVILEGED), None)
