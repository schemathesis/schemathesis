"""Seed the runtime resource pool with identifier-style values from a Bearer JWT."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.resources.repository import ResourceRepository
    from schemathesis.specs.openapi.extra_data_source import ParameterRequirement


# Standard JWT claim -> pool field names. Conservative: only fields seen in real operation
# parameters / body fields; off-table claims and synthetic field names are skipped.
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

_SEED_SOURCE = "<jwt:auth>"


def decode_jwt_payload(token: str) -> dict | None:
    """Return the JWT payload dict, or None if the token isn't a parseable JWT.

    No signature verification - the SUT already does that. Failure is silent
    because Bearer tokens are commonly opaque/JWE/non-JWT.
    """
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
    """If `headers` contains a Bearer JWT, seed identifier claims into compatible resource buckets.

    A claim seeds a value under a resource only when one of its queried fields matches the
    claim's target field, so unrelated buckets aren't contaminated.
    """
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
    repository.seed_input_values(by_resource, source=_SEED_SOURCE)
