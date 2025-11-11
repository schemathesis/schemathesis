"""Swagger 2.0 type definitions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeAlias

from typing_extensions import NotRequired, TypedDict

from schemathesis.specs.openapi.types.common import Reference, SchemaOrRef, _SecurityTypeKey


class BodyParameter(TypedDict):
    """Swagger 2.0 body parameter."""

    name: str
    description: NotRequired[str]
    required: NotRequired[bool]
    schema: SchemaOrRef


_BodyParameterIn = TypedDict("_BodyParameterIn", {"in": Literal["body"]})


class BodyParameterWithIn(BodyParameter, _BodyParameterIn):
    """Body parameter with 'in' field."""

    pass


class NonBodyParameter(TypedDict):
    """Swagger 2.0 non-body parameter (path/query/header/formData)."""

    name: str
    description: NotRequired[str]
    required: NotRequired[bool]
    type: NotRequired[Literal["string", "number", "integer", "boolean", "array", "file"]]
    format: NotRequired[str]
    items: NotRequired[SchemaOrRef]
    collectionFormat: NotRequired[Literal["csv", "ssv", "tsv", "pipes"]]
    default: NotRequired[Any]
    maximum: NotRequired[float]
    exclusiveMaximum: NotRequired[bool]
    minimum: NotRequired[float]
    exclusiveMinimum: NotRequired[bool]
    maxLength: NotRequired[int]
    minLength: NotRequired[int]
    pattern: NotRequired[str]
    maxItems: NotRequired[int]
    minItems: NotRequired[int]
    uniqueItems: NotRequired[bool]
    enum: NotRequired[list[Any]]
    multipleOf: NotRequired[float]


_NonBodyParameterIn = TypedDict("_NonBodyParameterIn", {"in": Literal["path", "query", "header", "formData"]})


class NonBodyParameterWithIn(NonBodyParameter, _NonBodyParameterIn):
    """Non-body parameter with 'in' field."""

    pass


Parameter: TypeAlias = BodyParameterWithIn | NonBodyParameterWithIn | Reference
"""Swagger 2.0 parameter (body, non-body, or reference)."""


class Header(TypedDict):
    """Swagger 2.0 response header."""

    type: Literal["string", "number", "integer", "boolean", "array"]
    description: NotRequired[str]
    format: NotRequired[str]
    items: NotRequired[SchemaOrRef]
    collectionFormat: NotRequired[Literal["csv", "ssv", "tsv", "pipes"]]
    default: NotRequired[Any]
    maximum: NotRequired[float]
    exclusiveMaximum: NotRequired[bool]
    minimum: NotRequired[float]
    exclusiveMinimum: NotRequired[bool]
    maxLength: NotRequired[int]
    minLength: NotRequired[int]
    pattern: NotRequired[str]
    maxItems: NotRequired[int]
    minItems: NotRequired[int]
    uniqueItems: NotRequired[bool]
    enum: NotRequired[list[Any]]
    multipleOf: NotRequired[float]


HeaderOrRef: TypeAlias = Header | Reference
"""Header definition or reference."""

Headers: TypeAlias = Mapping[str, HeaderOrRef]
"""Mapping from header name to header definition."""


class Response(TypedDict):
    """Swagger 2.0 response object."""

    description: str
    schema: NotRequired[SchemaOrRef]
    headers: NotRequired[dict[str, HeaderOrRef]]
    examples: NotRequired[dict[str, Any]]


ResponseOrRef: TypeAlias = Response | Reference
"""Response definition or reference."""

Responses: TypeAlias = Mapping[str, ResponseOrRef]
"""Mapping from status code to response definition."""


class Operation(TypedDict):
    responses: Responses
    parameters: NotRequired[list[Parameter]]
    consumes: NotRequired[list[str]]
    produces: NotRequired[list[str]]


# Security parameter types
class SecurityParameter(NonBodyParameter, _SecurityTypeKey):
    """Swagger 2.0 synthetic security parameter.

    Created from security definitions (apiKey or basic auth).
    Follows the same structure as NonBodyParameter since v2 has inline types.
    """

    pass
