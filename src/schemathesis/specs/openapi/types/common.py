"""Common type definitions shared across OpenAPI versions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeAlias

from typing_extensions import NotRequired, TypedDict

Reference = TypedDict("Reference", {"$ref": str})
"""JSON Reference object with $ref key."""

_SecurityTypeKey = TypedDict("_SecurityTypeKey", {"x-original-security-type": NotRequired[str]})
"""Type for x-original-security-type extension added by Schemathesis."""

SchemaOrRef: TypeAlias = Mapping[str, Any] | Reference
"""Schema definition that may be a reference or inline object."""

# A single security requirement: maps scheme name to required scope list (empty list for non-OAuth).
SecurityRequirement: TypeAlias = dict[str, list[str]]

# Operation-level security: a list of requirement objects (logical OR across entries, AND within).
OperationSecurity: TypeAlias = list[SecurityRequirement]
