from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemathesis.config._diff_base import DiffBase

# Exact keys to sanitize
DEFAULT_KEYS_TO_SANITIZE = (
    "phpsessid",
    "xsrf-token",
    "_csrf",
    "_csrf_token",
    "_session",
    "_xsrf",
    "aiohttp_session",
    "api_key",
    "api-key",
    "apikey",
    "auth",
    "authorization",
    "connect.sid",
    "cookie",
    "credentials",
    "csrf",
    "csrf_token",
    "csrf-token",
    "csrftoken",
    "ip_address",
    "mysql_pwd",
    "passwd",
    "password",
    "private_key",
    "private-key",
    "privatekey",
    "remote_addr",
    "remote-addr",
    "secret",
    "session",
    "sessionid",
    "set_cookie",
    "set-cookie",
    "token",
    "x_api_key",
    "x-api-key",
    "x_csrftoken",
    "x-csrftoken",
    "x_forwarded_for",
    "x-forwarded-for",
    "x_real_ip",
    "x-real-ip",
)

# Markers indicating potentially sensitive keys
DEFAULT_SENSITIVE_MARKERS = (
    "token",
    "key",
    "secret",
    "password",
    "auth",
    "session",
    "passwd",
    "credential",
)

DEFAULT_REPLACEMENT = "[Filtered]"


@dataclass(repr=False)
class SanitizationConfig(DiffBase):
    """Configuration for sanitizing sensitive data."""

    enabled: bool
    keys_to_sanitize: tuple[str, ...]
    sensitive_markers: tuple[str, ...]
    replacement: str

    __slots__ = ("enabled", "keys_to_sanitize", "sensitive_markers", "replacement")

    def __init__(
        self,
        *,
        enabled: bool = True,
        keys_to_sanitize: tuple[str, ...] | None = None,
        sensitive_markers: tuple[str, ...] | None = None,
        replacement: str | None = None,
    ) -> None:
        self.enabled = enabled
        self.keys_to_sanitize = keys_to_sanitize or DEFAULT_KEYS_TO_SANITIZE
        self.sensitive_markers = sensitive_markers or DEFAULT_SENSITIVE_MARKERS
        self.replacement = replacement or DEFAULT_REPLACEMENT

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SanitizationConfig:
        return cls(
            enabled=data.get("enabled", True),
            keys_to_sanitize=tuple(k.lower() for k in data.get("keys-to-sanitize", [])) or DEFAULT_KEYS_TO_SANITIZE,
            sensitive_markers=tuple(m.lower() for m in data.get("sensitive-markers", [])) or DEFAULT_SENSITIVE_MARKERS,
            replacement=data.get("replacement", DEFAULT_REPLACEMENT),
        )

    def update(self, *, enabled: bool | None = None) -> None:
        if enabled is not None:
            self.enabled = enabled


MAX_PAYLOAD_SIZE = 512
MAX_LINES = 10
MAX_WIDTH = 80


@dataclass(repr=False)
class TruncationConfig(DiffBase):
    """Configuration for truncating large output."""

    enabled: bool
    max_payload_size: int
    max_lines: int
    max_width: int

    __slots__ = ("enabled", "max_payload_size", "max_lines", "max_width")

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_payload_size: int = MAX_PAYLOAD_SIZE,
        max_lines: int = MAX_LINES,
        max_width: int = MAX_WIDTH,
    ) -> None:
        self.enabled = enabled
        self.max_payload_size = max_payload_size
        self.max_lines = max_lines
        self.max_width = max_width

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TruncationConfig:
        return cls(
            enabled=data.get("enabled", True),
            max_payload_size=data.get("max-payload-size", MAX_PAYLOAD_SIZE),
            max_lines=data.get("max-lines", MAX_LINES),
            max_width=data.get("max-width", MAX_WIDTH),
        )

    def update(self, *, enabled: bool | None = None) -> None:
        if enabled is not None:
            self.enabled = enabled


@dataclass(repr=False)
class OutputConfig(DiffBase):
    sanitization: SanitizationConfig
    truncation: TruncationConfig

    __slots__ = ("sanitization", "truncation")

    def __init__(
        self,
        *,
        sanitization: SanitizationConfig | None = None,
        truncation: TruncationConfig | None = None,
    ) -> None:
        self.sanitization = sanitization or SanitizationConfig()
        self.truncation = truncation or TruncationConfig()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OutputConfig:
        return cls(
            sanitization=SanitizationConfig.from_dict(data.get("sanitization", {})),
            truncation=TruncationConfig.from_dict(data.get("truncation", {})),
        )
