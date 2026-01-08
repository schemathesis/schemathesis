from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from packaging import version

from schemathesis.specs.openapi.definitions import (
    OPENAPI_30_VALIDATOR,
    OPENAPI_31_VALIDATOR,
    OPENAPI_32_VALIDATOR,
    SWAGGER_20_VALIDATOR,
)

_V3_1 = version.parse("3.1")
_V3_2 = version.parse("3.2")


def validate_v2(raw_schema: Mapping[str, Any]) -> None:
    SWAGGER_20_VALIDATOR.validate(raw_schema)


def validate_v3(raw_schema: Mapping[str, Any]) -> None:
    openapi_version = str(raw_schema.get("openapi", ""))
    parsed_version = version.parse(openapi_version)
    if parsed_version >= _V3_2:
        OPENAPI_32_VALIDATOR.validate(raw_schema)
    elif parsed_version >= _V3_1:
        OPENAPI_31_VALIDATOR.validate(raw_schema)
    else:
        OPENAPI_30_VALIDATOR.validate(raw_schema)
