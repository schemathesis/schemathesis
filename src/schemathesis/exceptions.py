from __future__ import annotations
import enum
import json
from dataclasses import dataclass, field
from hashlib import sha1
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any, Callable, Dict, Generator, List, NoReturn, Optional, Tuple, Type, Union

from .constants import SERIALIZERS_SUGGESTION_MESSAGE
from .failures import FailureContext

if TYPE_CHECKING:
    import hypothesis.errors
    from jsonschema import RefResolutionError, ValidationError
    from .utils import GenericResponse


class CheckFailed(AssertionError):
    """Custom error type to distinguish from arbitrary AssertionError that may happen in the dependent libraries."""

    __module__ = "builtins"
    context: Optional[FailureContext]
    causes: Optional[Tuple[Union["CheckFailed", AssertionError], ...]]

    def __init__(
        self,
        *args: Any,
        context: Optional[FailureContext] = None,
        causes: Optional[Tuple[Union["CheckFailed", AssertionError], ...]] = None,
    ):
        super().__init__(*args)
        self.context = context
        self.causes = causes


def make_unique_by_key(
    check_name: str, check_message: Optional[str], context: Optional[FailureContext]
) -> Tuple[Optional[str], ...]:
    """A key to distinguish different failed checks.

    It is not only based on `FailureContext`, because the end-user may raise plain `AssertionError` in their custom
    checks, and those won't have any context attached.
    """
    if context is not None:
        return context.unique_by_key(check_message)
    return check_name, check_message


def deduplicate_failed_checks(
    checks: List[Union[CheckFailed, AssertionError]]
) -> Generator[Union[CheckFailed, AssertionError], None, None]:
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


CACHE: Dict[Union[str, int], Type[CheckFailed]] = {}


def get_exception(name: str) -> Type[CheckFailed]:
    """Create a new exception class with provided name or fetch one from the cache."""
    if name in CACHE:
        exception_class = CACHE[name]
    else:
        exception_class = type(name, (CheckFailed,), {})
        exception_class.__qualname__ = CheckFailed.__name__
        exception_class.__name__ = CheckFailed.__name__
        CACHE[name] = exception_class
    return exception_class


def _get_hashed_exception(prefix: str, message: str) -> Type[CheckFailed]:
    """Give different exceptions for different error messages."""
    messages_digest = sha1(message.encode("utf-8")).hexdigest()
    name = f"{prefix}{messages_digest}"
    return get_exception(name)


def get_grouped_exception(prefix: str, *exceptions: AssertionError) -> Type[CheckFailed]:
    # The prefix is needed to distinguish multiple operations with the same error messages
    # that are coming from different operations
    messages = [exception.args[0] for exception in exceptions]
    message = "".join(messages)
    return _get_hashed_exception("GroupedException", f"{prefix}{message}")


def get_server_error(status_code: int) -> Type[CheckFailed]:
    """Return new exception for the Internal Server Error cases."""
    name = f"ServerError{status_code}"
    return get_exception(name)


def get_status_code_error(status_code: int) -> Type[CheckFailed]:
    """Return new exception for an unexpected status code."""
    name = f"StatusCodeError{status_code}"
    return get_exception(name)


def get_response_type_error(expected: str, received: str) -> Type[CheckFailed]:
    """Return new exception for an unexpected response type."""
    name = f"SchemaValidationError{expected}_{received}"
    return get_exception(name)


def get_malformed_media_type_error(media_type: str) -> Type[CheckFailed]:
    name = f"MalformedMediaType{media_type}"
    return get_exception(name)


def get_missing_content_type_error() -> Type[CheckFailed]:
    """Return new exception for a missing Content-Type header."""
    return get_exception("MissingContentTypeError")


def get_schema_validation_error(exception: ValidationError) -> Type[CheckFailed]:
    """Return new exception for schema validation error."""
    return _get_hashed_exception("SchemaValidationError", str(exception))


def get_response_parsing_error(exception: JSONDecodeError) -> Type[CheckFailed]:
    """Return new exception for response parsing error."""
    return _get_hashed_exception("ResponseParsingError", str(exception))


def get_headers_error(message: str) -> Type[CheckFailed]:
    """Return new exception for missing headers."""
    return _get_hashed_exception("MissingHeadersError", message)


def get_timeout_error(deadline: Union[float, int]) -> Type[CheckFailed]:
    """Request took too long."""
    return _get_hashed_exception("TimeoutError", str(deadline))


