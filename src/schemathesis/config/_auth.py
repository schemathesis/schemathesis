from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve
from schemathesis.config._error import ConfigError
from schemathesis.core.validation import is_latin_1_encodable


@dataclass(repr=False)
class AuthConfig(DiffBase):
    basic: dict[str, str] | None
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
        self.basic = {key: resolve(value, value) for key, value in basic.items()} if basic else None
        if self.basic:
            if not is_latin_1_encodable(self.basic["username"]):
                raise ConfigError("Username should be latin-1 encodable.")
            if not is_latin_1_encodable(self.basic["password"]):
                raise ConfigError("Password should be latin-1 encodable.")
        self.bearer = resolve(bearer, bearer) if bearer else None
        self.openapi = (
            {
                key: {sub_key: resolve(sub_value, sub_value) for sub_key, sub_value in value.items()}
                for key, value in openapi.items()
            }
            if openapi
            else None
        )

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
