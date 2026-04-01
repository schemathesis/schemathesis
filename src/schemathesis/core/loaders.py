from __future__ import annotations

import http.client
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NoReturn

from schemathesis.core.errors import LoaderError, LoaderErrorKind, get_request_error_extras, get_request_error_message
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT, USER_AGENT

if TYPE_CHECKING:
    import requests


def prepare_request_kwargs(kwargs: dict[str, Any]) -> None:
    """Prepare common request kwargs."""
    headers = kwargs.setdefault("headers", {})
    if "user-agent" not in {header.lower() for header in headers}:
        kwargs["headers"]["User-Agent"] = USER_AGENT


def handle_request_error(exc: requests.RequestException) -> NoReturn:
    """Handle request-level errors."""
    import requests

    url = exc.request.url if exc.request is not None else None
    if isinstance(exc, requests.exceptions.SSLError):
        kind = LoaderErrorKind.CONNECTION_SSL
    elif isinstance(exc, requests.exceptions.ConnectionError):
        kind = LoaderErrorKind.CONNECTION_OTHER
    else:
        kind = LoaderErrorKind.NETWORK_OTHER
    raise LoaderError(
        message=get_request_error_message(exc),
        kind=kind,
        url=url,
        extras=get_request_error_extras(exc),
    ) from exc


def raise_for_status(response: requests.Response) -> requests.Response:
    """Handle response status codes."""
    status_code = response.status_code
    if status_code < 400:
        return response

    reason = http.client.responses.get(status_code, "Unknown")
    if status_code >= 500:
        message = f"Failed to load schema due to server error (HTTP {status_code} {reason})"
        kind = LoaderErrorKind.HTTP_SERVER_ERROR
    else:
        message = f"Failed to load schema due to client error (HTTP {status_code} {reason})"
        kind = (
            LoaderErrorKind.HTTP_FORBIDDEN
            if status_code == 403
            else LoaderErrorKind.HTTP_NOT_FOUND
            if status_code == 404
            else LoaderErrorKind.HTTP_CLIENT_ERROR
        )
    raise LoaderError(message=message, kind=kind, url=response.request.url, extras=[])


def make_request(func: Callable[..., requests.Response], url: str, **kwargs: Any) -> requests.Response:
    """Make HTTP request with error handling."""
    import requests

    try:
        response = func(url, **kwargs)
        return raise_for_status(response)
    except requests.RequestException as exc:
        handle_request_error(exc)
    except OSError as exc:
        # Possible with certificate errors
        raise LoaderError(message=str(exc), kind=LoaderErrorKind.INVALID_CERTIFICATE, url=url, extras=[]) from None


WAIT_FOR_SCHEMA_INTERVAL = 0.05


class _ServiceUnavailableError(Exception):
    """Internal: HTTP 503 during schema load, eligible for retry under wait_for_schema."""


def load_from_url(
    func: Callable[..., requests.Response],
    *,
    url: str,
    wait_for_schema: float | None = None,
    **kwargs: Any,
) -> requests.Response:
    """Load schema from URL with retries."""
    import requests

    kwargs.setdefault("timeout", DEFAULT_RESPONSE_TIMEOUT)
    prepare_request_kwargs(kwargs)

    if wait_for_schema is not None:
        from tenacity import retry, retry_if_exception_type, stop_after_delay, wait_fixed

        def _func(url_: str, **kw: Any) -> requests.Response:
            response = func(url_, **kw)
            if response.status_code == 503:
                raise _ServiceUnavailableError
            return response

        retried = retry(
            wait=wait_fixed(WAIT_FOR_SCHEMA_INTERVAL),
            stop=stop_after_delay(wait_for_schema),
            retry=retry_if_exception_type((requests.exceptions.ConnectionError, _ServiceUnavailableError)),
            reraise=True,
        )(_func)

        try:
            return make_request(retried, url, **kwargs)
        except _ServiceUnavailableError:
            raise LoaderError(
                message="Failed to load schema due to server error (HTTP 503 Service Unavailable)",
                kind=LoaderErrorKind.HTTP_SERVER_ERROR,
                url=url,
                extras=[],
            ) from None

    return make_request(func, url, **kwargs)


def require_relative_url(url: str) -> None:
    """Raise an error if the URL is not relative."""
    # Deliberately simplistic approach
    if "://" in url or url.startswith("//"):
        raise ValueError("Schema path should be relative for WSGI/ASGI loaders")
