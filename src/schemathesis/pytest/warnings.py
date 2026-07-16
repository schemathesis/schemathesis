from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from schemathesis.python._constants.orchestrator import build_constants_pool
from schemathesis.python._constants.warnings import iter_constants_warnings
from schemathesis.specs.openapi.warnings import UnusedOpenAPIAuthWarning

if TYPE_CHECKING:
    from schemathesis.core.spec import SchemaWarnings
    from schemathesis.schemas import BaseSchema


def emit_openapi_auth_warnings(schema: SchemaWarnings) -> None:
    """Emit Python warnings for unused OpenAPI auth configuration."""
    for warning in schema.iter_schema_warnings():
        if isinstance(warning, UnusedOpenAPIAuthWarning):
            warnings.warn(
                f"Unused OpenAPI auth configuration: {warning.message}",
                UserWarning,
                stacklevel=6,
            )


def emit_constants_warnings(schema: BaseSchema) -> None:
    """Emit Python warnings for registered constants sources that could not be scanned."""
    for warning in iter_constants_warnings(build_constants_pool(schema)):
        warnings.warn(f"Constant reuse skipped: {warning.message}", UserWarning, stacklevel=6)
