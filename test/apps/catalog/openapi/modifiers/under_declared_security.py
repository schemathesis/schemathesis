from __future__ import annotations

from dataclasses import dataclass

from flask import Flask

from test.apps.catalog.openapi.under_declared_security import UnderDeclaredSecurityStore


@dataclass(slots=True)
class DeclareSecurity:
    """Add `security: [{BearerAuth: []}]` to the operation — server still enforces it the same way."""

    priority: int = 0

    def apply(self, app: Flask, store: UnderDeclaredSecurityStore) -> None:
        spec = app.config["schema"]
        spec["paths"]["/protected"]["get"]["security"] = [{"BearerAuth": []}]


@dataclass(slots=True)
class RespondWithStatus:
    """Return `status` (instead of 200) when the bearer token matches."""

    status: int
    priority: int = 0

    def apply(self, app: Flask, store: UnderDeclaredSecurityStore) -> None:
        store.config.authed_status = self.status


@dataclass(slots=True)
class DocumentResponseStatus:
    """Add `status` to the operation's `responses` block — pairs with `RespondWithStatus`."""

    status: int
    description: str = "documented response"
    priority: int = 0

    def apply(self, app: Flask, store: UnderDeclaredSecurityStore) -> None:
        spec = app.config["schema"]
        spec["paths"]["/protected"]["get"]["responses"][str(self.status)] = {"description": self.description}
