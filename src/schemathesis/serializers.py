from typing import TYPE_CHECKING, Any, Callable, Collection, Dict, Optional, Type, Union

import attr
from typing_extensions import Protocol

from .utils import is_json_media_type

if TYPE_CHECKING:
    from .models import Case


SERIALIZERS = {}


@attr.s(slots=True)
class SerializerContext:
    """Context for serialization process."""

    case: "Case" = attr.ib()


class Serializer(Protocol):
    """Transform generated data to a form, supported by the transport layer.

    For example, to handle multipart forms we need to serialize them differently for
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

    Example:
        @register("text/csv")
        class CSVSerializer:

            def as_requests(self, context, value):
                payload = serialize_to_csv(value)
                return {"data": payload}

            def as_werkzeug(self, context, value):
                payload = serialize_to_csv(value)
                return {"data": payload}

    The primary purpose of serializers is to transform data from its intermediate representation to the format suitable
    for making an API call. The representation depends on your schema, but its type matches Python equivalents to the
    JSON Schema types.

    """

    def wrapper(function: Type[Serializer]) -> Type[Serializer]:
        SERIALIZERS[media_type] = function
        for alias in aliases:
            SERIALIZERS[alias] = function
        return function

    return wrapper


@register("application/json")
class JSONSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> Any:
        return {"json": value}

    def as_werkzeug(self, context: SerializerContext, value: Any) -> Any:
        return {"json": value}


def prepare_form_data(form_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    # form_data can be optional
    if form_data is not None:
        for name, value in form_data.items():
            if isinstance(value, list):
                form_data[name] = [
                    to_bytes(item) if not isinstance(item, (bytes, str, int)) else item for item in value
                ]
            elif not isinstance(value, (bytes, str, int)):
                form_data[name] = to_bytes(value)
    return form_data


def to_bytes(value: Union[str, bytes, int, bool, float]) -> bytes:
    return str(value).encode(errors="ignore")


@register("multipart/form-data")
class MultipartSerializer:
    def as_requests(self, context: SerializerContext, value: Dict[str, Any]) -> Any:
        # Form data always is generated as a dictionary
        multipart = prepare_form_data(value)
        files, data = context.case.endpoint.prepare_multipart(multipart)
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
    """Get appropriate serializer for the given media type."""
    if is_json_media_type(media_type):
        media_type = "application/json"
    return SERIALIZERS.get(media_type)
