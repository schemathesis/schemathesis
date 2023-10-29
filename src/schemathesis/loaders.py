from __future__ import annotations
import http.client
import re
from typing import Callable, TypeVar, cast, TYPE_CHECKING

from .exceptions import SchemaError, SchemaErrorType

if TYPE_CHECKING:
    from .transports.responses import GenericResponse

R = TypeVar("R", bound="GenericResponse")


def remove_ssl_line_number(text: str) -> str:
    return re.sub(r"\(_ssl\.c:\d+\)", "", text)


def load_schema_from_url(loader: Callable[[], R]) -> R:
    import requests

    try:
        response = loader()
    except requests.RequestException as exc:
        request = cast(requests.PreparedRequest, exc.request)
        if isinstance(exc, requests.exceptions.SSLError):
            message = "SSL verification problem"
            type_ = SchemaErrorType.CONNECTION_SSL
            reason = str(exc.args[0].reason)
            extra = [remove_ssl_line_number(reason)]
        elif isinstance(exc, requests.exceptions.ConnectionError):
            message = "Connection failed"
            type_ = SchemaErrorType.CONNECTION_OTHER
            _, reason = exc.args[0].reason.args[0].split(":", maxsplit=1)
            extra = [reason.strip()]
        else:
            message = "Network problem"
            type_ = SchemaErrorType.NETWORK_OTHER
            extra = []
        raise SchemaError(message=message, type=type_, url=request.url, response=exc.response, extras=extra) from exc
    _raise_for_status(response)
    return response


def _raise_for_status(response: "GenericResponse") -> None:
    status_code = response.status_code
    reason = http.client.responses.get(status_code, "Unknown")
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
        return None
    raise SchemaError(message=message, type=type_, url=response.request.url, response=response, extras=[])
