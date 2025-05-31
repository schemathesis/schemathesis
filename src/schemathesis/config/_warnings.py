from __future__ import annotations

import enum

from schemathesis.config._env import resolve


class SchemathesisWarning(str, enum.Enum):
    MISSING_AUTH = "missing_auth"
    MISSING_TEST_DATA = "missing_test_data"
    VALIDATION_MISMATCH = "validation_mismatch"

    @classmethod
    def from_str(cls, value: str) -> SchemathesisWarning:
        return {
            "missing_auth": cls.MISSING_AUTH,
            "missing_test_data": cls.MISSING_TEST_DATA,
            "validation_mismatch": cls.VALIDATION_MISMATCH,
        }[value.lower()]


def resolve_warnings(value: bool | list[str] | None) -> bool | list[SchemathesisWarning] | None:
    if isinstance(value, list):
        return [SchemathesisWarning.from_str(resolve(item)) for item in value]
    return value
