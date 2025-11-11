from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from inspect import iscoroutinefunction
from typing import TYPE_CHECKING, Any, Generic, TypeVar

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


_Serializer = Callable[[SerializationContext, Any], bytes | None]


class SerializerRegistry:
    """Registry for serializers with aliasing support."""

    def __call__(self, *media_types: str) -> Callable[[_Serializer], None]:
        """Register a serializer for specified media types on HTTP, ASGI, and WSGI transports.

        Args:
            *media_types: One or more MIME types (e.g., "application/json") this serializer handles.

        Returns:
            A decorator that wraps a function taking `(ctx: SerializationContext, value: Any)` and returning `bytes` for serialized body and `None` for omitting request body.

        Example:
            ```python
            @schemathesis.serializer("text/csv")
            def csv_serializer(ctx, value):
                # Convert value to CSV bytes
                return csv_bytes
            ```

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

    def alias(self, target: str | list[str], source: str) -> None:
        """Reuse an existing serializer for additional media types.

        Register alias(es) for a built-in or previously registered serializer without
        duplicating implementation.

        Args:
            target: Media type(s) to register as aliases
            source: Existing media type whose serializer to reuse

        Raises:
            ValueError: If source media type has no registered serializer
            ValueError: If target is empty

        Example:
            ```python
            # Reuse built-in YAML serializer for custom media type
            schemathesis.serializer.alias("application/custom+yaml", "application/yaml")

            # Reuse built-in JSON serializer for vendor-specific type
            schemathesis.serializer.alias("application/vnd.api+json", "application/json")

            # Register multiple aliases at once
            schemathesis.serializer.alias(
                ["application/x-json", "text/json"],
                "application/json"
            )
            ```

        """
        from schemathesis.transport.asgi import ASGI_TRANSPORT
        from schemathesis.transport.requests import REQUESTS_TRANSPORT
        from schemathesis.transport.wsgi import WSGI_TRANSPORT

        if not source:
            raise ValueError("Source media type cannot be empty")

        targets = [target] if isinstance(target, str) else target

        if not targets or any(not t for t in targets):
            raise ValueError("Target media type cannot be empty")

        # Get serializer from source (use requests transport as reference)
        pair = REQUESTS_TRANSPORT.get_first_matching_media_type(source)
        if pair is None:
            raise ValueError(f"No serializer found for media type: {source}")

        _, serializer_func = pair

        # Register for all targets across all transports
        for t in targets:
            REQUESTS_TRANSPORT._serializers[t] = serializer_func
            ASGI_TRANSPORT._serializers[t] = serializer_func
            WSGI_TRANSPORT._serializers[t] = serializer_func


serializer = SerializerRegistry()
serializer.__doc__ = """Registry for serializers with decorator and aliasing support.

Use as a decorator to register custom serializers:

    @schemathesis.serializer("text/csv")
    def csv_serializer(ctx, value):
        # Convert value to CSV bytes
        return csv_bytes

Or use the alias method to reuse built-in serializers:

    schemathesis.serializer.alias("application/custom+yaml", "application/yaml")
"""
