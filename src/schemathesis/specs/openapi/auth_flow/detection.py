from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from schemathesis.config._auth import DynamicTokenAuthConfig
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.result import Ok
from schemathesis.specs.openapi.adapter.references import maybe_resolve_with_resolver
from schemathesis.specs.openapi.adapter.security import get_security_requirements
from schemathesis.specs.openapi.auth_flow.models import AuthFlowSpec, CredentialField, CredentialRole
from schemathesis.specs.openapi.auth_flow.vocabulary import classify

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemathesis.core.jsonschema.resolver import Resolver
    from schemathesis.core.jsonschema.types import JsonSchemaObject
    from schemathesis.specs.openapi.schemas import APIOperation, OpenApiSchema


REGISTER_PATH_RE = re.compile(
    r"/(register|signup|sign[_-]?up|users?|account|create[_-]?(account|user))(/|$)",
    re.IGNORECASE,
)


@dataclass(slots=True, frozen=True)
class OperationCandidate:
    label: str
    operation: APIOperation
    credential_fields: tuple[str, ...]


def _iter_operations(schema: OpenApiSchema) -> Iterator[APIOperation]:
    for result in schema.get_all_operations():
        if isinstance(result, Ok):
            yield result.ok()


def _body_credential_property_names(operation: APIOperation) -> tuple[str, ...]:
    # Multi-media-type bodies repeat the same schema; inspect the first one only.
    for body in operation.body:
        schema = body.raw_schema
        if not isinstance(schema, dict):
            continue
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            continue
        return tuple(name for name in properties if classify(name) is not None)
    return ()


def _has_2xx_response(operation: APIOperation) -> bool:
    for _ in operation.responses.iter_successful_responses():
        return True
    return False


def find_register_candidates(schema: OpenApiSchema) -> list[OperationCandidate]:
    """Operations matching Signal 1 (register-shaped POST with >=2 credential fields)."""
    candidates: list[OperationCandidate] = []
    for operation in _iter_operations(schema):
        if operation.method.lower() != "post":
            continue
        # Admin-area user-management endpoints (e.g. `/admin/users`) match the
        # register vocabulary but require admin auth — bootstrapping there is
        # a category error, not a signup flow.
        if "/admin/" in operation.path.lower():
            continue
        if not REGISTER_PATH_RE.search(operation.path):
            continue
        if not _has_2xx_response(operation):
            continue
        credential_fields = _body_credential_property_names(operation)
        if len(credential_fields) < 2:
            continue
        candidates.append(
            OperationCandidate(label=operation.label, operation=operation, credential_fields=credential_fields)
        )
    return candidates


LOGIN_PATH_RE = re.compile(
    r"/(login|signin|sign[_-]?in|auth|authenticate|token|session|oauth)(/|$)",
    re.IGNORECASE,
)


def find_login_for_register(schema: OpenApiSchema, register: OperationCandidate) -> OperationCandidate | None:
    """First operation matching the login-shape: POST to a login-like path whose body shares two or more credential fields with the register operation, including at least one SECRET-class field."""
    register_fields = set(register.credential_fields)
    for operation in _iter_operations(schema):
        if operation.method.lower() != "post":
            continue
        if not LOGIN_PATH_RE.search(operation.path):
            continue
        login_fields = _body_credential_property_names(operation)
        overlap = set(login_fields) & register_fields
        if len(overlap) < 2:
            continue
        if not any(classify(name) is CredentialRole.SECRET for name in overlap):
            continue
        return OperationCandidate(label=operation.label, operation=operation, credential_fields=tuple(overlap))
    return None


TOKEN_FIELD_RE = re.compile(
    r"^(access_?token|accessToken|jwt|bearer|id_?token|sessionToken|token|authToken)$",
    re.IGNORECASE,
)


@dataclass(slots=True, frozen=True)
class TokenSource:
    extract_from: str  # "body" or "header"
    extract_selector: str  # JSON-Pointer for body, header name for header
    target_scheme: str


def _resolve(subschema: JsonSchemaObject, resolver: Resolver) -> tuple[Resolver, JsonSchemaObject]:
    """Follow `$ref` chains until reaching an inline schema."""
    new_resolver, resolved = maybe_resolve_with_resolver(subschema, resolver)
    return new_resolver, cast("JsonSchemaObject", resolved)


def _walk_for_token(
    properties: JsonSchemaObject,
    resolver: Resolver,
    prefix: str = "",
) -> str | None:
    for name, subschema in properties.items():
        if not isinstance(subschema, dict):
            continue
        # Real-world specs almost always reference reusable schemas; dereference
        # before inspecting so refs to token-bearing types are matched.
        nested_resolver, resolved = _resolve(subschema, resolver)
        pointer = f"{prefix}/{name}"
        if resolved.get("type") == "string" and TOKEN_FIELD_RE.match(name):
            return pointer
        nested = resolved.get("properties")
        if isinstance(nested, dict):
            found = _walk_for_token(nested, nested_resolver, pointer)
            if found is not None:
                return found
    return None


