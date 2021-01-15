from typing import TYPE_CHECKING, Any, Callable, Collection, Dict, Optional, Type

import attr
import yaml
from typing_extensions import Protocol, runtime_checkable

from .utils import is_json_media_type, is_plain_text_media_type

if TYPE_CHECKING:
    from .models import Case


try:
    from yaml import CSafeDumper as SafeDumper
except ImportError:
    # pylint: disable=unused-import
    from yaml import SafeDumper  # type: ignore


SERIALIZERS = {}


@attr.s(slots=True)  # pragma: no mutate
class SerializerContext:
    """The context for serialization process.

    :ivar Case case: Generated example that is being processed.
    """

    case: "Case" = attr.ib()  # pragma: no mutate


@runtime_checkable
class Serializer(Protocol):
    """Transform generated data to a form supported by the transport layer.

    For example, to handle multipart payloads, we need to serialize them differently for
    `requests` and `werkzeug` transports.
    """

    def as_requests(self, context: SerializerContext, payload: Any) -> Any:
        raise NotImplementedError

    def as_werkzeug(self, context: SerializerContext, payload: Any) -> Any:
        raise NotImplementedError


def register(media_type: str, *, aliases: Collection[str] = ()) -> Callable[[Type[Serializer]], Type[Serializer]]:
    """Register a serializer for the given media type.

    Schemathesis uses ``requests`` for regular network calls and ``werkzeug`` for WSGI applications. Your serializer
    should have two methods, ``as_requests`` and ``as_werkzeug``, providing keyword arguments that Schemathesis will
    pass to ``requests.request`` and ``werkzeug.Client.open`` respectively.

    .. code-block:: python

        @register("text/csv")
        class CSVSerializer:
            def as_requests(self, context, value):
                return {"data": to_csv(value)}

            def as_werkzeug(self, context, value):
                return {"data": to_csv(value)}

    The primary purpose of serializers is to transform data from its Python representation to the format suitable
    for making an API call. The generated data structure depends on your schema, but its type matches
    Python equivalents to the JSON Schema types.

    """

    def wrapper(serializer: Type[Serializer]) -> Type[Serializer]:
        if not issubclass(serializer, Serializer):
            raise TypeError(
                f"`{serializer.__name__}` is not a valid serializer. "
                f"Check `schemathesis.serializers.Serializer` documentation for examples."
            )
        SERIALIZERS[media_type] = serializer
        for alias in aliases:
            SERIALIZERS[alias] = serializer
        return serializer

    return wrapper


def unregister(media_type: str) -> None:
    """Remove registered serializer for the given media type."""
    del SERIALIZERS[media_type]


def _to_json(value: Any) -> Dict[str, Any]:
    if value is None:
        # If the body is `None`, then the app expects `null`, but `None` is also the default value for the `json`
        # argument in `requests.request` and `werkzeug.Client.open` which makes these cases indistinguishable.
        # Therefore we explicitly create such payload
        return {"data": b"null"}
    return {"json": value}


@register("application/json")
class JSONSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> Any:
        return _to_json(value)

    def as_werkzeug(self, context: SerializerContext, value: Any) -> Any:
        return _to_json(value)


@register("text/yaml", aliases=("text/x-yaml", "application/x-yaml", "text/vnd.yaml"))
class YAMLSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> Any:
        return {"data": yaml.dump(value, Dumper=SafeDumper)}

    def as_werkzeug(self, context: SerializerContext, value: Any) -> Any:
        return {"data": yaml.dump(value, Dumper=SafeDumper)}


def _should_coerce_to_bytes(item: Any) -> bool:
    """Whether the item should be converted to bytes."""
    # These types are OK in forms, others should be coerced to bytes
    return not isinstance(item, (bytes, str, int))


def prepare_form_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Make the generated data suitable for sending as multipart.

    If the schema is loose, Schemathesis can generate data that can't be sent as multipart. In these cases,
    we convert it to bytes and send it as-is, ignoring any conversion errors.

    NOTE. This behavior might change in the future.
    """
    for name, value in data.items():
        if isinstance(value, list):
            data[name] = [to_bytes(item) if _should_coerce_to_bytes(item) else item for item in value]
        elif _should_coerce_to_bytes(value):
            data[name] = to_bytes(value)
    return data


def to_bytes(value: Any) -> bytes:
    """Convert the input value to bytes and ignore any conversion errors."""
    return str(value).encode(errors="ignore")


@register("multipart/form-data")
class MultipartSerializer:
    def as_requests(self, context: SerializerContext, value: Dict[str, Any]) -> Any:
        # Form data always is generated as a dictionary
        multipart = prepare_form_data(value)
        files, data = context.case.operation.prepare_multipart(multipart)
        return {"files": files, "data": data}

    def as_werkzeug(self, context: SerializerContext, value: Any) -> Any:
        return {"data": value}


@register("application/x-www-form-urlencoded")
class URLEncodedFormSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> Any:
        return {"data": value}

    def as_werkzeug(self, context: SerializerContext, value: Any) -> Any:
        return {"data": value}


@register("text/plain")
class TextSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> Any:
        return {"data": str(value).encode("utf8")}

    def as_werkzeug(self, context: SerializerContext, value: Any) -> Any:
        return {"data": str(value)}


@register("application/octet-stream")
class OctetStreamSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> Any:
        return {"data": value}

    def as_werkzeug(self, context: SerializerContext, value: Any) -> Any:
        return {"data": value}


def get(media_type: str) -> Optional[Type[Serializer]]:
    """Get an appropriate serializer for the given media type."""
    if is_json_media_type(media_type):
        media_type = "application/json"
    if is_plain_text_media_type(media_type):
        media_type = "text/plain"
    return SERIALIZERS.get(media_type)
