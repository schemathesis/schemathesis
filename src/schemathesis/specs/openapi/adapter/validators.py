from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from schemathesis.specs.openapi.definitions import OPENAPI_30_VALIDATOR, OPENAPI_31_VALIDATOR, SWAGGER_20_VALIDATOR


def validate_v2(raw_schema: Mapping[str, Any]) -> None:
    SWAGGER_20_VALIDATOR.validate(raw_schema)


def validate_v3(raw_schema: Mapping[str, Any]) -> None:
    version = str(raw_schema.get("openapi", ""))
    if version.startswith("3.1"):
        OPENAPI_31_VALIDATOR.validate(raw_schema)
    else:
        OPENAPI_30_VALIDATOR.validate(raw_schema)
