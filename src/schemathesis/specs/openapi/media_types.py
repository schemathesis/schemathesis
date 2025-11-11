from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any

from schemathesis.transport import SerializationContext
from schemathesis.transport.asgi import ASGI_TRANSPORT
from schemathesis.transport.requests import REQUESTS_TRANSPORT
from schemathesis.transport.wsgi import WSGI_TRANSPORT

if TYPE_CHECKING:
    from hypothesis import strategies as st


MEDIA_TYPES: dict[str, st.SearchStrategy[bytes]] = {}


def register_media_type(name: str, strategy: st.SearchStrategy[bytes], *, aliases: Collection[str] = ()) -> None:
    r"""Register a custom Hypothesis strategy for generating media type content.

    Args:
        name: Media type name that matches your OpenAPI requestBody content type
        strategy: Hypothesis strategy that generates bytes for this media type
        aliases: Additional media type names that use the same strategy

    Example:
        ```python
        import schemathesis
        from hypothesis import strategies as st

        # Register PDF file strategy
        pdf_strategy = st.sampled_from([
            b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n>>\nendobj\n%%EOF",
            b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<\n/Type /Catalog\n>>\nendobj\n%%EOF"
        ])
        schemathesis.openapi.media_type("application/pdf", pdf_strategy)

        # Dynamic content generation
        @st.composite
        def xml_content(draw):
            tag = draw(st.text(min_size=3, max_size=10))
            content = draw(st.text(min_size=1, max_size=50))
            return f"<?xml version='1.0'?><{tag}>{content}</{tag}>".encode()

        schemathesis.openapi.media_type("application/xml", xml_content())
        ```

    Schema usage:
        ```yaml
        requestBody:
          content:
            application/pdf:        # Uses your PDF strategy
              schema:
                type: string
                format: binary
            application/xml:        # Uses your XML strategy
              schema:
                type: string
                format: binary
        ```

    """

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
