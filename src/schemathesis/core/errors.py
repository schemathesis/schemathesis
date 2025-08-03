"""Base error handling that is not tied to any specific API specification or execution context."""

from __future__ import annotations

import enum
import re
import traceback
from types import TracebackType
from typing import TYPE_CHECKING, Any, Callable, NoReturn

from schemathesis.core.output import truncate_json

if TYPE_CHECKING:
    from jsonschema import SchemaError as JsonSchemaError
    from jsonschema import ValidationError
    from requests import RequestException

    from schemathesis.config import OutputConfig
    from schemathesis.core.compat import RefResolutionError


SCHEMA_ERROR_SUGGESTION = "Ensure that the definition complies with the OpenAPI specification"
SERIALIZERS_DOCUMENTATION_URL = "https://schemathesis.readthedocs.io/en/stable/guides/custom-serializers/"
SERIALIZERS_SUGGESTION_MESSAGE = f"Check your schema or add custom serializers: {SERIALIZERS_DOCUMENTATION_URL}"
SERIALIZATION_NOT_POSSIBLE_MESSAGE = f"No supported serializers for media types: {{}}\n{SERIALIZERS_SUGGESTION_MESSAGE}"
SERIALIZATION_FOR_TYPE_IS_NOT_POSSIBLE_MESSAGE = (
    f"Cannot serialize to '{{}}' (unsupported media type)\n{SERIALIZERS_SUGGESTION_MESSAGE}"
)
RECURSIVE_REFERENCE_ERROR_MESSAGE = (
    "Currently, Schemathesis can't generate data for this operation due to "
    "recursive references in the operation definition. See more information in "
    "this issue - https://github.com/schemathesis/schemathesis/issues/947"
)


class SchemathesisError(Exception):
    """Base exception class for all Schemathesis errors."""


class InvalidSchema(SchemathesisError):
    """Indicates errors in API schema validation or processing."""

    def __init__(
        self,
        message: str,
        path: str | None = None,
        method: str | None = None,
    ) -> None:
        self.message = message
        self.path = path
        self.method = method

    @classmethod
    def from_jsonschema_error(
        cls, error: ValidationError | JsonSchemaError, path: str | None, method: str | None, config: OutputConfig
    ) -> InvalidSchema:
        if error.absolute_path:
            part = error.absolute_path[-1]
            if isinstance(part, int) and len(error.absolute_path) > 1:
                parent = error.absolute_path[-2]
                message = f"Invalid definition for element at index {part} in `{parent}`"
            else:
                message = f"Invalid `{part}` definition"
        else:
            message = "Invalid schema definition"
        error_path = " -> ".join(str(entry) for entry in error.path) or "[root]"
        message += f"\n\nLocation:\n    {error_path}"
        instance = truncate_json(error.instance, config=config)
        message += f"\n\nProblematic definition:\n{instance}"
        message += "\n\nError details:\n    "
        # This default message contains the instance which we already printed
        if "is not valid under any of the given schemas" in error.message:
            message += "The provided definition doesn't match any of the expected formats or types."
        else:
            message += error.message
        message += f"\n\n{SCHEMA_ERROR_SUGGESTION}"
        return cls(message, path=path, method=method)

    @classmethod
    def from_reference_resolution_error(
        cls, error: RefResolutionError, path: str | None, method: str | None
    ) -> InvalidSchema:
        notes = getattr(error, "__notes__", [])
        # Some exceptions don't have the actual reference in them, hence we add it manually via notes
        pointer = f"'{notes[0]}'"
        message = "Unresolvable JSON pointer in the schema"
        # Get the pointer value from "Unresolvable JSON pointer: 'components/UnknownParameter'"
        message += f"\n\nError details:\n    JSON pointer: {pointer}"
        message += "\n    This typically means that the schema is referencing a component that doesn't exist."
        message += f"\n\n{SCHEMA_ERROR_SUGGESTION}"
        return cls(message, path=path, method=method)

    def as_failing_test_function(self) -> Callable:
        """Create a test function that will fail.

        This approach allows us to use default pytest reporting style for operation-level schema errors.
        """

        def actual_test(*args: Any, **kwargs: Any) -> NoReturn:
            __tracebackhide__ = True
            raise self

        return actual_test


class HookError(SchemathesisError):
    """Happens during hooks loading."""

    module_path: str

    __slots__ = ("module_path",)

    def __init__(self, module_path: str) -> None:
        self.module_path = module_path

    def __str__(self) -> str:
        return f"Failed to load Schemathesis extensions from `{self.module_path}`"


class InvalidRegexType(InvalidSchema):
    """Raised when an invalid type is used where a regex pattern is expected."""


