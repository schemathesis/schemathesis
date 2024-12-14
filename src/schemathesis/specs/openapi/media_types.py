from __future__ import annotations

from typing import TYPE_CHECKING, Any, Collection

from schemathesis.transport import SerializationContext
from schemathesis.transport.asgi import ASGI_TRANSPORT
from schemathesis.transport.requests import REQUESTS_TRANSPORT
from schemathesis.transport.wsgi import WSGI_TRANSPORT

if TYPE_CHECKING:
    from hypothesis import strategies as st


MEDIA_TYPES: dict[str, st.SearchStrategy[bytes]] = {}


def register_media_type(name: str, strategy: st.SearchStrategy[bytes], *, aliases: Collection[str] = ()) -> None:
    """Register a strategy for the given media type."""

    @REQUESTS_TRANSPORT.serializer(name, *aliases)
    @ASGI_TRANSPORT.serializer(name, *aliases)
    @WSGI_TRANSPORT.serializer(name, *aliases)
    def serialize(ctx: SerializationContext, value: Any) -> dict[str, Any]:
        return {"data": value}

    MEDIA_TYPES[name] = strategy
    for alias in aliases:
        MEDIA_TYPES[alias] = strategy


def unregister_all() -> None:
    MEDIA_TYPES.clear()
