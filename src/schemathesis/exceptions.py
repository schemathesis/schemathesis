from hashlib import sha1
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any, Callable, Dict, NoReturn, Optional, Type, Union

import attr
import hypothesis.errors
import requests
from jsonschema import ValidationError

from .constants import SERIALIZERS_SUGGESTION_MESSAGE
from .failures import FailureContext

if TYPE_CHECKING:
    from .utils import GenericResponse


class CheckFailed(AssertionError):
    """Custom error type to distinguish from arbitrary AssertionError that may happen in the dependent libraries."""

    __module__ = "builtins"
    context: Optional[FailureContext]

    def __init__(self, *args: Any, context: Optional[FailureContext] = None):
        super().__init__(*args)
        self.context = context


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


@attr.s(slots=True)
class InvalidSchema(Exception):
    """Schema associated with an API operation contains an error."""

    __module__ = "builtins"
    message: Optional[str] = attr.ib(default=None)
    path: Optional[str] = attr.ib(default=None)
    method: Optional[str] = attr.ib(default=None)
    full_path: Optional[str] = attr.ib(default=None)

    def as_failing_test_function(self) -> Callable:
        """Create a test function that will fail.

        This approach allows us to use default pytest reporting style for operation-level schema errors.
        """

        def actual_test(*args: Any, **kwargs: Any) -> NoReturn:
            __tracebackhide__ = True  # pylint: disable=unused-variable
            raise self

        return actual_test


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


class SchemaLoadingError(ValueError):
    """Failed to load an API schema."""


class NonCheckError(Exception):
    """An error happened in side the runner, but is not related to failed checks.

    Used primarily to not let Hypothesis to consider the test as flaky or detect multiple failures as we handle it
    on our side.
    """

    __module__ = "builtins"


class InternalError(Exception):
    """Internal error in Schemathesis."""

    __module__ = "builtins"


SERIALIZATION_NOT_POSSIBLE_MESSAGE = (
    f"Schemathesis can't serialize data to any of the defined media types: {{}} \n{SERIALIZERS_SUGGESTION_MESSAGE}"
)


class SerializationNotPossible(Exception):
    """Not possible to serialize to any of the media types defined for some API operation.

    Usually, there is still `application/json` along with less common ones, but this error happens when there is no
    media type that Schemathesis knows how to serialize data to.
    """

    __module__ = "builtins"

    @classmethod
    def from_media_types(cls, *media_types: str) -> "SerializationNotPossible":
        return cls(SERIALIZATION_NOT_POSSIBLE_MESSAGE.format(", ".join(media_types)))


class InvalidRegularExpression(Exception):
    __module__ = "builtins"


@attr.s  # pragma: no mutate
class HTTPError(Exception):
    response: "GenericResponse" = attr.ib()  # pragma: no mutate
    url: str = attr.ib()  # pragma: no mutate

    @classmethod
    def raise_for_status(cls, response: requests.Response) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise cls(response=response, url=response.url) from exc

    @classmethod
    def check_response(cls, response: requests.Response, schema_path: str) -> None:
        # Raising exception to provide unified behavior
        # E.g. it will be handled in CLI - a proper error message will be shown
        if 400 <= response.status_code < 600:
            raise cls(response=response, url=schema_path)


class UsageError(Exception):
    """Incorrect usage of Schemathesis functions."""
