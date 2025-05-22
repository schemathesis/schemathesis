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

    __slots__ = ("basic",)

    def __init__(
        self,
        *,
        basic: dict[str, str] | None = None,
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

    def update(self, *, basic: tuple[str, str] | None = None) -> None:
        if basic is not None:
            _validate_basic(*basic)
            self.basic = basic

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuthConfig:
        return cls(basic=data.get("basic"))

    @property
    def is_defined(self) -> bool:
        return self.basic is not None


def _validate_basic(username: str, password: str) -> None:
    if not is_latin_1_encodable(username):
        raise ConfigError("Username should be latin-1 encodable.")
    if not is_latin_1_encodable(password):
        raise ConfigError("Password should be latin-1 encodable.")
