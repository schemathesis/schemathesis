from __future__ import annotations

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import Any

from jsonschema_rs import ValidationError
from packaging import version

from schemathesis.specs.openapi.definitions import (
    OPENAPI_30_VALIDATOR,
    OPENAPI_31_VALIDATOR,
    OPENAPI_32_VALIDATOR,
    SWAGGER_20_VALIDATOR,
)

_V3_1 = version.parse("3.1")
_V3_2 = version.parse("3.2")


@contextmanager
def _ignore_value_error() -> Generator:
    try:
        yield
    except ValidationError:
        raise
    except ValueError:
        # `ValidationError` is a subclass of `ValueError`, but `ValueError` is possible when dict key is not a string
        # In such a case we skip validation completely
        pass


def validate_v2(raw_schema: Mapping[str, Any]) -> None:
    with _ignore_value_error():
        SWAGGER_20_VALIDATOR.validate(raw_schema)


def validate_v3(raw_schema: Mapping[str, Any]) -> None:
    openapi_version = str(raw_schema.get("openapi", ""))
    parsed_version = version.parse(openapi_version)
    with _ignore_value_error():
        if parsed_version >= _V3_2:
            OPENAPI_32_VALIDATOR.validate(raw_schema)
        elif parsed_version >= _V3_1:
            OPENAPI_31_VALIDATOR.validate(raw_schema)
        else:
            OPENAPI_30_VALIDATOR.validate(raw_schema)
