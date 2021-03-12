from hashlib import sha1
from json import JSONDecodeError
from typing import Any, Callable, Dict, List, NoReturn, Optional, Type, Union

import attr
import jsonschema

from .constants import SERIALIZERS_SUGGESTION_MESSAGE
from .specs.openapi.definitions import OPENAPI_30_VALIDATOR, SWAGGER_20_VALIDATOR
from .utils import GenericResponse


class CheckFailed(AssertionError):
    """Custom error type to distinguish from arbitrary AssertionError that may happen in the dependent libraries."""

    __module__ = "builtins"


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


def get_schema_validation_error(exception: jsonschema.ValidationError) -> Type[CheckFailed]:
    """Return new exception for schema validation error."""
    return _get_hashed_exception("SchemaValidationError", str(exception))


def get_response_parsing_error(exception: JSONDecodeError) -> Type[CheckFailed]:
    """Return new exception for response parsing error."""
    return _get_hashed_exception("ResponseParsingError", str(exception))


def get_headers_error(message: str) -> Type[CheckFailed]:
    """Return new exception for missing headers."""
    return _get_hashed_exception("MissingHeadersError", message)


class NonCheckError(Exception):
    """An error happened in side the runner, but is not related to failed checks.

    Used primarily to not let Hypothesis to consider the test as flaky or detect multiple failures as we handle it
    on our side.
    """

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
    response: GenericResponse = attr.ib()  # pragma: no mutate
    url: str = attr.ib()  # pragma: no mutate


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

    @classmethod
    def from_jsonschema(cls, exc: jsonschema.ValidationError, spec: str) -> "InvalidSchema":
        # TODO. We need some heuristics to choose the most clear error description among the error tree.
        # Not sure if it will work in all cases :( But errors like `"'query' is not one of ['header']"` are misleading
        # "'int' is not one of ['string', 'number', 'boolean', 'integer', 'array']" might work better
        # Ideas:
        #   - $ref validator errors are not that important among oneOf/anyOf errors. How to detect that the schema is
        #     `{"$ref": "#/definitions/jsonReference"}` ? by schema value? or check paths in the schema?
        reason = exc.message
        message = INVALID_SCHEMA_MESSAGE.format(
            spec=spec,
            location=f"schema{format_as_index(exc.absolute_path)}",
            value=exc.instance,
            reason=reason,
        )
        return cls(message)


def format_as_index(indices: List) -> str:
    if not indices:
        return ""
    return f"[{']['.join(repr(index) for index in indices)}]"


# TODO.
#   - improve formatting
#   - move to another module - it belongs to open api
#   - add the actual schema, why it is failed
#   - display all references, so people can follow them
#   - for "response schema" there should be a prefix
#   - Write an error message below
NUMERIC_STATUS_CODE_ERROR = ""
INVALID_SCHEMA_MESSAGE = (
    "\n\nYour schema does not conform to the {spec} specification!\n\n"
    "Location:\n\n    {location}\n\n"
    "Value:\n\n    {value}\n\n"
    "Reason:\n\n    {reason}"
)


def _is_numeric_status_code_error(exc: TypeError) -> bool:
    # TODO. Implement + doc why do we care (it is the most common schema error)
    return True


SCHEMA_ID_TO_NAME = {
    SWAGGER_20_VALIDATOR: "Swagger 2.0",
    OPENAPI_30_VALIDATOR: "Open API 3.0",
}


def validate_schema(instance: Dict[str, Any], validator: jsonschema.validators.Draft4Validator) -> None:
    """Validate API schema against the given metaschema.

    Exceptions, that happen during validation with `jsonschema` are very generic and often provide too much information
    about the problem. Here we adapt these exceptions to API schema validation context and make more concise
    error messages.
    """
    spec = SCHEMA_ID_TO_NAME[validator]
    try:
        errors = validator.iter_errors(instance)
        error = next(errors)
        if error is None:
            return None
        raise InvalidSchema.from_jsonschema(error, spec)
    except TypeError as exc:
        # Sometimes `jsonschema` raises exceptions on input that is not technically valid JSON but
        # a valid Python object. For example, in JSON, all object keys are strings, but in Python dictionaries
        # may have, e.g., integer keys, and they will be casted to strings during JSON serialization:
        #
        #   >>> json.dumps({200: "text"})
        #   '{"200": "text"}'
        #
        # For this reason, such schemas are not valid, and Schemathesis needs to report it.
        # The problem might be solved by serializing/deserializing the schema with:
        #
        #   >>> json.loads(json.dumps(schema))
        #
        # However, Schemathesis aims to report invalid schemas rather than trying to fix the input schema.
        # See more information about this `jsonschema` behavior in this issue:
        # https://github.com/Julian/jsonschema/pull/286
        if _is_numeric_status_code_error(exc):
            raise InvalidSchema(NUMERIC_STATUS_CODE_ERROR) from exc
        raise
