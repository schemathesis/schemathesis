from __future__ import annotations

from typing import TYPE_CHECKING, Any, Collection

if TYPE_CHECKING:
    from hypothesis import strategies as st


MEDIA_TYPES: dict[str, st.SearchStrategy[bytes]] = {}


def register_media_type(name: str, strategy: st.SearchStrategy[bytes], *, aliases: Collection[str] = ()) -> None:
    """Register a strategy for the given media type."""
    from ...serializers import SerializerContext, register

    @register(name, aliases=aliases)
    class MediaTypeSerializer:
        def as_requests(self, context: SerializerContext, value: Any) -> dict[str, Any]:
            return {"data": value}

        def as_werkzeug(self, context: SerializerContext, value: Any) -> dict[str, Any]:
            return {"data": value}

    MEDIA_TYPES[name] = strategy
    for alias in aliases:
        MEDIA_TYPES[alias] = strategy


def unregister_all() -> None:
    from ...serializers import unregister

    for media_type in MEDIA_TYPES:
        unregister(media_type)
    MEDIA_TYPES.clear()
