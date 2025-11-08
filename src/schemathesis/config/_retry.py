from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Iterable

from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve
from schemathesis.config._error import ConfigError

ALL_HTTP_METHODS = (
    "GET",
    "PUT",
    "POST",
    "DELETE",
    "OPTIONS",
    "HEAD",
    "PATCH",
    "TRACE",
)

SAFE_DEFAULT_METHODS = ("GET", "HEAD", "OPTIONS")


class RetryExceptionKind(str, enum.Enum):
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    READ = "read"

    @classmethod
    def from_value(cls, value: str) -> RetryExceptionKind:
        try:
            return cls(value.lower())
        except ValueError as exc:
            allowed = ", ".join(member.value for member in cls)
            raise ConfigError(f"Unsupported retry-on value: {value!r}. Allowed values: {allowed}") from exc


class RetryJitter(str, enum.Enum):
    NONE = "none"
    FULL = "full"

    @classmethod
    def from_value(cls, value: str) -> RetryJitter:
        try:
            return cls(value.lower())
        except ValueError as exc:
            allowed = ", ".join(member.value for member in cls)
            raise ConfigError(f"Unsupported jitter value: {value!r}. Allowed values: {allowed}") from exc


def _validate_methods(methods: Iterable[str]) -> tuple[str, ...]:
    normalized = []
    for method in methods:
        resolved = resolve(method)
        upper = resolved.upper()
        if upper not in ALL_HTTP_METHODS:
            allowed = ", ".join(ALL_HTTP_METHODS)
            raise ConfigError(f"Unsupported HTTP method in request-retry configuration: {resolved!r}. Allowed: {allowed}")
        normalized.append(upper)
    return tuple(dict.fromkeys(normalized))


def _validate_statuses(statuses: Iterable[int | str]) -> tuple[int, ...]:
    normalized = []
    for status in statuses:
        try:
            resolved = resolve(status)
            value = int(resolved)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"Request retry status codes must be integers. Got: {status!r}") from exc
        if value < 100 or value > 599:
            raise ConfigError(f"Invalid HTTP status code in retry configuration: {value}")
        normalized.append(value)
    return tuple(dict.fromkeys(normalized))


def _validate_attempts(value: int | float | str | None, *, default: int = 1) -> int:
    if value is None:
        return default
    try:
        resolved = resolve(value)
        attempts = int(resolved)
    except (TypeError, ValueError) as exc:
        raise ConfigError("request-retry.max-attempts must be an integer") from exc
    if attempts < 1:
        raise ConfigError("request-retry.max-attempts must be >= 1")
    return attempts


def _validate_float(name: str, value: Any, *, minimum: float | None = None) -> float | None:
    if value is None:
        return None
    try:
        number = float(resolve(value))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"request-retry.{name} must be a number") from exc
    if minimum is not None and number < minimum:
        raise ConfigError(f"request-retry.{name} must be >= {minimum}")
    return number


@dataclass(repr=False)
class RequestRetryConfig(DiffBase):
    enabled: bool
    max_attempts: int
    wait_initial: float
    backoff_multiplier: float
    max_wait: float | None
    jitter: RetryJitter
    retry_on_exceptions: tuple[RetryExceptionKind, ...]
    status_forcelist: tuple[int, ...]
    methods: tuple[str, ...]
    respect_retry_after: bool

    __slots__ = (
        "enabled",
        "max_attempts",
        "wait_initial",
        "backoff_multiplier",
        "max_wait",
        "jitter",
        "retry_on_exceptions",
        "status_forcelist",
        "methods",
        "respect_retry_after",
    )

    def __init__(
        self,
        *,
        enabled: bool = False,
        max_attempts: int = 1,
        wait_initial: float = 0.5,
        backoff_multiplier: float = 2.0,
        max_wait: float | None = 5.0,
        jitter: RetryJitter = RetryJitter.NONE,
        retry_on_exceptions: tuple[RetryExceptionKind, ...] | None = None,
        status_forcelist: tuple[int, ...] | None = None,
        methods: tuple[str, ...] | None = None,
        respect_retry_after: bool = True,
    ) -> None:
        self.enabled = enabled
        self.max_attempts = max_attempts
        self.wait_initial = wait_initial
        self.backoff_multiplier = backoff_multiplier
        self.max_wait = max_wait
        self.jitter = jitter
        self.retry_on_exceptions = retry_on_exceptions or ()
        self.status_forcelist = status_forcelist or ()
        self.methods = methods or SAFE_DEFAULT_METHODS
        self.respect_retry_after = respect_retry_after

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RequestRetryConfig:
        enabled = data.get("enabled")
        max_attempts = _validate_attempts(data.get("max-attempts"), default=3)
        wait_initial = _validate_float("wait-initial", data.get("wait-initial"), minimum=0)
        if wait_initial is None:
            wait_initial = 0.5
        elif wait_initial <= 0:
            raise ConfigError("request-retry.wait-initial must be > 0")
        backoff_multiplier = _validate_float("backoff-multiplier", data.get("backoff-multiplier"), minimum=1.0)
        if backoff_multiplier is None:
            backoff_multiplier = 2.0
        max_wait = _validate_float("max-wait", data.get("max-wait"), minimum=0)
        if max_wait is None:
            max_wait = 5.0
        jitter_value = data.get("jitter")
        jitter = RetryJitter.from_value(jitter_value) if jitter_value is not None else RetryJitter.NONE

        retry_on_raw = data.get("retry-on")
        if retry_on_raw is None:
            retry_on_raw = [RetryExceptionKind.CONNECTION.value, RetryExceptionKind.TIMEOUT.value]
        if not isinstance(retry_on_raw, list):
            raise ConfigError("request-retry.retry-on must be a list")
        retry_on = tuple(RetryExceptionKind.from_value(resolve(item)) for item in retry_on_raw)

        status_forcelist_raw = data.get("status-forcelist", [])
        if status_forcelist_raw is None:
            status_forcelist_raw = []
        if not isinstance(status_forcelist_raw, list):
            raise ConfigError("request-retry.status-forcelist must be a list")
        status_forcelist = _validate_statuses(status_forcelist_raw)

        methods_raw = data.get("methods")
        methods = _validate_methods(methods_raw) if methods_raw is not None else SAFE_DEFAULT_METHODS

        respect_retry_after = data.get("respect-retry-after")
        if respect_retry_after is None:
            respect_retry_after = True
        elif not isinstance(respect_retry_after, bool):
            raise ConfigError("request-retry.respect-retry-after must be a boolean")

        if enabled is None:
            enabled = False
        elif not isinstance(enabled, bool):
            raise ConfigError("request-retry.enabled must be a boolean")

        if (status_forcelist or retry_on) and max_attempts == 1:
            max_attempts = 2

        return cls(
            enabled=enabled,
            max_attempts=max_attempts,
            wait_initial=wait_initial,
            backoff_multiplier=backoff_multiplier,
            max_wait=max_wait,
            jitter=jitter,
            retry_on_exceptions=retry_on,
            status_forcelist=status_forcelist,
            methods=methods,
            respect_retry_after=respect_retry_after,
        )

    @property
    def is_enabled(self) -> bool:
        return self.enabled and (self.max_attempts > 1 or bool(self.status_forcelist))

    def allows_method(self, method: str) -> bool:
        return method.upper() in self.methods

    def should_retry_status(self, status_code: int, method: str) -> bool:
        if not self.is_enabled or not self.status_forcelist:
            return False
        return self.allows_method(method) and status_code in self.status_forcelist

    def has_exception_retry(self) -> bool:
        return self.is_enabled and bool(self.retry_on_exceptions)
