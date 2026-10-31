from __future__ import annotations

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import Any

from jsonschema_rs import ValidationError

from schemathesis.core.errors import InvalidSchema
from schemathesis.specs.openapi.definitions import (
    OPENAPI_30_VALIDATOR,
    OPENAPI_31_VALIDATOR,
    OPENAPI_32_VALIDATOR,
    SWAGGER_20_VALIDATOR,
)


def ensure_object(value: object, label: str) -> None:
    """Reject a schema node that must be an object but carries some other JSON value."""
    if not isinstance(value, dict):
        raise InvalidSchema(f"{label} must be an object")


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


def validate_v3_0(raw_schema: Mapping[str, Any]) -> None:
    with _ignore_value_error():
        OPENAPI_30_VALIDATOR.validate(raw_schema)


def validate_v3_1(raw_schema: Mapping[str, Any]) -> None:
    with _ignore_value_error():
        OPENAPI_31_VALIDATOR.validate(raw_schema)


def validate_v3_2(raw_schema: Mapping[str, Any]) -> None:
    with _ignore_value_error():
        OPENAPI_32_VALIDATOR.validate(raw_schema)
