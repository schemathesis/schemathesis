from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from schemathesis.specs.openapi.warnings import UnusedOpenAPIAuthWarning

if TYPE_CHECKING:
    from schemathesis.core.spec import SchemaWarnings


def emit_openapi_auth_warnings(schema: SchemaWarnings) -> None:
    """Emit Python warnings for unused OpenAPI auth configuration."""
    for warning in schema.iter_schema_warnings():
        if isinstance(warning, UnusedOpenAPIAuthWarning):
            warnings.warn(
                f"Unused OpenAPI auth configuration: {warning.message}",
                UserWarning,
                stacklevel=6,
            )
