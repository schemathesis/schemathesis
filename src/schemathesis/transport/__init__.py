from __future__ import annotations

from dataclasses import dataclass
from inspect import iscoroutinefunction
from typing import TYPE_CHECKING, Any, Callable, Generic, Iterator, TypeVar, Union

from schemathesis.core import media_types
from schemathesis.core.errors import SerializationNotPossible

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case


def get(app: Any) -> BaseTransport:
    """Get transport to send the data to the application."""
    from schemathesis.transport.asgi import ASGI_TRANSPORT
    from schemathesis.transport.requests import REQUESTS_TRANSPORT
    from schemathesis.transport.wsgi import WSGI_TRANSPORT

    if app is None:
        return REQUESTS_TRANSPORT
    if iscoroutinefunction(app) or (
        hasattr(app, "__call__") and iscoroutinefunction(app.__call__)  # noqa: B004
    ):
        return ASGI_TRANSPORT
    return WSGI_TRANSPORT


S = TypeVar("S", contravariant=True)


@dataclass
class SerializationContext:
    """Context object passed to serializer functions.

    It provides access to the generated test case and any related metadata.
    """

    case: Case
    """The generated test case."""

    __slots__ = ("case",)


Serializer = Callable[[SerializationContext, Any], Any]


class BaseTransport(Generic[S]):
    """Base implementation with serializer registration."""

    def __init__(self) -> None:
        self._serializers: dict[str, Serializer] = {}

    def serialize_case(self, case: Case, **kwargs: Any) -> dict[str, Any]:
        """Prepare the case for sending."""
        raise NotImplementedError

    def send(self, case: Case, *, session: S | None = None, **kwargs: Any) -> Response:
        """Send the case using this transport."""
        raise NotImplementedError

    def serializer(self, *media_types: str) -> Callable[[Serializer], Serializer]:
        """Register a serializer for given media types."""

        def decorator(func: Serializer) -> Serializer:
            for media_type in media_types:
                self._serializers[media_type] = func
            return func

        return decorator

    def unregister_serializer(self, *media_types: str) -> None:
        for media_type in media_types:
            self._serializers.pop(media_type, None)

    def _copy_serializers_from(self, transport: BaseTransport) -> None:
        self._serializers.update(transport._serializers)

    def get_first_matching_media_type(self, media_type: str) -> tuple[str, Serializer] | None:
        return next(self.get_matching_media_types(media_type), None)

    def get_matching_media_types(self, media_type: str) -> Iterator[tuple[str, Serializer]]:
        """Get all registered media types matching the given media type."""
        if media_type == "*/*":
            # Shortcut to avoid comparing all values
            yield from iter(self._serializers.items())
        else:
            main, sub = media_types.parse(media_type)
            checks = [
                media_types.is_json,
                media_types.is_xml,
                media_types.is_plain_text,
                media_types.is_yaml,
            ]
            for registered_media_type, serializer in self._serializers.items():
                # Try known variations for popular media types and fallback to comparison
                if any(check(media_type) and check(registered_media_type) for check in checks):
                    yield media_type, serializer
                else:
                    target_main, target_sub = media_types.parse(registered_media_type)
                    if main in ("*", target_main) and sub in ("*", target_sub):
                        yield registered_media_type, serializer

    def _get_serializer(self, input_media_type: str) -> Serializer:
        pair = self.get_first_matching_media_type(input_media_type)
        if pair is None:
            # This media type is set manually. Otherwise, it should have been rejected during the data generation
            raise SerializationNotPossible.for_media_type(input_media_type)
        return pair[1]


_Serializer = Callable[[SerializationContext, Any], Union[bytes, None]]


def serializer(*media_types: str) -> Callable[[_Serializer], None]:
    """Register a serializer for specified media types on HTTP, ASGI, and WSGI transports.

    Args:
        *media_types: One or more MIME types (e.g., "application/json") this serializer handles.

    Returns:
        A decorator that wraps a function taking `(ctx: SerializationContext, value: Any)` and returning `bytes` for serialized body and `None` for omitting request body.

    """

    def register(func: _Serializer) -> None:
        from schemathesis.transport.asgi import ASGI_TRANSPORT
        from schemathesis.transport.requests import REQUESTS_TRANSPORT
        from schemathesis.transport.wsgi import WSGI_TRANSPORT

        @ASGI_TRANSPORT.serializer(*media_types)
        @REQUESTS_TRANSPORT.serializer(*media_types)
        @WSGI_TRANSPORT.serializer(*media_types)
        def inner(ctx: SerializationContext, value: Any) -> dict[str, bytes]:
            result = {}
            serialized = func(ctx, value)
            if serialized is not None:
                result["data"] = serialized
            return result

    return register
