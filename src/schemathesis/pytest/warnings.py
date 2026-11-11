from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from schemathesis.core.errors import SERIALIZERS_SUGGESTION_MESSAGE
from schemathesis.core.warnings import SchemathesisWarning
from schemathesis.python._constants.orchestrator import build_constants_pool
from schemathesis.python._constants.warnings import iter_constants_warnings

if TYPE_CHECKING:
    from schemathesis.core.schema_analysis import SchemaWarning
    from schemathesis.schemas import BaseSchema

_TITLES = {
    SchemathesisWarning.UNUSED_OPENAPI_AUTH: "Unused OpenAPI auth configuration",
    SchemathesisWarning.CONSTANTS_EXTRACTION: "Constant reuse skipped",
    SchemathesisWarning.UNRESOLVABLE_REFERENCE: "Unresolvable references",
    SchemathesisWarning.UNSUPPORTED_REGEX: "Unsupported regex patterns",
    SchemathesisWarning.MISSING_DESERIALIZER: "Missing deserializer",
    SchemathesisWarning.DICTIONARY_MISMATCH: "Dictionary mismatch",
    SchemathesisWarning.UNSUPPORTED_FIELD: "Unsupported fields",
}


def emit_schema_warnings(schema: BaseSchema) -> None:
    """Emit Python warnings for schema problems found before any request is sent."""
    for warning in [*schema.iter_schema_warnings(), *iter_constants_warnings(build_constants_pool(schema))]:
        warnings.warn(_format_schema_warning(warning), UserWarning, stacklevel=6)


def _format_schema_warning(warning: SchemaWarning) -> str:
    message = warning.message
    if warning.kind is SchemathesisWarning.MISSING_DESERIALIZER:
        # The message is the status code; the media type is what lacks a deserializer.
        message = f"`{message}` responses in `{warning.group}` are not validated"
    title = _TITLES[warning.kind]
    if warning.operation_label is None:
        return f"{title}: {message}"
    return f"{title}: {warning.operation_label} - {message}"


def emit_unserializable_payload_warning(media_types: list[str]) -> None:
    """Emit a Python warning for payloads left untested because no serializer matched their media type."""
    warnings.warn(
        f"Skipped test cases with unsupported payload media types: {', '.join(media_types)}\n"
        f"{SERIALIZERS_SUGGESTION_MESSAGE}",
        UserWarning,
        stacklevel=2,
    )
