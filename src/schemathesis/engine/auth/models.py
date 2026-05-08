from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from schemathesis.engine import Status

if TYPE_CHECKING:
    from schemathesis.specs.openapi.auth_flow.models import AuthFlowSpec


@dataclass(slots=True)
class BootstrappedSession:
    credentials: dict[str, str]
    token: str


@dataclass(slots=True)
class AuthBootstrapPayload:
    spec: AuthFlowSpec | None
    status: Status
    failure_stage: Literal["mint", "register", "login", "extract"] | None = None
    status_code: int | None = None
    message: str | None = None
