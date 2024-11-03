from __future__ import annotations

from typing import TYPE_CHECKING, Callable, TypeVar

from .exceptions import SchemaError, SchemaErrorType
from .internal.exceptions import get_request_error_extras, get_request_error_message

if TYPE_CHECKING:
    from .transports.responses import GenericResponse

R = TypeVar("R", bound="GenericResponse")


def load_schema_from_url(loader: Callable[[], R]) -> R:
    import requests

    try:
        response = loader()
    except requests.RequestException as exc:
        url = exc.request.url if exc.request is not None else None
        if isinstance(exc, requests.exceptions.SSLError):
            type_ = SchemaErrorType.CONNECTION_SSL
        elif isinstance(exc, requests.exceptions.ConnectionError):
            type_ = SchemaErrorType.CONNECTION_OTHER
        else:
            type_ = SchemaErrorType.NETWORK_OTHER
        message = get_request_error_message(exc)
        extras = get_request_error_extras(exc)
        raise SchemaError(message=message, type=type_, url=url, response=exc.response, extras=extras) from exc
    _raise_for_status(response)
    return response


def _raise_for_status(response: GenericResponse) -> None:
    from .transports.responses import get_reason

    status_code = response.status_code
    reason = get_reason(status_code)
    if status_code >= 500:
        message = f"Failed to load schema due to server error (HTTP {status_code} {reason})"
        type_ = SchemaErrorType.HTTP_SERVER_ERROR
    elif status_code >= 400:
        message = f"Failed to load schema due to client error (HTTP {status_code} {reason})"
        if status_code == 403:
            type_ = SchemaErrorType.HTTP_FORBIDDEN
        elif status_code == 404:
            type_ = SchemaErrorType.HTTP_NOT_FOUND
        else:
            type_ = SchemaErrorType.HTTP_CLIENT_ERROR
    else:
        return
    raise SchemaError(message=message, type=type_, url=response.request.url, response=response, extras=[])
