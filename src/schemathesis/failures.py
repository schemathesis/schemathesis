from typing import Any, Dict, List, Union

import attr


@attr.s(slots=True, repr=False)  # pragma: no mutate
class FailureContext:
    """Additional data specific to certain failure kind."""

    # Short description of what happened
    title: str
    # A longer one
    message: str
    type: str


@attr.s(slots=True, repr=False)
class ValidationErrorContext(FailureContext):
    """Additional information about JSON Schema validation errors."""

    validation_message: str = attr.ib()
    schema_path: List[Union[str, int]] = attr.ib()
    schema: Union[Dict[str, Any], bool] = attr.ib()
    instance_path: List[Union[str, int]] = attr.ib()
    instance: Union[None, bool, float, str, list, Dict[str, Any]] = attr.ib()
    title: str = attr.ib(default="Non-conforming response payload")
    message: str = attr.ib(default="Response does not conform to the defined schema")
    type: str = attr.ib(default="json_schema")


@attr.s(slots=True, repr=False)
class JSONDecodeErrorContext(FailureContext):
    """Failed to decode JSON."""

    validation_message: str = attr.ib()
    document: str = attr.ib()
    position: int = attr.ib()
    lineno: int = attr.ib()
    colno: int = attr.ib()
    title: str = attr.ib(default="JSON deserialization error")
    message: str = attr.ib(default="Response is not a valid JSON")
    type: str = attr.ib(default="json_decode")


@attr.s(slots=True, repr=False)
class ServerError(FailureContext):
    status_code: int = attr.ib()
    title: str = attr.ib(default="Internal server error")
    message: str = attr.ib(default="Server got itself in trouble")
    type: str = attr.ib(default="server_error")


@attr.s(slots=True, repr=False)
class MissingContentType(FailureContext):
    """Content type header is missing."""

    media_types: List[str] = attr.ib()
    title: str = attr.ib(default="Missing Content-Type header")
    message: str = attr.ib(default="Response is missing the `Content-Type` header")
    type: str = attr.ib(default="missing_content_type")


@attr.s(slots=True, repr=False)
class UndefinedContentType(FailureContext):
    """Response has Content-Type that is not defined in the schema."""

    content_type: str = attr.ib()
    defined_content_types: List[str] = attr.ib()
    title: str = attr.ib(default="Undefined Content-Type")
    message: str = attr.ib(default="Response has `Content-Type` that is not declared in the schema")
    type: str = attr.ib(default="undefined_content_type")


@attr.s(slots=True, repr=False)
class UndefinedStatusCode(FailureContext):
    """Response has a status code that is not defined in the schema."""

    # Response's status code
    status_code: int = attr.ib()
    # Status codes as defined in schema
    defined_status_codes: List[str] = attr.ib()
    # Defined status code with expanded wildcards
    allowed_status_codes: List[int] = attr.ib()
    title: str = attr.ib(default="Undefined status code")
    message: str = attr.ib(default="Response has a status code that is not declared in the schema")
    type: str = attr.ib(default="undefined_status_code")


@attr.s(slots=True, repr=False)
class MissingHeaders(FailureContext):
    """Some required headers are missing."""

    missing_headers: List[str] = attr.ib()
    title: str = attr.ib(default="Missing required headers")
    message: str = attr.ib(default="Response is missing headers required by the schema")
    type: str = attr.ib(default="missing_headers")


@attr.s(slots=True, repr=False)
class MalformedMediaType(FailureContext):
    """Media type name is malformed.

    Example: `application-json` instead of `application/json`
    """

    actual: str = attr.ib()
    defined: str = attr.ib()
    title: str = attr.ib(default="Malformed media type name")
    message: str = attr.ib(default="Media type name is not valid")
    type: str = attr.ib(default="malformed_media_type")


@attr.s(slots=True, repr=False)
class ResponseTimeExceeded(FailureContext):
    """Response took longer than expected."""

    elapsed: float = attr.ib()
    deadline: int = attr.ib()
    title: str = attr.ib(default="Response time exceeded")
    message: str = attr.ib(default="Response time exceeds the deadline")
    type: str = attr.ib(default="response_time_exceeded")


@attr.s(slots=True, repr=False)
class RequestTimeout(FailureContext):
    """Request took longer than timeout."""

    timeout: int = attr.ib()
    title: str = attr.ib(default="Request timeout")
    message: str = attr.ib(default="The request timed out")
    type: str = attr.ib(default="request_timeout")
