from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve
from schemathesis.config._error import ConfigError
from schemathesis.core.validation import is_latin_1_encodable


@dataclass(repr=False, slots=True)
class ApiKeyAuthConfig(DiffBase):
    """API Key authentication configuration."""

    api_key: str

    def __init__(self, *, api_key: str = "") -> None:
        self.api_key = resolve(api_key)


@dataclass(repr=False, slots=True)
class HttpBasicAuthConfig(DiffBase):
    """HTTP Basic authentication configuration."""

    username: str
    password: str

    def __init__(self, *, username: str = "", password: str = "") -> None:
        resolved_username = resolve(username)
        resolved_password = resolve(password)
        if resolved_username or resolved_password:
            _validate_basic(resolved_username, resolved_password)
        self.username = resolved_username
        self.password = resolved_password


@dataclass(repr=False, slots=True)
class HttpBearerAuthConfig(DiffBase):
    """HTTP Bearer token authentication configuration."""

    bearer: str

    def __init__(self, *, bearer: str = "") -> None:
        self.bearer = resolve(bearer)


@dataclass(repr=False, slots=True)
class DynamicTokenAuthConfig(DiffBase):
    """Dynamic token fetch authentication configuration."""

    path: str
    method: str
    payload: dict[str, str] | None
    payload_content_type: str
    extract_from: str
    extract_selector: str

    def __init__(
        self,
        *,
        path: str = "",
        method: str = "post",
        payload: dict[str, str] | None = None,
        payload_content_type: str = "application/json",
        extract_from: str = "body",
        extract_selector: str = "",
    ) -> None:
        if path and not path.startswith("/"):
            raise ConfigError(f"Dynamic auth `path` must start with '/': {path!r}")
        if extract_from == "body" and extract_selector and not extract_selector.startswith("/"):
            raise ConfigError(
                f"Dynamic auth `extract_selector` must start with '/' when extract_from='body': {extract_selector!r}"
            )
        if not payload_content_type:
            raise ConfigError("Dynamic auth `payload_content_type` must be a non-empty media type string.")
        self.path = path
        self.method = method.lower()
        self.payload = {k: resolve(v) for k, v in payload.items()} if payload else None
        self.payload_content_type = payload_content_type
        self.extract_from = extract_from
        self.extract_selector = extract_selector


@dataclass(repr=False, slots=True)
class OpenAPIAuthConfig(DiffBase):
    """OpenAPI-aware authentication configuration."""

    schemes: dict[str, ApiKeyAuthConfig | HttpBasicAuthConfig | HttpBearerAuthConfig]

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


@dataclass(repr=False, slots=True)
class OpenAPIDynamicAuthConfig(DiffBase):
    """OpenAPI-aware dynamic authentication configuration."""

    schemes: dict[str, DynamicTokenAuthConfig]

    def __init__(self, *, schemes: dict[str, dict[str, Any]] | None = None) -> None:
        self.schemes = {name: DynamicTokenAuthConfig(**cfg) for name, cfg in schemes.items()} if schemes else {}

    @property
    def is_defined(self) -> bool:
        return bool(self.schemes)


@dataclass(repr=False, slots=True)
class WFCAuthConfig(DiffBase):
    """Web Fuzzing Commons authentication configuration."""

    path: str
    user: str | None
    refresh_interval: int

    def __init__(
        self,
        *,
        path: str,
        user: str | None = None,
        refresh_interval: int = 300,
    ) -> None:
        self.path = resolve(path)
        self.user = resolve(user) if user is not None else None
        self.refresh_interval = refresh_interval


@dataclass(repr=False, slots=True)
class AuthConfig(DiffBase):
    basic: tuple[str, str] | None
    openapi: OpenAPIAuthConfig
    dynamic: OpenAPIDynamicAuthConfig
    wfc: WFCAuthConfig | None

    def __init__(
        self,
        *,
        basic: dict[str, str] | None = None,
        openapi: dict[str, dict[str, Any]] | None = None,
        dynamic: dict[str, Any] | None = None,
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

        self.dynamic = OpenAPIDynamicAuthConfig(schemes=dynamic.get("openapi") if dynamic else None)
        overlap = set(self.openapi.schemes) & set(self.dynamic.schemes)
        if overlap:
            if len(overlap) == 1:
                (name,) = overlap
                raise ConfigError(
                    f"Scheme {name!r} appears in both auth.openapi and auth.dynamic.openapi. Use one or the other."
                )
            names = ", ".join(f"'{n}'" for n in sorted(overlap))
            raise ConfigError(
                f"Schemes {names} appear in both auth.openapi and auth.dynamic.openapi. Use one or the other."
            )

        if wfc is not None:
            self.wfc = WFCAuthConfig(**wfc)
        else:
            self.wfc = None

        # Validate mutual exclusivity
        openapi_aware = self.openapi.is_defined or self.dynamic.is_defined
        auth_methods = sum(
            [
                self.basic is not None,
                openapi_aware,
                self.wfc is not None,
            ]
        )
        if auth_methods > 1:
            methods = []
            if self.basic is not None:
                methods.append("[auth.basic] (generic basic authentication)")
            if openapi_aware:
                methods.append("[auth.openapi.*] or [auth.dynamic.openapi.*] (OpenAPI-aware authentication)")
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
        return cls(
            basic=data.get("basic"),
            openapi=data.get("openapi"),
            dynamic=data.get("dynamic"),
            wfc=data.get("wfc"),
        )

    @property
    def all_openapi_schemes(
        self,
    ) -> dict[str, ApiKeyAuthConfig | HttpBasicAuthConfig | HttpBearerAuthConfig | DynamicTokenAuthConfig]:
        """Combined static and dynamic OpenAPI security schemes."""
        return {**self.openapi.schemes, **self.dynamic.schemes}

    @property
    def is_defined(self) -> bool:
        return self.basic is not None or self.openapi.is_defined or self.dynamic.is_defined or self.wfc is not None


def _validate_basic(username: str, password: str) -> None:
    if not is_latin_1_encodable(username):
        raise ConfigError("Username should be latin-1 encodable.")
    if not is_latin_1_encodable(password):
        raise ConfigError("Password should be latin-1 encodable.")
