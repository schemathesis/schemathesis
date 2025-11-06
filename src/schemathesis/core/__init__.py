from __future__ import annotations

import enum
from dataclasses import dataclass

SCHEMATHESIS_TEST_CASE_HEADER = "X-Schemathesis-TestCaseId"
HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER = ":memory:"
INTERNAL_BUFFER_SIZE = 32 * 1024
DEFAULT_STATEFUL_STEP_COUNT = 6
INJECTED_PATH_PARAMETER_KEY = "x-schemathesis-injected"


class NotSet: ...


NOT_SET = NotSet()


class SpecificationFeature(str, enum.Enum):
    """Features that Schemathesis can provide for different specifications."""

    SCHEMA_ANALYSIS = "schema_analysis"
    STATEFUL_TESTING = "stateful_testing"
    COVERAGE = "coverage_tests"
    EXAMPLES = "example_tests"


@dataclass
class Specification:
    kind: SpecificationKind
    version: str

    __slots__ = ("kind", "version")

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
            return feature in {
                SpecificationFeature.SCHEMA_ANALYSIS,
                SpecificationFeature.STATEFUL_TESTING,
                SpecificationFeature.COVERAGE,
                SpecificationFeature.EXAMPLES,
            }
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
