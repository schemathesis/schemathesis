"""OpenAPI-specific static schema warnings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.config import SchemathesisWarning
from schemathesis.core import deserialization
from schemathesis.core.errors import MalformedMediaType
from schemathesis.core.jsonschema.types import get_type

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


@dataclass
class MissingDeserializerWarning:
    """Warning for responses with structured schemas but no registered deserializer."""

    operation_label: str
    """Label of the operation (e.g., 'GET /users')."""

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
