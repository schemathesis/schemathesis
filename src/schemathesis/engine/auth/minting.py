from __future__ import annotations

import secrets
import string
from collections.abc import Sequence

import jsonschema_rs

from schemathesis.core.jsonschema import make_validator
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.specs.openapi.auth_flow.models import CredentialField, CredentialRole

_LOWER = string.ascii_lowercase
_UPPER = string.ascii_uppercase
_DIGITS = string.digits
_SYMBOLS = "!@#$%"


class MintingError(Exception):
    """Raised when no value satisfies a credential field's declared schema."""


def _validate(value: object, schema: JsonSchema) -> bool:
    try:
        validator = make_validator(schema, jsonschema_rs.Draft7Validator)
    except Exception:
        return True
    try:
        return validator.is_valid(value)
    except Exception:
        return False


def _random_alphanum(length: int) -> str:
    pool = _LOWER + _UPPER + _DIGITS
    return "".join(secrets.choice(pool) for _ in range(length))


def _strong_password(length: int) -> str:
    chars = [
        secrets.choice(_UPPER),
        secrets.choice(_LOWER),
        secrets.choice(_DIGITS),
        secrets.choice(_SYMBOLS),
    ]
    chars.extend(secrets.choice(_LOWER + _UPPER + _DIGITS + _SYMBOLS) for _ in range(max(0, length - 4)))
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def _mint_email() -> str:
    local = _random_alphanum(8).lower()
    domain = _random_alphanum(6).lower()
    return f"{local}@{domain}.test"


def _mint_lowercase(length: int) -> str:
    return "".join(secrets.choice(_LOWER) for _ in range(length))


def _hypothesis_fallback(schema: JsonSchema) -> str | None:
    """Single positive draw via Hypothesis; returns None when unavailable or impossible."""
    try:
        from hypothesis import HealthCheck, find, settings
        from hypothesis_jsonschema import from_schema
    except Exception:
        return None
    try:
        strategy = from_schema(schema)
    except Exception:
        return None
    try:
        result = find(
            strategy,
            lambda v: True,
            settings=settings(suppress_health_check=list(HealthCheck), max_examples=50, deadline=None),
        )
    except Exception:
        return None
    if isinstance(result, str) and result:
        return result
    return None


def mint_credential_value(field: CredentialField) -> str:
    schema = field.schema
    role = field.role
    # `JsonSchema` is `dict[str, Any] | bool`; only object schemas carry constraints.
    if isinstance(schema, dict):
        minimum_length: int = schema.get("minLength") or 0
        maximum_length: int | None = schema.get("maxLength")
        pattern: str | None = schema.get("pattern")
    else:
        minimum_length = 0
        maximum_length = None
        pattern = None

    candidate: str
    if role is CredentialRole.SECRET:
        length = max(16, minimum_length)
        if maximum_length is not None and maximum_length < length:
            length = maximum_length
        candidate = _strong_password(length)
    elif role is CredentialRole.EMAIL:
        candidate = _mint_email()
    else:
        # CredentialRole.IDENTIFIER — detection only emits classified roles.
        length = max(8, minimum_length)
        if maximum_length is not None and maximum_length < length:
            length = max(minimum_length, 1)
        candidate = _random_alphanum(length)

    if _validate(candidate, schema):
        return candidate

    if pattern:
        retry = _mint_lowercase(max(minimum_length, 8))
        if _validate(retry, schema):
            return retry

    fallback = _hypothesis_fallback(schema)
    if fallback is not None and _validate(fallback, schema):
        return fallback

    raise MintingError(f"could not mint a value for {field.name!r} satisfying {schema!r}")


def mint_credentials(fields: Sequence[CredentialField]) -> dict[str, str]:
    """Mint a value for every field; raises `MintingError` on the first failure."""
    return {field.name: mint_credential_value(field) for field in fields}
