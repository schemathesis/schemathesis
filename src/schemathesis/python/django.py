from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from schemathesis.core.errors import LoaderError, LoaderErrorKind


@contextmanager
def explain_disallowed_host(app: object, host: str) -> Generator[None, None, None]:
    """Turn Django's opaque 400 for a rejected `Host` header into an actionable message."""
    try:
        yield
    except LoaderError as error:
        if error.kind is not LoaderErrorKind.HTTP_CLIENT_ERROR:
            raise
        reason = _rejected_host_reason(app, host)
        if reason is None:
            raise
        raise LoaderError(error.kind, f"{error.message}\n\n{reason}", url=error.url, extras=error.extras) from None


def _rejected_host_reason(app: object, host: str) -> str | None:
    try:
        from django.conf import settings
        from django.core.exceptions import DisallowedHost
        from django.core.handlers.base import BaseHandler
        from django.http import HttpRequest
    except ImportError:
        return None

    if not isinstance(app, BaseHandler):
        return None

    # Asking Django itself keeps the verdict in sync with whatever the application applied.
    request = HttpRequest()
    request.META["HTTP_HOST"] = host
    try:
        request.get_host()
        return None
    except DisallowedHost:
        pass

    allowed_hosts = f"{settings.ALLOWED_HOSTS!r}"
    if settings.DEBUG and not settings.ALLOWED_HOSTS:
        allowed_hosts += " (empty with DEBUG on, which allows only '.localhost', '127.0.0.1' and '[::1]')"
    return (
        "Django rejected the request because its `Host` header is not in `ALLOWED_HOSTS`\n\n"
        f"    Host:          {host}\n"
        f"    ALLOWED_HOSTS: {allowed_hosts}\n\n"
        f"Add '{host}' to ALLOWED_HOSTS in the Django settings you use for testing"
    )
