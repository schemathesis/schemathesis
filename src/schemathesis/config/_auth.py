from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve
from schemathesis.config._error import ConfigError
from schemathesis.core.validation import is_latin_1_encodable


@dataclass(repr=False)
class ApiKeyAuthConfig(DiffBase):
    """API Key authentication configuration."""

    api_key: str

    __slots__ = ("api_key",)

    def __init__(self, *, api_key: str = "") -> None:
        self.api_key = resolve(api_key)


@dataclass(repr=False)
class HttpBasicAuthConfig(DiffBase):
    """HTTP Basic authentication configuration."""

    username: str
    password: str

    __slots__ = ("username", "password")

    def __init__(self, *, username: str = "", password: str = "") -> None:
        resolved_username = resolve(username)
        resolved_password = resolve(password)
        if resolved_username or resolved_password:
            _validate_basic(resolved_username, resolved_password)
        self.username = resolved_username
        self.password = resolved_password


@dataclass(repr=False)
class HttpBearerAuthConfig(DiffBase):
    """HTTP Bearer token authentication configuration."""

    bearer: str

    __slots__ = ("bearer",)

    def __init__(self, *, bearer: str = "") -> None:
        self.bearer = resolve(bearer)


@dataclass(repr=False)
class OpenAPIAuthConfig(DiffBase):
    """OpenAPI-aware authentication configuration."""

    schemes: dict[str, ApiKeyAuthConfig | HttpBasicAuthConfig | HttpBearerAuthConfig]

    __slots__ = ("schemes",)

    def __init__(self, *, schemes: dict[str, dict[str, Any]] | None = None) -> None:
        if schemes is None:
            self.schemes = {}
        else:
            self.schemes = {}
            for name, scheme in schemes.items():
                # Detect config type by unique property sets
                if "api_key" in scheme:
                    self.schemes[name] = ApiKeyAuthConfig(**scheme)
                elif "username" in scheme and "password" in scheme:
                    self.schemes[name] = HttpBasicAuthConfig(**scheme)
                elif "bearer" in scheme:
                    self.schemes[name] = HttpBearerAuthConfig(**scheme)

    @property
    def is_defined(self) -> bool:
        return len(self.schemes) > 0


@dataclass(repr=False)
class AuthConfig(DiffBase):
    basic: tuple[str, str] | None
    openapi: OpenAPIAuthConfig

    __slots__ = ("basic", "openapi")

    def __init__(
        self,
        *,
        basic: dict[str, str] | None = None,
        openapi: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        if basic is not None:
            assert "username" in basic
            username = resolve(basic["username"])
            assert "password" in basic
            password = resolve(basic["password"])
            _validate_basic(username, password)
            self.basic = (username, password)
        else:
            self.basic = None

        self.openapi = OpenAPIAuthConfig(schemes=openapi)

        # Validate mutual exclusivity
        if self.basic is not None and self.openapi.is_defined:
            raise ConfigError(
                "Cannot use both generic basic authentication and OpenAPI-aware authentication.\n\n"
                "You have configured:\n"
                "  - [auth.basic] (generic basic authentication)\n"
                "  - [auth.openapi.*] (OpenAPI-aware authentication)\n\n"
                "Please choose one authentication method:\n"
                "  - Use [auth.basic] for simple basic auth on all operations\n"
                "  - Use [auth.openapi.*] for OpenAPI security scheme-aware authentication"
            )

    def update(self, *, basic: tuple[str, str] | None = None) -> None:
        """Update auth config with explicit override (from CLI or user code).

        This method is for explicit overrides, so it does not validate mutual exclusivity.
        CLI and programmatic overrides take precedence over config file settings.
        """
        if basic is not None:
            _validate_basic(*basic)
            self.basic = basic

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuthConfig:
        return cls(basic=data.get("basic"), openapi=data.get("openapi"))

    @property
    def is_defined(self) -> bool:
        return self.basic is not None or self.openapi.is_defined


def _validate_basic(username: str, password: str) -> None:
    if not is_latin_1_encodable(username):
        raise ConfigError("Username should be latin-1 encodable.")
    if not is_latin_1_encodable(password):
        raise ConfigError("Password should be latin-1 encodable.")
