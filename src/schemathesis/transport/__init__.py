from __future__ import annotations

from inspect import iscoroutinefunction
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from schemathesis.core.transport import Response
from schemathesis.transport.asgi import ASGITransport
from schemathesis.transport.requests import RequestsTransport
from schemathesis.transport.wsgi import WSGITransport

if TYPE_CHECKING:
    from ..models import Case


def get(app: Any) -> Transport:
    """Get transport to send the data to the application."""
    if app is None:
        return RequestsTransport()
    if iscoroutinefunction(app) or (
        hasattr(app, "__call__") and iscoroutinefunction(app.__call__)  # noqa: B004
    ):
        return ASGITransport(app=app)
    return WSGITransport(app=app)


S = TypeVar("S", contravariant=True)


class Transport(Protocol[S]):
    def serialize_case(
        self,
        case: Case,
        *,
        base_url: str | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def send(
        self,
        case: Case,
        *,
        session: S | None = None,
        base_url: str | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Response:
        raise NotImplementedError
