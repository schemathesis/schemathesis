from __future__ import annotations

import enum
import re
import traceback
from dataclasses import dataclass, field
from hashlib import sha1
from json import JSONDecodeError
from types import TracebackType
from typing import TYPE_CHECKING, Any, Callable, Generator, NoReturn

from .constants import SERIALIZERS_SUGGESTION_MESSAGE
from .failures import FailureContext
from .internal.output import truncate_json

if TYPE_CHECKING:
    import hypothesis.errors
    from graphql.error import GraphQLFormattedError
    from jsonschema import RefResolutionError, ValidationError
    from jsonschema import SchemaError as JsonSchemaError
    from requests import RequestException

    from .transports.responses import GenericResponse


class CheckFailed(AssertionError):
    """Custom error type to distinguish from arbitrary AssertionError that may happen in the dependent libraries."""

    __module__ = "builtins"
    context: FailureContext | None
    causes: tuple[CheckFailed | AssertionError, ...] | None

    def __init__(
        self,
        *args: Any,
        context: FailureContext | None = None,
        causes: tuple[CheckFailed | AssertionError, ...] | None = None,
    ):
        super().__init__(*args)
        self.context = context
        self.causes = causes


def make_unique_by_key(
    check_name: str, check_message: str | None, context: FailureContext | None
) -> tuple[str | None, ...]:
    """A key to distinguish different failed checks.

    It is not only based on `FailureContext`, because the end-user may raise plain `AssertionError` in their custom
    checks, and those won't have any context attached.
    """
    if context is not None:
        return context.unique_by_key(check_message)
    return check_name, check_message


def deduplicate_failed_checks(
    checks: list[CheckFailed | AssertionError],
) -> Generator[CheckFailed | AssertionError, None, None]:
    """Keep only unique failed checks."""
    seen = set()
    for check in checks:
        check_message = check.args[0]
        if isinstance(check, CheckFailed) and check.context is not None:
            key = check.context.unique_by_key(check_message)
        else:
            key = check_message
        if key not in seen:
            yield check
            seen.add(key)


CACHE: dict[str | int, type[CheckFailed]] = {}


def get_exception(name: str) -> type[CheckFailed]:
    """Create a new exception class with provided name or fetch one from the cache."""
    if name in CACHE:
        exception_class = CACHE[name]
    else:
        exception_class = type(name, (CheckFailed,), {})
        exception_class.__qualname__ = CheckFailed.__name__
        exception_class.__name__ = CheckFailed.__name__
        CACHE[name] = exception_class
    return exception_class


def _get_hashed_exception(prefix: str, message: str) -> type[CheckFailed]:
    """Give different exceptions for different error messages."""
    messages_digest = sha1(message.encode("utf-8")).hexdigest()
    name = f"{prefix}{messages_digest}"
    return get_exception(name)


def get_grouped_exception(prefix: str, *exceptions: AssertionError) -> type[CheckFailed]:
    # The prefix is needed to distinguish multiple operations with the same error messages
    # that are coming from different operations
    messages = [exception.args[0] for exception in exceptions]
    message = "".join(messages)
    return _get_hashed_exception("GroupedException", f"{prefix}{message}")


def get_server_error(prefix: str, status_code: int) -> type[CheckFailed]:
    """Return new exception for the Internal Server Error cases."""
    name = f"ServerError{prefix}{status_code}"
    return get_exception(name)


def get_status_code_error(prefix: str, status_code: int) -> type[CheckFailed]:
    """Return new exception for an unexpected status code."""
    name = f"StatusCodeError{prefix}{status_code}"
    return get_exception(name)


def get_response_type_error(prefix: str, expected: str, received: str) -> type[CheckFailed]:
    """Return new exception for an unexpected response type."""
    name = f"SchemaValidationError{prefix}{expected}_{received}"
    return get_exception(name)


def get_malformed_media_type_error(prefix: str, media_type: str) -> type[CheckFailed]:
    name = f"MalformedMediaType{prefix}{media_type}"
    return get_exception(name)


def get_missing_content_type_error(prefix: str) -> type[CheckFailed]:
    """Return new exception for a missing Content-Type header."""
    return get_exception(f"MissingContentTypeError{prefix}")


def get_schema_validation_error(prefix: str, exception: ValidationError) -> type[CheckFailed]:
    """Return new exception for schema validation error."""
    return _get_hashed_exception(f"SchemaValidationError{prefix}", str(exception))


