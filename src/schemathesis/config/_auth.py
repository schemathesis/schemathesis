from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve
from schemathesis.config._error import ConfigError
from schemathesis.core.validation import is_latin_1_encodable


@dataclass(repr=False)
class AuthConfig(DiffBase):
    basic: tuple[str, str] | None
    bearer: str | None
    openapi: dict[str, dict[str, str]] | None

    __slots__ = ("basic", "bearer", "openapi")

    def __init__(
        self,
        *,
        basic: dict[str, str] | None = None,
        bearer: str | None = None,
        openapi: dict[str, dict[str, str]] | None = None,
    ) -> None:
        if basic is not None:
            assert "username" in basic
            username = resolve(basic["username"], basic["username"])
            assert "password" in basic
            password = resolve(basic["password"], basic["password"])
            _validate_basic(username, password)
            self.basic = (username, password)
        else:
            self.basic = None
        self.bearer = resolve(bearer, bearer) if bearer else None
        self.openapi = (
            {
                key: {sub_key: resolve(sub_value, sub_value) for sub_key, sub_value in value.items()}
                for key, value in openapi.items()
            }
            if openapi
            else None
        )

    def update(self, *, basic: tuple[str, str] | None = None) -> None:
        if basic is not None:
            _validate_basic(*basic)
            self.basic = basic

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuthConfig:
        return cls(
            basic=data.get("basic"),
            bearer=data.get("bearer"),
            openapi=data.get("openapi"),
        )

    @property
    def is_defined(self) -> bool:
        return self.basic is not None or self.bearer is not None or self.openapi is not None


def _validate_basic(username: str, password: str) -> None:
    if not is_latin_1_encodable(username):
        raise ConfigError("Username should be latin-1 encodable.")
    if not is_latin_1_encodable(password):
        raise ConfigError("Password should be latin-1 encodable.")
