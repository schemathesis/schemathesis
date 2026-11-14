"""Seed the runtime resource pool with identifier-style values from a Bearer JWT."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.resources.repository import ResourceRepository
    from schemathesis.specs.openapi.extra_data_source import ParameterRequirement


# JWT claim to pool field names, limited to fields seen in real parameters and body fields.
IDENTIFIER_CLAIMS: dict[str, tuple[str, ...]] = {
    "sub": ("id", "user_id", "userId", "username"),
    "username": ("username",),
    "preferred_username": ("username",),
    "user_id": ("id", "user_id", "userId"),
    "userId": ("id", "user_id", "userId"),
    "uid": ("id", "user_id", "userId"),
    "email": ("email",),
    "tenant": ("tenant_id", "tenantId"),
    "tenantId": ("tenant_id", "tenantId"),
    "tid": ("tenant_id", "tenantId"),
    "client_id": ("client_id", "clientId"),
    "azp": ("client_id", "clientId"),
    "org_id": ("org_id", "orgId"),
    "orgId": ("org_id", "orgId"),
}

_JWT_SEED_SOURCE = "<jwt:auth>"
_BASIC_SEED_SOURCE = "<basic:auth>"
_BASIC_AUTH_FIELDS: tuple[str, ...] = ("id", "user_id", "userId", "username")


def decode_jwt_payload(token: str) -> dict | None:
    """The JWT payload without signature verification, or `None` for opaque and other non-JWT tokens."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def seed_pool_from_headers(
    repository: ResourceRepository, headers: dict[str, str], requirements: Iterable[ParameterRequirement]
) -> None:
    """Seed identifier claims of a Bearer JWT into resources that query one of the claim's target fields."""
    token = next(
        (v[7:].strip() for k, v in headers.items() if k.lower() == "authorization" and v.startswith("Bearer ")),
        None,
    )
    if token is None:
        return
    payload = decode_jwt_payload(token)
    if payload is None:
        return
    field_to_value: dict[str, str] = {}
    for claim, fields in IDENTIFIER_CLAIMS.items():
        value = payload.get(claim)
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            continue
        for field in fields:
            field_to_value.setdefault(field, str(value))
    if not field_to_value:
        return
    by_resource: dict[str, dict[str, str]] = {}
    for requirement in requirements:
        if requirement.resource_field in field_to_value:
            by_resource.setdefault(requirement.resource_name, {})[requirement.resource_field] = field_to_value[
                requirement.resource_field
            ]
    repository.seed_input_values(by_resource, source=_JWT_SEED_SOURCE)


def seed_pool_from_basic_auth(
    repository: ResourceRepository,
    basic_auth: tuple[str, str] | None,
    requirements: Iterable[ParameterRequirement],
) -> None:
    """Seed the configured Basic-auth username, never the password, into compatible resource buckets."""
    if basic_auth is None or not basic_auth[0]:
        return
    username = basic_auth[0]
    by_resource: dict[str, dict[str, str]] = {}
    for requirement in requirements:
        if requirement.resource_field in _BASIC_AUTH_FIELDS:
            by_resource.setdefault(requirement.resource_name, {})[requirement.resource_field] = username
    repository.seed_input_values(by_resource, source=_BASIC_SEED_SOURCE)
