from __future__ import annotations

import enum


class SchemathesisWarning(str, enum.Enum):
    MISSING_AUTH = "missing_auth"
    MISSING_TEST_DATA = "missing_test_data"
    BASE_URL_MISMATCH = "base_url_mismatch"
    VALIDATION_MISMATCH = "validation_mismatch"
    MISSING_DESERIALIZER = "missing_deserializer"
    UNUSED_OPENAPI_AUTH = "unused_openapi_auth"
    UNSUPPORTED_REGEX = "unsupported_regex"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    CONSTANTS_EXTRACTION = "constants_extraction"
    UNMATCHED_FILTER = "unmatched_filter"
    UNRESOLVABLE_REFERENCE = "unresolvable_reference"

    @classmethod
    def from_str(cls, value: str) -> SchemathesisWarning:
        return {
            "missing_auth": cls.MISSING_AUTH,
            "missing_test_data": cls.MISSING_TEST_DATA,
            "base_url_mismatch": cls.BASE_URL_MISMATCH,
            "validation_mismatch": cls.VALIDATION_MISMATCH,
            "missing_deserializer": cls.MISSING_DESERIALIZER,
            "unused_openapi_auth": cls.UNUSED_OPENAPI_AUTH,
            "unsupported_regex": cls.UNSUPPORTED_REGEX,
            "method_not_allowed": cls.METHOD_NOT_ALLOWED,
            "constants_extraction": cls.CONSTANTS_EXTRACTION,
            "unmatched_filter": cls.UNMATCHED_FILTER,
            "unresolvable_reference": cls.UNRESOLVABLE_REFERENCE,
        }[value.lower()]
