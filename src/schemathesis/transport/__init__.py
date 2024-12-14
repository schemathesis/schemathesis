from __future__ import annotations

from dataclasses import dataclass
from inspect import iscoroutinefunction
from typing import Any, Callable, Generic, Iterator, TypeVar

from schemathesis.core import media_types
from schemathesis.core.errors import SerializationNotPossible


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


C = TypeVar("C", contravariant=True)
R = TypeVar("R", covariant=True)
S = TypeVar("S", contravariant=True)


@dataclass
class SerializationContext(Generic[C]):
    """Generic context for serialization process."""

    case: C

    __slots__ = ("case",)


Serializer = Callable[[SerializationContext[C], Any], Any]


class BaseTransport(Generic[C, R, S]):
    """Base implementation with serializer registration."""

    def __init__(self) -> None:
        self._serializers: dict[str, Serializer[C]] = {}

    def serialize_case(self, case: C, **kwargs: Any) -> dict[str, Any]:
        """Prepare the case for sending."""
        raise NotImplementedError

    def send(self, case: C, *, session: S | None = None, **kwargs: Any) -> R:
        """Send the case using this transport."""
        raise NotImplementedError

    def serializer(self, *media_types: str) -> Callable[[Serializer[C]], Serializer[C]]:
        """Register a serializer for given media types."""

        def decorator(func: Serializer[C]) -> Serializer[C]:
            for media_type in media_types:
                self._serializers[media_type] = func
            return func

        return decorator

    def unregister_serializer(self, *media_types: str) -> None:
        for media_type in media_types:
            self._serializers.pop(media_type, None)

    def _copy_serializers_from(self, transport: BaseTransport) -> None:
        self._serializers.update(transport._serializers)

    def get_first_matching_media_type(self, media_type: str) -> tuple[str, Serializer[C]] | None:
        return next(self.get_matching_media_types(media_type), None)

    def get_matching_media_types(self, media_type: str) -> Iterator[tuple[str, Serializer[C]]]:
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

    def _get_serializer(self, input_media_type: str) -> Serializer[C]:
        pair = self.get_first_matching_media_type(input_media_type)
        if pair is None:
            # This media type is set manually. Otherwise, it should have been rejected during the data generation
            raise SerializationNotPossible.for_media_type(input_media_type)
        return pair[1]
