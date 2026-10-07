from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from schemathesis.core.result import Ok
from schemathesis.specs.openapi.adapter.references import maybe_resolve_with_resolver
from schemathesis.specs.openapi.adapter.security import get_security_requirements
from schemathesis.specs.openapi.auth_flow.models import AuthFlowSpec
from schemathesis.specs.openapi.auth_flow.vocabulary import is_credential, is_secret

if TYPE_CHECKING:
    from schemathesis.core.jsonschema.resolver import Resolver
    from schemathesis.core.jsonschema.types import JsonSchemaObject
    from schemathesis.specs.openapi.schemas import APIOperation, OpenApiSchema


REGISTER_SEGMENT_RE = re.compile(r"register|signup|sign[_-]?up|users?|account|create[_-]?(account|user)", re.IGNORECASE)
LOGIN_SEGMENT_RE = re.compile(r"login|signin|sign[_-]?in|auth|authenticate|token|session|oauth", re.IGNORECASE)
TOKEN_FIELD_RE = re.compile(
    r"^(access_?token|accessToken|jwt|bearer|id_?token|sessionToken|token|authToken)$",
    re.IGNORECASE,
)


@dataclass(slots=True, frozen=True)
class _Body:
    media_type: str
    credentials: frozenset[str]


def _credential_body(operation: APIOperation) -> _Body | None:
    # Multi-media-type bodies repeat the same schema; inspect the first one with properties.
    for body in operation.body:
        schema = body.raw_schema
        if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
            names = frozenset(name for name in schema["properties"] if is_credential(name))
            return _Body(media_type=body.media_type, credentials=names)
    return None


def _last_segment(path: str) -> str:
    # What the operation does is named by its final literal segment: `/auth/register` registers.
    segments = [segment for segment in path.split("/") if segment and not segment.startswith("{")]
    return segments[-1] if segments else ""


def _is_register(operation: APIOperation, body: _Body) -> bool:
    # Admin-area user management matches the sign-up vocabulary but needs admin auth.
    return (
        "/admin/" not in operation.path.lower()
        and REGISTER_SEGMENT_RE.fullmatch(_last_segment(operation.path)) is not None
        and any(True for _ in operation.responses.iter_successful_responses())
        and len(body.credentials) >= 2
    )


def _find_login(
    operations: list[tuple[APIOperation, _Body]], register: APIOperation, register_body: _Body
) -> tuple[APIOperation, _Body, frozenset[str]] | None:
    for operation, body in operations:
        # A templated login path cannot be fetched as-is.
        if "{" in operation.path or not LOGIN_SEGMENT_RE.fullmatch(_last_segment(operation.path)):
            continue
        overlap = body.credentials & register_body.credentials
        if len(overlap) >= 2 and any(is_secret(name) for name in overlap):
            return operation, body, overlap
    return None


def _resolve(subschema: JsonSchemaObject, resolver: Resolver) -> tuple[Resolver, JsonSchemaObject]:
    new_resolver, resolved = maybe_resolve_with_resolver(subschema, resolver)
    return new_resolver, cast("JsonSchemaObject", resolved)


def _find_token(properties: JsonSchemaObject, resolver: Resolver, seen: set[int], prefix: str = "") -> str | None:
    for name, subschema in properties.items():
        if not isinstance(subschema, dict):
            continue
        nested_resolver, resolved = _resolve(subschema, resolver)
        pointer = f"{prefix}/{name}"
        if resolved.get("type") == "string" and TOKEN_FIELD_RE.match(name):
            return pointer
        nested = resolved.get("properties")
        # Recursive response types would otherwise be walked forever.
        if isinstance(nested, dict) and id(nested) not in seen:
            seen.add(id(nested))
            found = _find_token(nested, nested_resolver, seen, pointer)
            if found is not None:
                return found
    return None


def _token_pointer(login: APIOperation) -> str | None:
    for response in login.responses.iter_successful_responses():
        raw_schema = response.get_raw_schema()
        if not isinstance(raw_schema, dict):
            continue
        resolver, body_schema = _resolve(raw_schema, response.resolver)
        properties = body_schema.get("properties")
        if isinstance(properties, dict):
            found = _find_token(properties, resolver, {id(properties)})
            if found is not None:
                return found
    return None


def _target_scheme(schema: OpenApiSchema, login: APIOperation) -> str | None:
    available = schema.security.security_definitions
    candidates = [
        name for name in get_security_requirements(schema.raw_schema, login.definition.raw) if name in available
    ]
    # Login operations produce the token rather than consume it, so they rarely declare the target scheme.
    if not candidates:
        candidates = list(available)
    bearer = [
        name
        for name in candidates
        if available[name].get("type") == "http" and available[name].get("scheme", "").lower() == "bearer"
    ]
    apikey = [name for name in candidates if available[name].get("type") == "apiKey"]
    target = bearer or apikey
    return target[0] if target else None


def detect_auth_flow(schema: OpenApiSchema) -> AuthFlowSpec | None:
    """A sign-up operation, a login operation sharing its credentials, and where the login returns a token."""
    operations = []
    for result in schema.get_all_operations():
        if isinstance(result, Ok):
            operation = result.ok()
            body = _credential_body(operation)
            if operation.method.lower() == "post" and body is not None:
                operations.append((operation, body))
    for register, register_body in operations:
        if not _is_register(register, register_body):
            continue
        found = _find_login(operations, register, register_body)
        if found is None:
            continue
        login, login_body, credentials = found
        pointer = _token_pointer(login)
        if pointer is None:
            continue
        target = _target_scheme(schema, login)
        if target is None:
            continue
        return AuthFlowSpec(
            register_operation=register.label,
            login_operation=login.label,
            login_path=login.path,
            login_media_type=login_body.media_type,
            credentials=tuple(sorted(credentials)),
            token_pointer=pointer,
            target_scheme=target,
        )
    return None
