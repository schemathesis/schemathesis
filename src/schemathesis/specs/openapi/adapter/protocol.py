from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol, Union

if TYPE_CHECKING:
    from schemathesis.core.compat import RefResolver
    from schemathesis.core.jsonschema.types import JsonSchema

ExtractResponseSchema = Callable[[Mapping[str, Any], "RefResolver", str, str], Union["JsonSchema", None]]


class SpecificationAdapter(Protocol):
    nullable_keyword: str
    extract_response_schema: ExtractResponseSchema
