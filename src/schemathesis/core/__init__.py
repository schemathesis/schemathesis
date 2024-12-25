from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

SCHEMATHESIS_TEST_CASE_HEADER = "X-Schemathesis-TestCaseId"


class NotSet:
    pass


NOT_SET = NotSet()


@dataclass
class Specification:
    kind: SpecificationKind
    version: str

    @classmethod
    def openapi(cls, version: str) -> Specification:
        return cls(kind=SpecificationKind.OPENAPI, version=version)

    @classmethod
    def graphql(cls, version: str) -> Specification:
        return cls(kind=SpecificationKind.GRAPHQL, version=version)

    @property
    def name(self) -> str:
        name = {SpecificationKind.GRAPHQL: "GraphQL", SpecificationKind.OPENAPI: "Open API"}[self.kind]
        return f"{name} {self.version}".strip()

    def asdict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind.value, "version": self.version}


class SpecificationKind(str, enum.Enum):
    """Specification of the given schema."""

    OPENAPI = "openapi"
    GRAPHQL = "graphql"


def string_to_boolean(value: str) -> str | bool:
    if value.lower() in ("y", "yes", "t", "true", "on", "1"):
        return True
    if value.lower() in ("n", "no", "f", "false", "off", "0"):
        return False
    return value