def get_response_parsing_error(prefix: str, exception: JSONDecodeError) -> type[CheckFailed]:
    """Return new exception for response parsing error."""
    return _get_hashed_exception(f"ResponseParsingError{prefix}", str(exception))


def get_headers_error(prefix: str, message: str) -> type[CheckFailed]:
    """Return new exception for missing headers."""
    return _get_hashed_exception(f"MissingHeadersError{prefix}", message)


def get_negative_rejection_error(prefix: str, status: int) -> type[CheckFailed]:
    return _get_hashed_exception(f"AcceptedNegativeDataError{prefix}", str(status))


def get_use_after_free_error(free: str) -> type[CheckFailed]:
    return _get_hashed_exception("UseAfterFreeError", free)


def get_ensure_resource_availability_error(operation: str) -> type[CheckFailed]:
    return _get_hashed_exception("EnsureResourceAvailabilityError", operation)


def get_ignored_auth_error(operation: str) -> type[CheckFailed]:
    return _get_hashed_exception("IgnoredAuthError", operation)


def get_timeout_error(prefix: str, deadline: float | int) -> type[CheckFailed]:
    """Request took too long."""
    return _get_hashed_exception(f"TimeoutError{prefix}", str(deadline))


def get_unexpected_graphql_response_error(type_: type) -> type[CheckFailed]:
    """When GraphQL response is not a JSON object."""
    return get_exception(f"UnexpectedGraphQLResponseError:{type_}")


def get_grouped_graphql_error(errors: list[GraphQLFormattedError]) -> type[CheckFailed]:
    # Canonicalize GraphQL errors by serializing them uniformly and sorting the outcomes
    entries = []
    for error in errors:
        message = error["message"]
        if "locations" in error:
            message += ";locations:"
            for location in sorted(error["locations"]):
                message += f"({location['line'], location['column']})"
        if "path" in error:
            message += ";path:"
            for chunk in error["path"]:
                message += str(chunk)
        entries.append(message)
    entries.sort()
    return _get_hashed_exception("GraphQLErrors", "".join(entries))


SCHEMA_ERROR_SUGGESTION = "Ensure that the definition complies with the OpenAPI specification"


@dataclass
class OperationSchemaError(Exception):
    """Schema associated with an API operation contains an error."""

    __module__ = "builtins"
    message: str | None = None
    path: str | None = None
    method: str | None = None
    full_path: str | None = None

    @classmethod
    def from_jsonschema_error(
        cls, error: ValidationError, path: str | None, method: str | None, full_path: str | None
    ) -> OperationSchemaError:
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
        instance = truncate_json(error.instance)
        message += f"\n\nProblematic definition:\n{instance}"
        message += "\n\nError details:\n    "
        # This default message contains the instance which we already printed
        if "is not valid under any of the given schemas" in error.message:
            message += "The provided definition doesn't match any of the expected formats or types."
        else:
            message += error.message
        message += f"\n\n{SCHEMA_ERROR_SUGGESTION}"
        return cls(message, path=path, method=method, full_path=full_path)

    @classmethod
    def from_reference_resolution_error(
        cls, error: RefResolutionError, path: str | None, method: str | None, full_path: str | None
    ) -> OperationSchemaError:
        notes = getattr(error, "__notes__", [])
        # Some exceptions don't have the actual reference in them, hence we add it manually via notes
        pointer = f"'{notes[0]}'"
        message = "Unresolvable JSON pointer in the schema"
        # Get the pointer value from "Unresolvable JSON pointer: 'components/UnknownParameter'"
        message += f"\n\nError details:\n    JSON pointer: {pointer}"
        message += "\n    This typically means that the schema is referencing a component that doesn't exist."
        message += f"\n\n{SCHEMA_ERROR_SUGGESTION}"
        return cls(message, path=path, method=method, full_path=full_path)

    def as_failing_test_function(self) -> Callable:
        """Create a test function that will fail.

        This approach allows us to use default pytest reporting style for operation-level schema errors.
        """

        def actual_test(*args: Any, **kwargs: Any) -> NoReturn:
            __tracebackhide__ = True
            raise self

        return actual_test


@dataclass
class BodyInGetRequestError(OperationSchemaError):
    __module__ = "builtins"


@dataclass
class OperationNotFound(KeyError):
    message: str
    item: str
    __module__ = "builtins"

    def __str__(self) -> str:
        return self.message