SCHEMA_ERROR_SUGGESTION = "Ensure that the definition complies with the OpenAPI specification"


@dataclass
class OperationSchemaError(Exception):
    """Schema associated with an API operation contains an error."""

    __module__ = "builtins"
    message: Optional[str] = None
    path: Optional[str] = None
    method: Optional[str] = None
    full_path: Optional[str] = None

    @classmethod
    def from_jsonschema_error(
        cls, error: ValidationError, path: Optional[str], method: Optional[str], full_path: Optional[str]
    ) -> "OperationSchemaError":
        if error.absolute_path:
            part = error.absolute_path[-1]
            if isinstance(part, int) and len(error.absolute_path) > 1:
                parent = error.absolute_path[-2]
                message = f"Invalid definition for element at index {part} in `{parent}`"
            else:
                message = f"Invalid `{part}` definition"
        else:
            message = "Invalid schema definition"
        error_path = " -> ".join((str(entry) for entry in error.path)) or "[root]"
        message += f"\n\nLocation:\n    {error_path}"
        instance = truncated_json(error.instance)
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
        cls, error: RefResolutionError, path: Optional[str], method: Optional[str], full_path: Optional[str]
    ) -> "OperationSchemaError":
        message = "Unresolvable JSON pointer in the schema"
        # Get the pointer value from "Unresolvable JSON pointer: 'components/UnknownParameter'"
        pointer = str(error).split(": ", 1)[-1]
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


def truncated_json(data: Any, max_lines: int = 10, max_width: int = 80) -> str:
    # Convert JSON to string with indentation
    indent = 4
    serialized = json.dumps(data, indent=indent)

    # Split string by lines

    lines = [line[: max_width - 3] + "..." if len(line) > max_width else line for line in serialized.split("\n")]

    if len(lines) <= max_lines:
        return "\n".join(lines)

    truncated_lines = lines[: max_lines - 1]
    indentation = " " * indent
    truncated_lines.append(f"{indentation}// Output truncated...")
    truncated_lines.append(lines[-1])

    return "\n".join(truncated_lines)


class DeadlineExceeded(Exception):
    """Test took too long to run."""

    __module__ = "builtins"

    @classmethod
    def from_exc(cls, exc: hypothesis.errors.DeadlineExceeded) -> "DeadlineExceeded":
        runtime = exc.runtime.total_seconds() * 1000
        deadline = exc.deadline.total_seconds() * 1000
        return cls(
            f"API response time is too slow! It took {runtime:.2f}ms, which exceeds the deadline of {deadline:.2f}ms.\n"
        )


@enum.unique
class SchemaErrorType(enum.Enum):
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
    UNEXPECTED_CONTENT_TYPE = "unexpected_content_type"
    YAML_NUMERIC_STATUS_CODES = "yaml_numeric_status_codes"
    YAML_NON_STRING_KEYS = "yaml_non_string_keys"

    # Open API validation
    OPEN_API_INVALID_SCHEMA = "open_api_invalid_schema"
    OPEN_API_UNSPECIFIED_VERSION = "open_api_unspecified_version"
    OPEN_API_UNSUPPORTED_VERSION = "open_api_unsupported_version"

    # Unclassified
    UNCLASSIFIED = "unclassified"


@dataclass
class SchemaError(RuntimeError):
    """Failed to load an API schema."""

    type: SchemaErrorType
    message: str
    url: Optional[str] = None
    response: Optional["GenericResponse"] = None
    extras: List[str] = field(default_factory=list)

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


class SerializationNotPossible(SerializationError):
    """Not possible to serialize to any of the media types defined for some API operation.

    Usually, there is still `application/json` along with less common ones, but this error happens when there is no
    media type that Schemathesis knows how to serialize data to.
    """

    __module__ = "builtins"

    @classmethod
    def from_media_types(cls, *media_types: str) -> "SerializationNotPossible":
        return cls(SERIALIZATION_NOT_POSSIBLE_MESSAGE.format(", ".join(media_types)))

    @classmethod
    def for_media_type(cls, media_type: str) -> "SerializationNotPossible":
        return cls(SERIALIZATION_FOR_TYPE_IS_NOT_POSSIBLE_MESSAGE.format(media_type))


class InvalidRegularExpression(Exception):
    __module__ = "builtins"


class UsageError(Exception):
    """Incorrect usage of Schemathesis functions."""