class InvalidStateMachine(SchemathesisError):
    """Collection of validation errors found in API state machine transitions.

    Raised during schema initialization when one or more transitions
    contain invalid definitions, such as references to non-existent parameters
    or operations.
    """

    errors: list[InvalidTransition]

    __slots__ = ("errors",)

    def __init__(self, errors: list[InvalidTransition]) -> None:
        self.errors = errors

    def __str__(self) -> str:
        """Format state machine validation errors in a clear, hierarchical structure."""
        result = "The following API operations contain invalid link definitions:"

        # Group transitions by source operation, then by target and status
        by_source: dict[str, dict[tuple[str, str], list[InvalidTransition]]] = {}
        for transition in self.errors:
            source_group = by_source.setdefault(transition.source, {})
            target_key = (transition.target, transition.status_code)
            source_group.setdefault(target_key, []).append(transition)

        for source, target_groups in by_source.items():
            for (target, status), transitions in target_groups.items():
                for transition in transitions:
                    result += f"\n\n  {_format_transition(source, status, transition.name, target)}\n"
                    for error in transition.errors:
                        result += f"\n     - {error.message}"
        return result


def _format_transition(source: str, status: str, transition: str, target: str) -> str:
    return f"{source} -> [{status}] {transition} -> {target}"


class InvalidTransition(SchemathesisError):
    """Raised when a stateful transition contains one or more errors."""

    name: str
    source: str
    target: str
    status_code: str
    errors: list[TransitionValidationError]

    __slots__ = ("name", "source", "target", "status_code", "errors")

    def __init__(
        self,
        name: str,
        source: str,
        target: str,
        status_code: str,
        errors: list[TransitionValidationError],
    ) -> None:
        self.name = name
        self.source = source
        self.target = target
        self.status_code = status_code
        self.errors = errors


class TransitionValidationError(SchemathesisError):
    """Single validation error found during stateful transition validation."""

    message: str

    __slots__ = ("message",)

    def __init__(self, message: str) -> None:
        self.message = message


class MalformedMediaType(ValueError):
    """Raised on parsing of incorrect media type."""


class InvalidRegexPattern(InvalidSchema):
    """Raised when a string pattern is not a valid regular expression."""

    @classmethod
    def from_hypothesis_jsonschema_message(cls, message: str) -> InvalidRegexPattern:
        match = re.search(r"pattern='(.*?)'.*?\((.*?)\)", message)
        if match:
            message = f"Invalid regular expression. Pattern `{match.group(1)}` is not recognized - `{match.group(2)}`"
        return cls(message)

    @classmethod
    def from_schema_error(cls, error: JsonSchemaError, *, from_examples: bool) -> InvalidRegexPattern:
        if from_examples:
            message = (
                "Failed to generate test cases from examples for this API operation because of "
                f"unsupported regular expression `{error.instance}`"
            )
        else:
            message = (
                "Failed to generate test cases for this API operation because of "
                f"unsupported regular expression `{error.instance}`"
            )
        return cls(message)


class InvalidHeadersExample(InvalidSchema):
    @classmethod
    def from_headers(cls, headers: dict[str, str]) -> InvalidHeadersExample:
        message = (
            "Failed to generate test cases from examples for this API operation because of "
            "some header examples are invalid:\n"
        )
        for key, value in headers.items():
            message += f"\n  - {key!r}={value!r}"
        message += "\n\nEnsure the header examples comply with RFC 7230, Section 3.2"
        return cls(message)


class IncorrectUsage(SchemathesisError):
    """Indicates incorrect usage of Schemathesis' public API."""


class NoLinksFound(IncorrectUsage):
    """Raised when no valid links are available for stateful testing."""


class InvalidRateLimit(IncorrectUsage):
    """Incorrect input for rate limiting."""

    def __init__(self, value: str) -> None:
        super().__init__(
            f"Invalid rate limit value: `{value}`. Should be in form `limit/interval`. "
            "Example: `10/m` for 10 requests per minute."
        )


class InternalError(SchemathesisError):
    """Internal error in Schemathesis."""


class SerializationError(SchemathesisError):
    """Can't serialize request payload."""


NAMESPACE_DEFINITION_URL = "https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.3.md#xmlNamespace"
UNBOUND_PREFIX_MESSAGE_TEMPLATE = (
    "Unbound prefix: `{prefix}`. "
    "You need to define this namespace in your API schema via the `xml.namespace` keyword. "
    f"See more at {NAMESPACE_DEFINITION_URL}"
)


class UnboundPrefix(SerializationError):
    """XML serialization error.

    It happens when the schema does not define a namespace that is used by some of its parts.
    """

    def __init__(self, prefix: str):
        super().__init__(UNBOUND_PREFIX_MESSAGE_TEMPLATE.format(prefix=prefix))


class SerializationNotPossible(SerializationError):
    """Not possible to serialize data to specified media type(s).

    This error occurs in two scenarios:
    1. When attempting to serialize to a specific media type that isn't supported
    2. When none of the available media types can be used for serialization
    """

    def __init__(self, message: str, media_types: list[str]) -> None:
        self.message = message
        self.media_types = media_types

    def __str__(self) -> str:
        return self.message

    @classmethod
    def from_media_types(cls, *media_types: str) -> SerializationNotPossible:
        """Create error when no available media type can be used."""
        return cls(SERIALIZATION_NOT_POSSIBLE_MESSAGE.format(", ".join(media_types)), media_types=list(media_types))

    @classmethod
    def for_media_type(cls, media_type: str) -> SerializationNotPossible:
        """Create error when a specific required media type isn't supported."""
        return cls(SERIALIZATION_FOR_TYPE_IS_NOT_POSSIBLE_MESSAGE.format(media_type), media_types=[media_type])


