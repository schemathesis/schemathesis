from __future__ import annotations

import base64
from dataclasses import dataclass

from flask import Flask, jsonify

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


@dataclass(slots=True)
class RotateTokenOnLogin:
    """Serve `POST /login` outside the spec; each call mints a new token and revokes the previous one."""

    priority: int = 0

    def apply(self, app: Flask, store: UnderDeclaredSecurityStore) -> None:
        issued = []

        @app.post("/login")
        def login():
            issued.append(f"token-{len(issued) + 1}")
            store.config.valid_token = issued[-1]
            return jsonify({"access_token": issued[-1]})


@dataclass(slots=True)
class ExpectBasicAuth:
    """Accept HTTP Basic credentials instead of the bearer token."""

    username: str
    password: str
    priority: int = 0

    def apply(self, app: Flask, store: UnderDeclaredSecurityStore) -> None:
        store.config.scheme = "Basic"
        store.config.valid_token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
