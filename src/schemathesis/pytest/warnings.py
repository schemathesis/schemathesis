from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.schemas import BaseSchema


def emit_openapi_auth_warnings(schema: BaseSchema) -> None:
    """Emit Python warnings for unused OpenAPI auth configuration."""
    from schemathesis.specs.openapi.schemas import OpenApiSchema
    from schemathesis.specs.openapi.warnings import UnusedOpenAPIAuthWarning

    if not isinstance(schema, OpenApiSchema):
        return
    for warning in schema.analysis._get_schema_warnings():
        if isinstance(warning, UnusedOpenAPIAuthWarning):
            warnings.warn(
                f"Unused OpenAPI auth configuration: {warning.message}",
                UserWarning,
                stacklevel=6,
            )
