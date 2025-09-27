from __future__ import annotations

from typing import Any, Mapping, TypedDict, Union

from typing_extensions import NotRequired

Reference = TypedDict("Reference", {"$ref": str})


class Operation(TypedDict):
    responses: Responses
    requestBody: NotRequired[RequestBodyOrRef]


class RequestBody(TypedDict):
    content: dict[str, MediaType]
    required: NotRequired[bool]


class MediaType(TypedDict):
    schema: Schema
    example: Any


class Example(TypedDict):
    value: NotRequired[Any]
    externalValue: NotRequired[str]


class Link(TypedDict):
    operationId: NotRequired[str]
    operationRef: NotRequired[str]
    parameters: NotRequired[dict[str, Any]]
    requestBody: NotRequired[Any]
    server: NotRequired[Any]


class Response(TypedDict):
    headers: NotRequired[dict[str, HeaderOrRef]]
    content: NotRequired[dict[str, MediaType]]
    links: NotRequired[dict[str, LinkOrRef]]


_ResponsesBase = Mapping[str, Union[Response, Reference]]


class Responses(_ResponsesBase):
    pass


class Header(TypedDict):
    required: NotRequired[bool]


_HeadersBase = Mapping[str, Union[Header, Reference]]


class Headers(_HeadersBase):
    pass


SchemaObject = TypedDict("SchemaObject", {"$ref": str})
Schema = Union[SchemaObject, bool]
RequestBodyOrRef = Union[RequestBody, Reference]
ExampleOrRef = Union[Example, Reference]
HeaderOrRef = Union[Header, Reference]
LinkOrRef = Union[Link, Reference]
ResponseOrRef = Union[Response, Reference]
