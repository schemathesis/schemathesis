from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve


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
