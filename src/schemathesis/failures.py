from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union


class FailureContext:
    """Additional data specific to certain failure kind."""

    # Short description of what happened
    title: str
    # A longer one
    message: str
    type: str

    def unique_by_key(self, check_message: Optional[str]) -> Tuple[str, ...]:
        """A key to distinguish different failure contexts."""
        return (check_message or self.message,)


@dataclass(repr=False)
class ValidationErrorContext(FailureContext):
    """Additional information about JSON Schema validation errors."""

    validation_message: str
    schema_path: List[Union[str, int]]
    schema: Union[Dict[str, Any], bool]
    instance_path: List[Union[str, int]]
    instance: Union[None, bool, float, str, list, Dict[str, Any]]
    title: str = "Non-conforming response payload"
    message: str = "Response does not conform to the defined schema"
    type: str = "json_schema"

    def unique_by_key(self, check_message: Optional[str]) -> Tuple[str, ...]:
        # Deduplicate by JSON Schema path. All errors that happened on this sub-schema will be deduplicated
        return ("/".join(map(str, self.schema_path)),)


@dataclass(repr=False)
class JSONDecodeErrorContext(FailureContext):
    """Failed to decode JSON."""

    validation_message: str
    document: str
    position: int
    lineno: int
    colno: int
    title: str = "JSON deserialization error"
    message: str = "Response is not a valid JSON"
    type: str = "json_decode"

    def unique_by_key(self, check_message: Optional[str]) -> Tuple[str, ...]:
        # Treat different JSON decoding failures as the same issue
        # Payloads often contain dynamic data and distinguishing it by the error location still would not be sufficient
        # as it may be different on different dynamic payloads
        return (self.title,)


@dataclass(repr=False)
class ServerError(FailureContext):
    status_code: int
    title: str = "Internal server error"
    message: str = "Server got itself in trouble"
    type: str = "server_error"


@dataclass(repr=False)
class MissingContentType(FailureContext):
    """Content type header is missing."""

    media_types: List[str]
    title: str = "Missing Content-Type header"
    message: str = "Response is missing the `Content-Type` header"
    type: str = "missing_content_type"


@dataclass(repr=False)
class UndefinedContentType(FailureContext):
    """Response has Content-Type that is not defined in the schema."""

    content_type: str
    defined_content_types: List[str]
    title: str = "Undefined Content-Type"
    message: str = "Response has `Content-Type` that is not declared in the schema"
    type: str = "undefined_content_type"


@dataclass(repr=False)
class UndefinedStatusCode(FailureContext):
    """Response has a status code that is not defined in the schema."""

    # Response's status code
    status_code: int
    # Status codes as defined in schema
    defined_status_codes: List[str]
    # Defined status code with expanded wildcards
    allowed_status_codes: List[int]
    title: str = "Undefined status code"
    message: str = "Response has a status code that is not declared in the schema"
    type: str = "undefined_status_code"


@dataclass(repr=False)
class MissingHeaders(FailureContext):
    """Some required headers are missing."""

    missing_headers: List[str]
    title: str = "Missing required headers"
    message: str = "Response is missing headers required by the schema"
    type: str = "missing_headers"


@dataclass(repr=False)
class MalformedMediaType(FailureContext):
    """Media type name is malformed.

    Example: `application-json` instead of `application/json`
    """

    actual: str
    defined: str
    title: str = "Malformed media type name"
    message: str = "Media type name is not valid"
    type: str = "malformed_media_type"


@dataclass(repr=False)
class ResponseTimeExceeded(FailureContext):
    """Response took longer than expected."""

    elapsed: float
    deadline: int
    title: str = "Response time exceeded"
    message: str = "Response time exceeds the deadline"
    type: str = "response_time_exceeded"


@dataclass(repr=False)
class RequestTimeout(FailureContext):
    """Request took longer than timeout."""

    timeout: int
    title: str = "Request timeout"
    message: str = "The request timed out"
    type: str = "request_timeout"
