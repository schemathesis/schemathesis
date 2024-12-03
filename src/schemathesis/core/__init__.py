from __future__ import annotations

import enum


class NotSet:
    pass


NOT_SET = NotSet()


class Specification(str, enum.Enum):
    """Specification of the given schema."""

    OPENAPI = "openapi"
    GRAPHQL = "graphql"


def string_to_boolean(value: str) -> str | bool:
    if value.lower() in ("y", "yes", "t", "true", "on", "1"):
        return True
    if value.lower() in ("n", "no", "f", "false", "off", "0"):
        return False
    return value
