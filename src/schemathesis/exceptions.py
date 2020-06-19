from hashlib import sha1
from typing import Dict, Type, Union

import attr
import requests
from jsonschema import ValidationError

from .utils import WSGIResponse


class CheckFailed(AssertionError):
    """Custom error type to distinguish from arbitrary AssertionError that may happen in the dependent libraries."""


CACHE: Dict[Union[str, int], Type[CheckFailed]] = {}


def get_exception(name: str) -> Type[CheckFailed]:
    """Create a new exception class with provided name or fetch one from cache."""
    if name in CACHE:
        exception_class = CACHE[name]
    else:
        exception_class = type(name, (CheckFailed,), {})
        CACHE[name] = exception_class
    return exception_class


def _get_hashed_exception(prefix: str, message: str) -> Type[CheckFailed]:
    """Give different exceptions for different error messages."""
    messages_digest = sha1(message.encode("utf-8")).hexdigest()
    name = f"{prefix}{messages_digest}"
    return get_exception(name)


def get_grouped_exception(*exceptions: AssertionError) -> Type[CheckFailed]:
    messages = [exception.args[0] for exception in exceptions]
    message = "".join(messages)
    return _get_hashed_exception("GroupedException", message)


def get_status_code_error(status_code: int) -> Type[CheckFailed]:
    """Return new exception for an unexpected status code."""
    name = f"StatusCodeError{status_code}"
    return get_exception(name)


def get_response_type_error(expected: str, received: str) -> Type[CheckFailed]:
    """Return new exception for an unexpected response type."""
    name = f"SchemaValidationError{expected}_{received}"
    return get_exception(name)


def get_schema_validation_error(exception: ValidationError) -> Type[CheckFailed]:
    """Return new exception for schema validation error."""
    return _get_hashed_exception("SchemaValidationError", str(exception))


class InvalidSchema(Exception):
    """Schema associated with an endpoint contains an error."""


@attr.s  # pragma: no mutate
class HTTPError(Exception):
    response: Union[requests.Response, WSGIResponse] = attr.ib()  # pragma: no mutate
    url: str = attr.ib()  # pragma: no mutate
