from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.parameters import ParameterLocation

if TYPE_CHECKING:
    from schemathesis.config._auth import DynamicTokenAuthConfig


class CredentialRole(str, enum.Enum):
    IDENTIFIER = "identifier"
    SECRET = "secret"
    EMAIL = "email"


@dataclass(slots=True, frozen=True)
class CredentialField:
    name: str
    location: ParameterLocation
    schema: JsonSchema
    role: CredentialRole


@dataclass(slots=True, frozen=True)
class AuthFlowSpec:
    register_operation: str
    login_operation: str
    credentials: tuple[CredentialField, ...]
    token_config: DynamicTokenAuthConfig
    target_scheme: str