def _security_schemes_for(schema: OpenApiSchema, operation: APIOperation) -> list[str]:
    return get_security_requirements(schema.raw_schema, operation.definition.raw)


def resolve_token_source(schema: OpenApiSchema, login: OperationCandidate) -> TokenSource | None:
    """Identify how to extract the bearer/api-key token from the login operation's success response."""
    pointer: str | None = None
    for response in login.operation.responses.iter_successful_responses():
        raw_schema = response.get_raw_schema()
        if not isinstance(raw_schema, dict):
            continue
        # Login response bodies are commonly declared as `$ref` to a reusable
        # type; resolve here so the property walker sees real `properties`.
        body_resolver, body_schema = _resolve(raw_schema, response.resolver)
        properties = body_schema.get("properties")
        if isinstance(properties, dict):
            found = _walk_for_token(properties, body_resolver)
            if found is not None:
                pointer = found
                break
    if pointer is None:
        return None

    requirement_names = _security_schemes_for(schema, login.operation)
    available = schema.security.security_definitions
    candidates = [name for name in requirement_names if name in available]
    # Login operations are typically unauthenticated (they produce the token, not consume it),
    # so the operation rarely declares the target scheme. Fall back to schema-wide schemes.
    if not candidates:
        candidates = list(available.keys())
    bearer = [
        name
        for name in candidates
        if available[name].get("type") == "http" and available[name].get("scheme", "").lower() == "bearer"
    ]
    apikey = [name for name in candidates if available[name].get("type") == "apiKey"]
    target = bearer or apikey
    if not target:
        return None

    return TokenSource(extract_from="body", extract_selector=pointer, target_scheme=target[0])


def _stricter(left: JsonSchemaObject, right: JsonSchemaObject) -> JsonSchemaObject:
    """Return whichever of two property schemas is more constrained.

    Compares the standard string constraints — pattern, minLength, maxLength,
    format — and prefers the schema scoring higher across them. Ties resolve
    to ``left``.
    """
    score_left = (
        bool(left.get("pattern")),
        left.get("minLength", 0),
        -left.get("maxLength", 1 << 31),
        bool(left.get("format")),
    )
    score_right = (
        bool(right.get("pattern")),
        right.get("minLength", 0),
        -right.get("maxLength", 1 << 31),
        bool(right.get("format")),
    )
    return left if score_left >= score_right else right


def _first_body_properties(operation: APIOperation) -> JsonSchemaObject | None:
    # Multi-media-type bodies repeat the same schema; inspect the first one only.
    for body in operation.body:
        schema = body.raw_schema
        if not isinstance(schema, dict):
            continue
        properties = schema.get("properties")
        if isinstance(properties, dict):
            return properties
    return None


def _build_credential_fields(register: OperationCandidate, login: OperationCandidate) -> tuple[CredentialField, ...]:
    """Build credential fields, preferring whichever schema is more constrained per field.

    Register and login bodies often share property names (``username``, ``password``)
    but only one side may carry validation constraints. Picking the stricter schema
    means minted credentials satisfy both endpoints.
    """
    register_properties = _first_body_properties(register.operation) or {}
    login_properties = _first_body_properties(login.operation)
    if login_properties is None:
        return ()

    fields: list[CredentialField] = []
    for name in login.credential_fields:
        login_schema = login_properties.get(name)
        if not isinstance(login_schema, dict):
            continue
        register_schema = register_properties.get(name)
        chosen = _stricter(register_schema, login_schema) if isinstance(register_schema, dict) else login_schema
        role = classify(name)
        # `_body_credential_property_names` filters by `classify(name) is not None`,
        # so login.credential_fields only contains classified names.
        assert role is not None
        fields.append(
            CredentialField(
                name=name,
                location=ParameterLocation.BODY,
                schema=chosen,
                role=role,
            )
        )
    return tuple(fields)


def detect_auth_flow(schema: OpenApiSchema) -> AuthFlowSpec | None:
    """Combine the three detection signals; return a complete `AuthFlowSpec` or `None`."""
    for register in find_register_candidates(schema):
        login = find_login_for_register(schema, register)
        if login is None:
            continue
        token = resolve_token_source(schema, login)
        if token is None:
            continue
        token_config = DynamicTokenAuthConfig(
            path=login.operation.path,
            method="post",
            payload={},
            extract_from=token.extract_from,
            extract_selector=token.extract_selector,
        )
        credentials = _build_credential_fields(register, login)
        if not credentials:
            continue
        return AuthFlowSpec(
            register_operation=register.label,
            login_operation=login.label,
            credentials=credentials,
            token_config=token_config,
            target_scheme=token.target_scheme,
        )
    return None
