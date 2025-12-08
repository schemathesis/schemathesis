from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import (
    TYPE_CHECKING,
    Any,
    Protocol,
    Union,
)

if TYPE_CHECKING:
    from jsonschema.protocols import Validator

    from schemathesis.core.adapter import OperationParameter
    from schemathesis.core.compat import RefResolver
    from schemathesis.core.jsonschema.bundler import Bundle, BundleCache, Bundler
    from schemathesis.core.jsonschema.types import JsonSchema
    from schemathesis.core.transport import Response
    from schemathesis.schemas import APIOperation

IterResponseExamples = Callable[[Mapping[str, Any], str], Iterator[tuple[str, object]]]
ExtractRawResponseSchema = Callable[[Mapping[str, Any]], Union["JsonSchema", None]]
ExtractResponseSchema = Callable[[Mapping[str, Any], "RefResolver", str, str], Union["Bundle", None]]
PrepareResponseMediaTypeSchema = Callable[["JsonSchema", "RefResolver", str, str], "Bundle"]
ExtractHeaderSchema = Callable[[Mapping[str, Any], "RefResolver", str, str], "Bundle"]
GetDefaultResponseMediaType = Callable[[Mapping[str, Any]], str | None]
ResolveResponseMediaType = Callable[[Mapping[str, Any], str | None], str | None]
ExtractSchemaForMediaType = Callable[[Mapping[str, Any], str | None, "RefResolver", str, str], Union["Bundle", None]]
ExtractParameterSchema = Callable[[Mapping[str, Any]], "JsonSchema"]
ExtractSecurityParameters = Callable[
    [Mapping[str, Any], Mapping[str, Any], "RefResolver"],
    Iterator[Mapping[str, Any]],
]
PrepareMultipart = Callable[
    ["APIOperation", dict[str, Any], dict[str, str] | None],
    tuple[list[tuple[str, Any]] | None, dict[str, Any] | None],
]
GetResponseContentTypes = Callable[["APIOperation", "Response"], list[str]]
GetRequestPayloadContentTypes = Callable[["APIOperation"], list[str]]
GetDefaultMediaTypes = Callable[[Mapping[str, Any]], list[str]]
GetBasePath = Callable[[Mapping[str, Any]], str]
ValidateSchema = Callable[[Mapping[str, Any]], None]
GetParameterSerializer = Callable[[list[dict[str, Any]]], Callable | None]
IterParameters = Callable[
    [
        Mapping[str, Any],
        Sequence[Mapping[str, Any]],
        list[str],
        "RefResolver",
        "SpecificationAdapter",
        "Bundler",
        "BundleCache",
    ],
    Iterable["OperationParameter"],
]
BuildPathParameter = Callable[[Mapping[str, Any]], "OperationParameter"]
ExtractSecurityDefinitions = Callable[[Mapping[str, Any], "RefResolver"], Mapping[str, Mapping[str, Any]]]


class SpecificationAdapter(Protocol):
    """Protocol for abstracting over different API specification formats (OpenAPI 2/3, etc.)."""

    # Keyword used to mark nullable fields (e.g., "x-nullable" in OpenAPI 2.0, "nullable" in 3.x)
    nullable_keyword: str
    # Keyword used for required / optional headers. Open API 2.0 does not expect `required` there
    header_required_keyword: str
    # Keyword for Open API links
    links_keyword: str
    # Keyword for a single example
    example_keyword: str
    # Keyword for examples container
    examples_container_keyword: str

    # Function to extract schema from parameter definition
    extract_parameter_schema: ExtractParameterSchema
    # Function to extract response schema from specification
    extract_raw_response_schema: ExtractRawResponseSchema
    extract_response_schema: ExtractResponseSchema
    prepare_response_media_type_schema: PrepareResponseMediaTypeSchema
    # Functions for handling multiple media types in responses
    get_default_response_media_type: GetDefaultResponseMediaType
    resolve_response_media_type: ResolveResponseMediaType
    extract_schema_for_media_type: ExtractSchemaForMediaType
    # Function to extract header schema from specification
    extract_header_schema: ExtractHeaderSchema
    # Function to iterate over API operation parameters
    iter_parameters: IterParameters
    # Function to create a new path parameter
    build_path_parameter: BuildPathParameter
    # Function to extract examples from response definition
    iter_response_examples: IterResponseExamples
    # Function to extract security parameters for an API operation
    extract_security_parameters: ExtractSecurityParameters
    prepare_multipart: PrepareMultipart
    get_response_content_types: GetResponseContentTypes
    get_request_payload_content_types: GetRequestPayloadContentTypes
    get_default_media_types: GetDefaultMediaTypes
    get_base_path: GetBasePath
    validate_schema: ValidateSchema
    get_parameter_serializer: GetParameterSerializer
    # Function to extract security scheme definitions from the schema with resolved references
    extract_security_definitions: ExtractSecurityDefinitions

    # JSON Schema validator class appropriate for this specification version
    jsonschema_validator_cls: type[Validator]
