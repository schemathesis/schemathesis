from __future__ import annotations
from typing import TypedDict, Literal, Any

from .common import Reference, Operation as OperationBase
from .._jsonschema import Schema


class Specification(TypedDict):
    openapi: str


class Operation(OperationBase):
    requestBody: RequestBody | Reference
    parameters: list[Parameter | Reference]


class RequestBody(TypedDict):
    required: bool
    content: dict[str, MediaType]


class MediaType(TypedDict):
    schema: dict[str, Any] | Reference


# Describes a single operation parameter.
#
# Ref: https://github.com/OAI/OpenAPI-Specification/blob/main/versions/3.0.3.md#parameter-object
ParameterBase = TypedDict(
    "ParameterBase",
    {
        "in": Literal["query", "header", "path", "cookie"],
    },
)


class Parameter(ParameterBase):
    name: str
    required: bool
    deprecated: bool
    schema: dict[str, Any] | Reference
