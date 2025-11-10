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
class WFCAuthConfig(DiffBase):
    """Web Fuzzing Commons authentication configuration."""

    file: str
    user: str | None
    refresh_interval: int
    base_url: str | None

    __slots__ = ("file", "user", "refresh_interval", "base_url")

    def __init__(
        self,
        *,
        file: str,
        user: str | None = None,
        refresh_interval: int = 300,
        base_url: str | None = None,
    ) -> None:
        self.file = resolve(file)
        self.user = resolve(user) if user is not None else None
        self.refresh_interval = refresh_interval
        self.base_url = resolve(base_url) if base_url is not None else None


@dataclass(repr=False)
class AuthConfig(DiffBase):
    basic: tuple[str, str] | None
    openapi: OpenAPIAuthConfig
    wfc: WFCAuthConfig | None

    __slots__ = ("basic", "openapi", "wfc")

    def __init__(
        self,
        *,
        basic: dict[str, str] | None = None,
        openapi: dict[str, dict[str, Any]] | None = None,
        wfc: dict[str, Any] | None = None,
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

        if wfc is not None:
            self.wfc = WFCAuthConfig(**wfc)
        else:
            self.wfc = None

        # Validate mutual exclusivity
        auth_methods = sum(
            [
                self.basic is not None,
                self.openapi.is_defined,
                self.wfc is not None,
            ]
        )
        if auth_methods > 1:
            methods = []
            if self.basic is not None:
                methods.append("[auth.basic] (generic basic authentication)")
            if self.openapi.is_defined:
                methods.append("[auth.openapi.*] (OpenAPI-aware authentication)")
            if self.wfc is not None:
                methods.append("[auth.wfc] (Web Fuzzing Commons authentication)")

            raise ConfigError(
                "Cannot use multiple authentication methods simultaneously.\n\n"
                "You have configured:\n" + "\n".join(f"  - {m}" for m in methods) + "\n\n"
                "Please choose one authentication method."
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
        return cls(basic=data.get("basic"), openapi=data.get("openapi"), wfc=data.get("wfc"))

    @property
    def is_defined(self) -> bool:
        return self.basic is not None or self.openapi.is_defined or self.wfc is not None


def _validate_basic(username: str, password: str) -> None:
    if not is_latin_1_encodable(username):
        raise ConfigError("Username should be latin-1 encodable.")
    if not is_latin_1_encodable(password):
        raise ConfigError("Password should be latin-1 encodable.")
