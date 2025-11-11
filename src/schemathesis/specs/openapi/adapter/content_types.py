from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.schemas import APIOperation


def response_content_types_v2(operation: APIOperation, response: Response) -> list[str]:
    produces = operation.definition.raw.get("produces")
    if produces:
        return produces
    return operation.schema.raw_schema.get("produces", [])


def request_content_types_v2(operation: APIOperation) -> list[str]:
    consumes = operation.definition.raw.get("consumes")
    if consumes:
        return consumes
    return operation.schema.raw_schema.get("consumes", [])


def response_content_types_v3(operation: APIOperation, response: Response) -> list[str]:
    definition = operation.responses.find_by_status_code(response.status_code)
    if definition is None:
        return []
    return list(definition.definition.get("content", {}).keys())


def request_content_types_v3(operation: APIOperation) -> list[str]:
    return [body.media_type for body in operation.body]


def default_media_types_v2(raw_schema: Mapping[str, Any]) -> list[str]:
    consumes = raw_schema.get("consumes")
    if isinstance(consumes, list):
        return consumes
    return []


def default_media_types_v3(raw_schema: Mapping[str, Any]) -> list[str]:
    return []
