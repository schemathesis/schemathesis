from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping, Protocol, Union

if TYPE_CHECKING:
    from jsonschema.protocols import Validator

    from schemathesis.core.compat import RefResolver
    from schemathesis.core.jsonschema.types import JsonSchema

IterResponseExamples = Callable[[Mapping[str, Any], str], Iterator[tuple[str, object]]]
ExtractResponseSchema = Callable[[Mapping[str, Any], "RefResolver", str, str], Union["JsonSchema", None]]
ExtractHeaderSchema = Callable[[Mapping[str, Any], "RefResolver", str, str], "JsonSchema"]


class SpecificationAdapter(Protocol):
    """Protocol for abstracting over different API specification formats (OpenAPI 2/3, etc.)."""

    # Keyword used to mark nullable fields (e.g., "x-nullable" in OpenAPI 2.0, "nullable" in 3.x)
    nullable_keyword: str
    # Keyword used for required / optional headers. Open API 2.0 does not expect `required` there
    header_required_keyword: str
    # Keyword for Open API links
    links_keyword: str
    # Function to extract examples from response definition
    iter_response_examples: IterResponseExamples
    # Function to extract response schema from specification
    extract_response_schema: ExtractResponseSchema
    # Function to extract header schema from specification
    extract_header_schema: ExtractHeaderSchema
    # JSON Schema validator class appropriate for this specification version
    jsonschema_validator_cls: type[Validator]
