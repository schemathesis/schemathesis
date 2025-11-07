from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from schemathesis.specs.openapi import serialization


def serializer_v2(definitions: List[Dict[str, Any]]) -> Optional[Callable]:
    return serialization.serialize_swagger2_parameters(definitions)


def serializer_v3(definitions: List[Dict[str, Any]]) -> Optional[Callable]:
    return serialization.serialize_openapi3_parameters(definitions)