@dataclass
class InvalidRegularExpression(OperationSchemaError):
    is_valid_type: bool = True
    __module__ = "builtins"

    @classmethod
    def from_hypothesis_jsonschema_message(cls, message: str) -> InvalidRegularExpression:
        match = re.search(r"pattern='(.*?)'.*?\((.*?)\)", message)
        if match:
            message = f"Invalid regular expression. Pattern `{match.group(1)}` is not recognized - `{match.group(2)}`"
        return cls(message)

    @classmethod
    def from_schema_error(cls, error: JsonSchemaError, *, from_examples: bool) -> InvalidRegularExpression:
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


class InvalidHeadersExample(OperationSchemaError):
    __module__ = "builtins"

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


class DeadlineExceeded(Exception):
    """Test took too long to run."""

    __module__ = "builtins"

    @classmethod
    def from_exc(cls, exc: hypothesis.errors.DeadlineExceeded) -> DeadlineExceeded:
        runtime = exc.runtime.total_seconds() * 1000
        deadline = exc.deadline.total_seconds() * 1000
        return cls(
            f"Test running time is too slow! It took {runtime:.2f}ms, which exceeds the deadline of {deadline:.2f}ms.\n"
        )


class RecursiveReferenceError(Exception):
    """Recursive reference is impossible to resolve due to current limitations."""

    __module__ = "builtins"


@enum.unique
class RuntimeErrorType(str, enum.Enum):
    # Connection related issues
    CONNECTION_SSL = "connection_ssl"
    CONNECTION_OTHER = "connection_other"
    NETWORK_OTHER = "network_other"

    # Hypothesis issues
    HYPOTHESIS_DEADLINE_EXCEEDED = "hypothesis_deadline_exceeded"
    HYPOTHESIS_UNSATISFIABLE = "hypothesis_unsatisfiable"
    HYPOTHESIS_UNSUPPORTED_GRAPHQL_SCALAR = "hypothesis_unsupported_graphql_scalar"
    HYPOTHESIS_HEALTH_CHECK_DATA_TOO_LARGE = "hypothesis_health_check_data_too_large"
    HYPOTHESIS_HEALTH_CHECK_FILTER_TOO_MUCH = "hypothesis_health_check_filter_too_much"
    HYPOTHESIS_HEALTH_CHECK_TOO_SLOW = "hypothesis_health_check_too_slow"
    HYPOTHESIS_HEALTH_CHECK_LARGE_BASE_EXAMPLE = "hypothesis_health_check_large_base_example"

    SCHEMA_BODY_IN_GET_REQUEST = "schema_body_in_get_request"
    SCHEMA_INVALID_REGULAR_EXPRESSION = "schema_invalid_regular_expression"
    SCHEMA_UNSUPPORTED = "schema_unsupported"
    SCHEMA_GENERIC = "schema_generic"

    SERIALIZATION_NOT_POSSIBLE = "serialization_not_possible"
    SERIALIZATION_UNBOUNDED_PREFIX = "serialization_unbounded_prefix"

    # Unclassified
    UNCLASSIFIED = "unclassified"

    @property
    def has_useful_traceback(self) -> bool:
        return self not in (
            RuntimeErrorType.SCHEMA_BODY_IN_GET_REQUEST,
            RuntimeErrorType.SCHEMA_INVALID_REGULAR_EXPRESSION,
            RuntimeErrorType.SCHEMA_UNSUPPORTED,
            RuntimeErrorType.SCHEMA_GENERIC,
            RuntimeErrorType.SERIALIZATION_NOT_POSSIBLE,
        )


@enum.unique
class SchemaErrorType(str, enum.Enum):
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
    OPEN_API_EXPERIMENTAL_VERSION = "open_api_experimental_version"

    # GraphQL validation
    GRAPHQL_INVALID_SCHEMA = "graphql_invalid_schema"

    # Unclassified
    UNCLASSIFIED = "unclassified"


@dataclass
class SchemaError(RuntimeError):
    """Failed to load an API schema."""

    type: SchemaErrorType
    message: str
    url: str | None = None
    response: GenericResponse | None = None
    extras: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return self.message


class NonCheckError(Exception):
    """An error happened in side the runner, but is not related to failed checks.

    Used primarily to not let Hypothesis consider the test as flaky or detect multiple failures as we handle it
    on our side.
    """

    __module__ = "builtins"


