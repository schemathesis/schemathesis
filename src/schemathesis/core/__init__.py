from __future__ import annotations

import enum
from dataclasses import dataclass

SCHEMATHESIS_TEST_CASE_HEADER = "X-Schemathesis-TestCaseId"


class NotSet: ...


NOT_SET = NotSet()


class SpecificationFeature(str, enum.Enum):
    """Features that Schemathesis can provide for different specifications."""

    STATEFUL_TESTING = "stateful_testing"


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

    def supports_feature(self, feature: SpecificationFeature) -> bool:
        """Check if Schemathesis supports a given feature for this specification."""
        if self.kind == SpecificationKind.OPENAPI:
            return feature in {SpecificationFeature.STATEFUL_TESTING}
        return False


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
