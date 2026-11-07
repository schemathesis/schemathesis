"""Web Fuzzing Commons authentication support (https://github.com/WebFuzzing/Commons)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.errors import LoaderError, LoaderErrorKind

if TYPE_CHECKING:
    from schemathesis.config import ProjectConfig
    from schemathesis.schemas import BaseSchema


def setup_auth(schema: BaseSchema, config: ProjectConfig, kind: LoaderErrorKind) -> None:
    """Register `[auth.wfc]` on a loaded HTTP schema, reporting a broken auth file as a loading error of `kind`."""
    if config.auth.wfc is None:
        return
    from schemathesis.wfc.errors import WFCError
    from schemathesis.wfc.integration import register_wfc_auth

    try:
        register_wfc_auth(schema, config.auth.wfc)
    except WFCError as exc:
        raise LoaderError(kind, f"Failed to load Web Fuzzing Commons authentication: {exc}") from exc
