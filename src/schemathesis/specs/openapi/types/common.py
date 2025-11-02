"""Common type definitions shared across OpenAPI versions."""

from __future__ import annotations

from typing import Any, Mapping, Union

from typing_extensions import NotRequired, TypeAlias, TypedDict

Reference = TypedDict("Reference", {"$ref": str})
"""JSON Reference object with $ref key."""

SchemaObject = TypedDict("SchemaObject", {"$ref": str})
"""Schema object that may be a reference."""

_SecurityTypeKey = TypedDict("_SecurityTypeKey", {"x-original-security-type": NotRequired[str]})
"""Type for x-original-security-type extension added by Schemathesis."""

# Type aliases for commonly used patterns
Schema: TypeAlias = Union[SchemaObject, bool]
"""JSON Schema can be an object or boolean."""

SchemaOrRef: TypeAlias = Union[Mapping[str, Any], Reference]
"""Schema definition that may be a reference or inline object."""
