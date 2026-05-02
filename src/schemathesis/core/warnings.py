from __future__ import annotations

import enum


class SchemathesisWarning(str, enum.Enum):
    MISSING_AUTH = "missing_auth"
    MISSING_TEST_DATA = "missing_test_data"
    VALIDATION_MISMATCH = "validation_mismatch"
    MISSING_DESERIALIZER = "missing_deserializer"
    UNUSED_OPENAPI_AUTH = "unused_openapi_auth"
    UNSUPPORTED_REGEX = "unsupported_regex"
    METHOD_NOT_ALLOWED = "method_not_allowed"

    @classmethod
    def from_str(cls, value: str) -> SchemathesisWarning:
        return {
            "missing_auth": cls.MISSING_AUTH,
            "missing_test_data": cls.MISSING_TEST_DATA,
            "validation_mismatch": cls.VALIDATION_MISMATCH,
            "missing_deserializer": cls.MISSING_DESERIALIZER,
            "unused_openapi_auth": cls.UNUSED_OPENAPI_AUTH,
            "unsupported_regex": cls.UNSUPPORTED_REGEX,
            "method_not_allowed": cls.METHOD_NOT_ALLOWED,
        }[value.lower()]
