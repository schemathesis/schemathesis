from __future__ import annotations

import textwrap
from dataclasses import dataclass
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any

from schemathesis.internal.output import OutputConfig

if TYPE_CHECKING:
    from graphql.error import GraphQLFormattedError
    from jsonschema import ValidationError


class FailureContext:
    """Additional data specific to certain failure kind."""

    # Short description of what happened
    title: str
    # A longer one
    message: str
    type: str

    def unique_by_key(self, check_message: str | None) -> tuple[str, ...]:
        """A key to distinguish different failure contexts."""
        return (check_message or self.message,)


@dataclass(repr=False)
class ValidationErrorContext(FailureContext):
    """Additional information about JSON Schema validation errors."""

    validation_message: str
    schema_path: list[str | int]
    schema: dict[str, Any] | bool
    instance_path: list[str | int]
    instance: None | bool | float | str | list | dict[str, Any]
    message: str
    title: str = "Response violates schema"
    type: str = "json_schema"

    def unique_by_key(self, check_message: str | None) -> tuple[str, ...]:
        # Deduplicate by JSON Schema path. All errors that happened on this sub-schema will be deduplicated
        return ("/".join(map(str, self.schema_path)),)

    @classmethod
    def from_exception(
        cls, exc: ValidationError, *, output_config: OutputConfig | None = None
    ) -> ValidationErrorContext:
        from .internal.output import truncate_json

        output_config = OutputConfig.from_parent(output_config, max_lines=20)
        schema = textwrap.indent(truncate_json(exc.schema, config=output_config), prefix="    ")
        value = textwrap.indent(truncate_json(exc.instance, config=output_config), prefix="    ")
        schema_path = list(exc.absolute_schema_path)
        if len(schema_path) > 1:
            # Exclude the last segment, which is already in the schema
            schema_title = "Schema at "
            for segment in schema_path[:-1]:
                schema_title += f"/{segment}"
        else:
            schema_title = "Schema"
        message = f"{exc.message}\n\n{schema_title}:\n\n{schema}\n\nValue:\n\n{value}"
        return cls(
            message=message,
            validation_message=exc.message,
            schema_path=schema_path,
            schema=exc.schema,
            instance_path=list(exc.absolute_path),
            instance=exc.instance,
        )


@dataclass(repr=False)
class JSONDecodeErrorContext(FailureContext):
    """Failed to decode JSON."""

    validation_message: str
    document: str
    position: int
    lineno: int
    colno: int
    message: str
    title: str = "JSON deserialization error"
    type: str = "json_decode"

    def unique_by_key(self, check_message: str | None) -> tuple[str, ...]:
        # Treat different JSON decoding failures as the same issue
        # Payloads often contain dynamic data and distinguishing it by the error location still would not be sufficient
        # as it may be different on different dynamic payloads
        return (self.title,)

    @classmethod
    def from_exception(cls, exc: JSONDecodeError) -> JSONDecodeErrorContext:
        return cls(
            message=str(exc),
            validation_message=exc.msg,
            document=exc.doc,
            position=exc.pos,
            lineno=exc.lineno,
            colno=exc.colno,
        )


@dataclass(repr=False)
class ServerError(FailureContext):
    status_code: int
    title: str = "Server error"
    message: str = ""
    type: str = "server_error"


@dataclass(repr=False)
class MissingContentType(FailureContext):
    """Content type header is missing."""

    media_types: list[str]
    message: str
    title: str = "Missing Content-Type header"
    type: str = "missing_content_type"


@dataclass(repr=False)
class UndefinedContentType(FailureContext):
    """Response has Content-Type that is not documented in the schema."""

    content_type: str
    defined_content_types: list[str]
    message: str
    title: str = "Undocumented Content-Type"
    type: str = "undefined_content_type"


@dataclass(repr=False)
class AcceptedNegativeData(FailureContext):
    """Response with negative data was accepted."""

    message: str
    title: str = "Accepted negative data"
    type: str = "accepted_negative_data"


@dataclass(repr=False)
class UseAfterFree(FailureContext):
    """Resource was used after a successful DELETE operation on it."""

    message: str
    free: str
    usage: str
    title: str = "Use after free"
    type: str = "use_after_free"


@dataclass(repr=False)
class EnsureResourceAvailability(FailureContext):
    """Resource is not available immediately after creation."""

    message: str
    created_with: str
    not_available_with: str
    title: str = "Resource is not available after creation"
    type: str = "ensure_resource_availability"


@dataclass(repr=False)
class IgnoredAuth(FailureContext):
    """The API operation does not check the specified authentication."""

    message: str
    title: str = "Authentication declared but not enforced for this operation"
    type: str = "ignored_auth"


@dataclass(repr=False)
class UndefinedStatusCode(FailureContext):
    """Response has a status code that is not defined in the schema."""

    # Response's status code
    status_code: int
    # Status codes as defined in schema
    defined_status_codes: list[str]
    # Defined status code with expanded wildcards
    allowed_status_codes: list[int]
    message: str
    title: str = "Undocumented HTTP status code"
    type: str = "undefined_status_code"


@dataclass(repr=False)
class MissingHeaders(FailureContext):
    """Some required headers are missing."""

    missing_headers: list[str]
    message: str
    title: str = "Missing required headers"
    type: str = "missing_headers"


@dataclass(repr=False)
class MalformedMediaType(FailureContext):
    """Media type name is malformed.

    Example: `application-json` instead of `application/json`
    """

    actual: str
    defined: str
    message: str
    title: str = "Malformed media type"
    type: str = "malformed_media_type"


@dataclass(repr=False)
class ResponseTimeExceeded(FailureContext):
    """Response took longer than expected."""

    elapsed: float
    deadline: int
    message: str
    title: str = "Response time limit exceeded"
    type: str = "response_time_exceeded"

    def unique_by_key(self, check_message: str | None) -> tuple[str, ...]:
        return (self.title,)


@dataclass(repr=False)
class RequestTimeout(FailureContext):
    """Request took longer than timeout."""

    timeout: int
    message: str
    title: str = "Response timeout"
    type: str = "request_timeout"


@dataclass(repr=False)
class UnexpectedGraphQLResponse(FailureContext):
    """GraphQL response is not a JSON object."""

    message: str
    title: str = "Unexpected GraphQL Response"
    type: str = "graphql_unexpected_response"


@dataclass(repr=False)
class GraphQLClientError(FailureContext):
    """GraphQL query has not been executed."""

    message: str
    errors: list[GraphQLFormattedError]
    title: str = "GraphQL client error"
    type: str = "graphql_client_error"


@dataclass(repr=False)
class GraphQLServerError(FailureContext):
    """GraphQL response indicates at least one server error."""

    message: str
    errors: list[GraphQLFormattedError]
    title: str = "GraphQL server error"
    type: str = "graphql_server_error"