class OperationNotFound(LookupError, SchemathesisError):
    """Raised when an API operation cannot be found in the schema.

    This error typically occurs during schema access in user code when trying to
    reference a non-existent operation.
    """

    def __init__(self, message: str, item: str) -> None:
        self.message = message
        self.item = item

    def __str__(self) -> str:
        return self.message


@enum.unique
class LoaderErrorKind(str, enum.Enum):
    # Connection related issues
    CONNECTION_SSL = "connection_ssl"
    CONNECTION_OTHER = "connection_other"
    NETWORK_OTHER = "network_other"

    # HTTP error codes
    HTTP_SERVER_ERROR = "http_server_error"
    HTTP_CLIENT_ERROR = "http_client_error"
    HTTP_NOT_FOUND = "http_not_found"
    HTTP_FORBIDDEN = "http_forbidden"

    # Content decoding issues
    SYNTAX_ERROR = "syntax_error"
    UNEXPECTED_CONTENT_TYPE = "unexpected_content_type"
    YAML_NUMERIC_STATUS_CODES = "yaml_numeric_status_codes"
    YAML_NON_STRING_KEYS = "yaml_non_string_keys"

    # Open API validation
    OPEN_API_INVALID_SCHEMA = "open_api_invalid_schema"
    OPEN_API_UNSPECIFIED_VERSION = "open_api_unspecified_version"
    OPEN_API_UNSUPPORTED_VERSION = "open_api_unsupported_version"

    # GraphQL validation
    GRAPHQL_INVALID_SCHEMA = "graphql_invalid_schema"

    # Unclassified
    UNCLASSIFIED = "unclassified"


class LoaderError(SchemathesisError):
    """Failed to load an API schema."""

    def __init__(
        self,
        kind: LoaderErrorKind,
        message: str,
        url: str | None = None,
        extras: list[str] | None = None,
    ) -> None:
        self.kind = kind
        self.message = message
        self.url = url
        self.extras = extras or []

    def __str__(self) -> str:
        return self.message


def get_request_error_extras(exc: RequestException) -> list[str]:
    """Extract additional context from a request exception."""
    from requests.exceptions import ChunkedEncodingError, ConnectionError, SSLError
    from urllib3.exceptions import MaxRetryError

    if isinstance(exc, SSLError):
        reason = str(exc.args[0].reason)
        return [_remove_ssl_line_number(reason).strip()]
    if isinstance(exc, ConnectionError):
        inner = exc.args[0]
        if isinstance(inner, MaxRetryError) and inner.reason is not None:
            arg = inner.reason.args[0]
            if isinstance(arg, str):
                if ":" not in arg:
                    reason = arg
                else:
                    _, reason = arg.split(":", maxsplit=1)
            else:
                reason = f"Max retries exceeded with url: {inner.url}"
            return [reason.strip()]
        return [" ".join(map(_clean_inner_request_message, inner.args))]
    if isinstance(exc, ChunkedEncodingError):
        args = exc.args[0].args
        if len(args) == 1:
            return [str(args[0])]
        return [str(args[1])]
    return []


def _remove_ssl_line_number(text: str) -> str:
    return re.sub(r"\(_ssl\.c:\d+\)", "", text)


def _clean_inner_request_message(message: object) -> str:
    if isinstance(message, str) and message.startswith("HTTPConnectionPool"):
        return re.sub(r"HTTPConnectionPool\(.+?\): ", "", message).rstrip(".")
    return str(message)


def get_request_error_message(exc: RequestException) -> str:
    """Extract user-facing message from a request exception."""
    from requests.exceptions import ChunkedEncodingError, ConnectionError, ReadTimeout, SSLError

    if isinstance(exc, ReadTimeout):
        _, duration = exc.args[0].args[0][:-1].split("read timeout=")
        return f"Read timed out after {duration} seconds"
    if isinstance(exc, SSLError):
        return "SSL verification problem"
    if isinstance(exc, ConnectionError):
        return "Connection failed"
    if isinstance(exc, ChunkedEncodingError):
        return "Connection broken. The server declared chunked encoding but sent an invalid chunk"
    return str(exc)


def split_traceback(traceback: str) -> list[str]:
    return [entry for entry in traceback.splitlines() if entry]


def format_exception(
    error: BaseException,
    *,
    with_traceback: bool = False,
    skip_frames: int = 0,
) -> str:
    """Format exception with optional traceback."""
    if not with_traceback:
        lines = traceback.format_exception_only(type(error), error)
        return "".join(lines).strip()

    trace = error.__traceback__
    if skip_frames > 0:
        trace = extract_nth_traceback(trace, skip_frames)
    lines = traceback.format_exception(type(error), error, trace)
    return "".join(lines).strip()


def extract_nth_traceback(trace: TracebackType | None, n: int) -> TracebackType | None:
    depth = 0
    while depth < n and trace is not None:
        trace = trace.tb_next
        depth += 1
    return trace