class InternalError(Exception):
    """Internal error in Schemathesis."""

    __module__ = "builtins"


class SkipTest(BaseException):
    """Raises when a test should be skipped and return control to the execution engine (own Schemathesis' or pytest)."""

    __module__ = "builtins"


SERIALIZATION_NOT_POSSIBLE_MESSAGE = (
    f"Schemathesis can't serialize data to any of the defined media types: {{}} \n{SERIALIZERS_SUGGESTION_MESSAGE}"
)
NAMESPACE_DEFINITION_URL = "https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.3.md#xmlNamespace"
UNBOUND_PREFIX_MESSAGE_TEMPLATE = (
    "Unbound prefix: `{prefix}`. "
    "You need to define this namespace in your API schema via the `xml.namespace` keyword. "
    f"See more at {NAMESPACE_DEFINITION_URL}"
)

SERIALIZATION_FOR_TYPE_IS_NOT_POSSIBLE_MESSAGE = (
    f"Schemathesis can't serialize data to {{}} \n{SERIALIZERS_SUGGESTION_MESSAGE}"
)


class SerializationError(Exception):
    """Serialization can not be done."""

    __module__ = "builtins"


class UnboundPrefixError(SerializationError):
    """XML serialization error.

    It happens when the schema does not define a namespace that is used by some of its parts.
    """

    def __init__(self, prefix: str):
        super().__init__(UNBOUND_PREFIX_MESSAGE_TEMPLATE.format(prefix=prefix))


@dataclass
class SerializationNotPossible(SerializationError):
    """Not possible to serialize to any of the media types defined for some API operation.

    Usually, there is still `application/json` along with less common ones, but this error happens when there is no
    media type that Schemathesis knows how to serialize data to.
    """

    message: str
    media_types: list[str]

    __module__ = "builtins"

    def __str__(self) -> str:
        return self.message

    @classmethod
    def from_media_types(cls, *media_types: str) -> SerializationNotPossible:
        return cls(SERIALIZATION_NOT_POSSIBLE_MESSAGE.format(", ".join(media_types)), media_types=list(media_types))

    @classmethod
    def for_media_type(cls, media_type: str) -> SerializationNotPossible:
        return cls(SERIALIZATION_FOR_TYPE_IS_NOT_POSSIBLE_MESSAGE.format(media_type), media_types=[media_type])


class UsageError(Exception):
    """Incorrect usage of Schemathesis functions."""


def maybe_set_assertion_message(exc: AssertionError, check_name: str) -> str:
    message = str(exc)
    title = f"Custom check failed: `{check_name}`"
    if not message:
        exc.args = (title, None)
    else:
        exc.args = (title, message)
    return message


def format_exception(error: Exception, include_traceback: bool = False) -> str:
    """Format exception as text."""
    error_type = type(error)
    if include_traceback:
        lines = traceback.format_exception(error_type, error, error.__traceback__)
    else:
        lines = traceback.format_exception_only(error_type, error)
    return "".join(lines).strip()


def extract_nth_traceback(trace: TracebackType | None, n: int) -> TracebackType | None:
    depth = 0
    while depth < n and trace is not None:
        trace = trace.tb_next
        depth += 1
    return trace


def remove_ssl_line_number(text: str) -> str:
    return re.sub(r"\(_ssl\.c:\d+\)", "", text)


def _clean_inner_request_message(message: Any) -> str:
    if isinstance(message, str) and message.startswith("HTTPConnectionPool"):
        return re.sub(r"HTTPConnectionPool\(.+?\): ", "", message).rstrip(".")
    return str(message)


def extract_requests_exception_details(exc: RequestException) -> tuple[str, list[str]]:
    from requests.exceptions import ChunkedEncodingError, ConnectionError, SSLError
    from urllib3.exceptions import MaxRetryError

    if isinstance(exc, SSLError):
        message = "SSL verification problem"
        reason = str(exc.args[0].reason)
        extra = [remove_ssl_line_number(reason).strip()]
    elif isinstance(exc, ConnectionError):
        message = "Connection failed"
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
            extra = [reason.strip()]
        else:
            extra = [" ".join(map(_clean_inner_request_message, inner.args))]
    elif isinstance(exc, ChunkedEncodingError):
        message = "Connection broken. The server declared chunked encoding but sent an invalid chunk"
        extra = [str(exc.args[0].args[1])]
    else:
        message = str(exc)
        extra = []
    return message, extra
