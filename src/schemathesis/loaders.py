from __future__ import annotations

from typing import TYPE_CHECKING, Callable, TypeVar

from schemathesis.core.errors import LoaderError, LoaderErrorKind

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
            kind = LoaderErrorKind.CONNECTION_SSL
        elif isinstance(exc, requests.exceptions.ConnectionError):
            kind = LoaderErrorKind.CONNECTION_OTHER
        else:
            kind = LoaderErrorKind.NETWORK_OTHER
        message = get_request_error_message(exc)
        extras = get_request_error_extras(exc)
        raise LoaderError(message=message, kind=kind, url=url, response=exc.response, extras=extras) from exc
    _raise_for_status(response)
    return response


def _raise_for_status(response: GenericResponse) -> None:
    from .transports.responses import get_reason

    status_code = response.status_code
    reason = get_reason(status_code)
    if status_code >= 500:
        message = f"Failed to load schema due to server error (HTTP {status_code} {reason})"
        kind = LoaderErrorKind.HTTP_SERVER_ERROR
    elif status_code >= 400:
        message = f"Failed to load schema due to client error (HTTP {status_code} {reason})"
        if status_code == 403:
            kind = LoaderErrorKind.HTTP_FORBIDDEN
        elif status_code == 404:
            kind = LoaderErrorKind.HTTP_NOT_FOUND
        else:
            kind = LoaderErrorKind.HTTP_CLIENT_ERROR
    else:
        return
    raise LoaderError(message=message, kind=kind, url=response.request.url, response=response, extras=[])
