from __future__ import annotations

from collections.abc import Callable
from typing import Any

from schemathesis.specs.openapi import serialization


def serializer_v2(definitions: list[dict[str, Any]]) -> Callable | None:
    return serialization.serialize_swagger2_parameters(definitions)


def serializer_v3(definitions: list[dict[str, Any]]) -> Callable | None:
    return serialization.serialize_openapi3_parameters(definitions)
