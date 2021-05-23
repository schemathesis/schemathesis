from typing import Any, Dict, List, Union

import attr


@attr.s(slots=True, repr=False)  # pragma: no mutate
class FailureContext:
    """Additional data specific to certain failure kind."""


@attr.s(slots=True, repr=False)
class ValidationErrorContext(FailureContext):
    """Additional information about JSON Schema validation errors."""

    validation_message: str = attr.ib()
    schema_path: List[Union[str, int]] = attr.ib()
    schema: Union[Dict[str, Any], bool] = attr.ib()
    instance_path: List[Union[str, int]] = attr.ib()
    instance: Union[None, bool, float, str, list, Dict[str, Any]] = attr.ib()


@attr.s(slots=True, repr=False)
class JSONDecodeErrorContext(FailureContext):
    """Failed to decode JSON."""

    message: str = attr.ib()
    document: str = attr.ib()
    position: int = attr.ib()
    lineno: int = attr.ib()
    colno: int = attr.ib()


@attr.s(slots=True, repr=False)
class ServerError(FailureContext):
    status_code: int = attr.ib()


@attr.s(slots=True, repr=False)
class MissingContentType(FailureContext):
    """Content type header is missing."""

    media_types: List[str] = attr.ib()


@attr.s(slots=True, repr=False)
class UndefinedContentType(FailureContext):
    """Response has Content-Type that is not defined in the schema."""

    content_type: str = attr.ib()
    defined_content_types: List[str] = attr.ib()


@attr.s(slots=True, repr=False)
class UndefinedStatusCode(FailureContext):
    """Response has a status code that is not defined in the schema."""

    # Response's status code
    status_code: int = attr.ib()
    # Status codes as defined in schema
    defined_status_codes: List[str] = attr.ib()
    # Defined status code with expanded wildcards
    allowed_status_codes: List[int] = attr.ib()


@attr.s(slots=True, repr=False)
class MissingHeaders(FailureContext):
    """Some required headers are missing."""

    missing_headers: List[str] = attr.ib()


@attr.s(slots=True, repr=False)
class MalformedMediaType(FailureContext):
    """Media type name is malformed.

    Example: `application-json` instead of `application/json`
    """

    actual: str = attr.ib()
    defined: str = attr.ib()


@attr.s(slots=True, repr=False)
class ResponseTimeExceeded(FailureContext):
    """Response took longer than expected."""

    elapsed: float = attr.ib()
    deadline: int = attr.ib()


@attr.s(slots=True, repr=False)
class ResponseTimeout(FailureContext):
    """Response took longer than timeout and wasn't received."""

    timeout: int = attr.ib()
