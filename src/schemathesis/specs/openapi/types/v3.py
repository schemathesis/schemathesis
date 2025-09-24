from __future__ import annotations

from typing import Any, TypedDict, Union

from typing_extensions import NotRequired


class Operation(TypedDict):
    responses: dict[str, Response | Reference]
    requestBody: RequestBodyOrRef


class RequestBody(TypedDict):
    content: dict[str, MediaType]
    required: NotRequired[bool]


class MediaType(TypedDict):
    schema: Schema
    example: Any


class Response(TypedDict):
    pass


SchemaObject = TypedDict("SchemaObject", {"$ref": str})
Schema = Union[SchemaObject, bool]
Reference = TypedDict("Reference", {"$ref": str})
RequestBodyOrRef = Union[RequestBody, Reference]
