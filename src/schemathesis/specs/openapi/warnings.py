"""OpenAPI-specific static schema warnings."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.config import SchemathesisWarning
from schemathesis.core import deserialization
from schemathesis.core.errors import MalformedMediaType
from schemathesis.core.jsonschema.types import get_type

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.schemas import OpenApiSchema


@dataclass
class MissingDeserializerWarning:
    """Warning for responses with structured schemas but no registered deserializer."""

    operation_label: str | None
    """Label of the operation (e.g., 'GET /users'). Always set for this warning type."""

    status_code: str
    """HTTP status code for the response."""

    content_type: str
    """Media type that has no deserializer."""

    __slots__ = ("operation_label", "status_code", "content_type")

    @property
    def kind(self) -> SchemathesisWarning:
        """The warning kind for configuration matching."""
        return SchemathesisWarning.MISSING_DESERIALIZER

    @property
    def message(self) -> str:
        """Human-readable description of the warning."""
        return f"Cannot validate response {self.status_code}: no deserializer registered for {self.content_type}"


def detect_missing_deserializers(operation: APIOperation) -> list[MissingDeserializerWarning]:
    """Detect responses with structured schemas but no registered deserializer."""
    warnings: list[MissingDeserializerWarning] = []

    for status_code, response in operation.responses.items():
        raw_schema = getattr(response, "get_raw_schema", lambda: None)()
        if raw_schema is None:
            continue

        schema_types = get_type(raw_schema)
        is_structured = any(t in ("object", "array") for t in schema_types)

        if not is_structured:
            continue

        content_types = response.definition.get("content", {}).keys() if response.definition else []

        for content_type in content_types:
            try:
                has_deserializer = deserialization.has_deserializer(content_type)
            except MalformedMediaType:
                continue

            if not has_deserializer:
                warnings.append(
                    MissingDeserializerWarning(
                        operation_label=operation.label,
                        status_code=status_code,
                        content_type=content_type,
                    )
                )

    return warnings


@dataclass
class UnusedOpenAPIAuthWarning:
    """Warning for configured OpenAPI auth schemes that are not used in the schema."""

    operation_label: str | None
    """Label of the operation or None for schema-level warnings."""

    scheme_name: str
    """Name of the configured auth scheme that is unused."""

    suggestion: str | None
    """Suggested scheme name if there's a close match."""

    __slots__ = ("operation_label", "scheme_name", "suggestion")

    @property
    def kind(self) -> SchemathesisWarning:
        """The warning kind for configuration matching."""
        return SchemathesisWarning.UNUSED_OPENAPI_AUTH

    @property
    def message(self) -> str:
        """Human-readable description of the warning."""
        if self.suggestion:
            return f"'{self.scheme_name}' - Did you mean '{self.suggestion}'?"
        return f"'{self.scheme_name}'"


def detect_unused_openapi_auth(schema: OpenApiSchema) -> list[UnusedOpenAPIAuthWarning]:
    """Detect configured OpenAPI auth schemes that don't exist in the schema."""
    warnings: list[UnusedOpenAPIAuthWarning] = []

    configured_schemes = schema.config.auth.openapi.schemes
    if not configured_schemes:
        return warnings

    # Get security schemes defined in the OpenAPI schema (via adapter)
    security_schemes = schema.security.security_definitions

    for scheme_name in configured_schemes:
        # Check if scheme exists in the schema definition, and suggest a candidate if it does not
        if scheme_name not in security_schemes:
            suggestion = _find_closest_match(scheme_name, list(security_schemes.keys()))
            warnings.append(
                UnusedOpenAPIAuthWarning(operation_label=None, scheme_name=scheme_name, suggestion=suggestion)
            )

    return warnings


def _find_closest_match(value: str, candidates: list[str]) -> str | None:
    """Find the closest matching string from candidates."""
    matches = difflib.get_close_matches(value, candidates, n=1, cutoff=0.6)
    return matches[0] if matches else None
