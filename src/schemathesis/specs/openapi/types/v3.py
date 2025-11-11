"""OpenAPI 3.0 and 3.1 type definitions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeAlias

from typing_extensions import NotRequired, TypedDict

from schemathesis.specs.openapi.types.common import Reference, SchemaOrRef, _SecurityTypeKey


class Example(TypedDict):
    value: NotRequired[Any]
    externalValue: NotRequired[str]


class MediaType(TypedDict):
    schema: SchemaOrRef
    example: NotRequired[Any]


class Link(TypedDict):
    operationId: NotRequired[str]
    operationRef: NotRequired[str]
    parameters: NotRequired[dict[str, Any]]
    requestBody: NotRequired[Any]
    server: NotRequired[Any]


class Header(TypedDict):
    required: NotRequired[bool]


class Response(TypedDict):
    headers: NotRequired[dict[str, HeaderOrRef]]
    content: NotRequired[dict[str, MediaType]]
    links: NotRequired[dict[str, LinkOrRef]]


class RequestBody(TypedDict):
    content: dict[str, MediaType]
    required: NotRequired[bool]


_ResponsesBase = Mapping[str, Response | Reference]


class Responses(_ResponsesBase):
    pass


_HeadersBase = Mapping[str, Header | Reference]


class Headers(_HeadersBase):
    pass


ExampleOrRef: TypeAlias = Example | Reference
"""Example definition or reference."""

HeaderOrRef: TypeAlias = Header | Reference
"""Header definition or reference."""

LinkOrRef: TypeAlias = Link | Reference
"""Link definition or reference."""

ResponseOrRef: TypeAlias = Response | Reference
"""Response definition or reference."""

RequestBodyOrRef: TypeAlias = RequestBody | Reference
"""Request body definition or reference."""


class ParameterWithSchema(TypedDict):
    """OpenAPI 3.0/3.1 parameter with schema."""

    name: str
    description: NotRequired[str]
    required: NotRequired[bool]
    deprecated: NotRequired[bool]
    allowEmptyValue: NotRequired[bool]
    schema: SchemaOrRef
    style: NotRequired[str]
    explode: NotRequired[bool]
    allowReserved: NotRequired[bool]
    example: NotRequired[Any]
    examples: NotRequired[dict[str, ExampleOrRef]]


_ParameterIn = TypedDict("_ParameterIn", {"in": Literal["path", "query", "header", "cookie"]})


class ParameterWithSchemaAndIn(ParameterWithSchema, _ParameterIn):
    """Parameter with schema and 'in' field."""

    pass


class ParameterWithContent(TypedDict):
    """OpenAPI 3.0/3.1 parameter with content."""

    name: str
    description: NotRequired[str]
    required: NotRequired[bool]
    deprecated: NotRequired[bool]
    content: dict[str, MediaType]


class ParameterWithContentAndIn(ParameterWithContent, _ParameterIn):
    """Parameter with content and 'in' field."""

    pass


Parameter: TypeAlias = ParameterWithSchemaAndIn | ParameterWithContentAndIn | Reference
"""OpenAPI 3.x parameter (with schema, with content, or reference)."""


class Operation(TypedDict):
    responses: Responses
    requestBody: NotRequired[RequestBodyOrRef]
    parameters: NotRequired[list[Parameter]]


# Security parameter types
class SecurityParameter(ParameterWithSchema, _SecurityTypeKey):
    """OpenAPI 3.x synthetic security parameter.

    Created from security schemes (apiKey or http auth).
    Follows the same structure as ParameterWithSchema since v3 uses nested schema.
    """

    pass
