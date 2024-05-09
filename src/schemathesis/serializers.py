from __future__ import annotations

import binascii
import os
from dataclasses import dataclass
from io import BytesIO
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Collection,
    Dict,
    Generator,
    Protocol,
    cast,
    runtime_checkable,
)

from ._xml import _to_xml
from .internal.copy import fast_deepcopy
from .internal.jsonschema import traverse_schema
from .transports.content_types import (
    is_json_media_type,
    is_plain_text_media_type,
    is_xml_media_type,
    parse_content_type,
)

if TYPE_CHECKING:
    from .models import Case


SERIALIZERS: dict[str, type[Serializer]] = {}


@dataclass
class Binary(str):
    """A wrapper around `bytes` to resolve OpenAPI and JSON Schema `format` discrepancies.

    Treat `bytes` as a valid type, allowing generation of bytes for OpenAPI `format` values like `binary` or `file`
    that JSON Schema expects to be strings.
    """

    data: bytes


@dataclass
class SerializerContext:
    """The context for serialization process.

    :ivar Case case: Generated example that is being processed.
    """

    case: Case

    @property
    def media_type(self) -> str:
        # `media_type` is a string, otherwise we won't serialize anything
        return cast(str, self.case.media_type)

    # Note on type casting below.
    # If we serialize data, then there should be non-empty definition for it in the first place
    # Therefore `schema` is never `None` if called from here. However, `APIOperation.get_raw_payload_schema` is
    # generic and can be called from other places where it may return `None`

    def get_raw_payload_schema(self) -> dict[str, Any]:
        schema = self.case.operation.get_raw_payload_schema(self.media_type)
        return cast(Dict[str, Any], schema)

    def get_resolved_payload_schema(self) -> dict[str, Any]:
        schema = self.case.operation.get_resolved_payload_schema(self.media_type)
        return cast(Dict[str, Any], schema)


@runtime_checkable
class Serializer(Protocol):
    """Transform generated data to a form supported by the transport layer.

    For example, to handle multipart payloads, we need to serialize them differently for
    `requests` and `werkzeug` transports.
    """

    def as_requests(self, context: SerializerContext, payload: Any) -> dict[str, Any]:
        raise NotImplementedError

    def as_werkzeug(self, context: SerializerContext, payload: Any) -> dict[str, Any]:
        raise NotImplementedError


def register(media_type: str, *, aliases: Collection[str] = ()) -> Callable[[type[Serializer]], type[Serializer]]:
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

    def wrapper(serializer: type[Serializer]) -> type[Serializer]:
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


def _to_json(value: Any) -> dict[str, Any]:
    if isinstance(value, bytes):
        # Possible to get via explicit examples, e.g. `externalValue`
        return {"data": value}
    if isinstance(value, Binary):
        return {"data": value.data}
    if value is None:
        # If the body is `None`, then the app expects `null`, but `None` is also the default value for the `json`
        # argument in `requests.request` and `werkzeug.Client.open` which makes these cases indistinguishable.
        # Therefore we explicitly create such payload
        return {"data": b"null"}
    return {"json": value}


@register("application/json", aliases=("text/json",))
class JSONSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> dict[str, Any]:
        return _to_json(value)

    def as_werkzeug(self, context: SerializerContext, value: Any) -> dict[str, Any]:
        return _to_json(value)


def _replace_binary(value: dict) -> dict:
    return {key: value.data if isinstance(value, Binary) else value for key, value in value.items()}


def _to_yaml(value: Any) -> dict[str, Any]:
    import yaml

    try:
        from yaml import CSafeDumper as SafeDumper
    except ImportError:
        from yaml import SafeDumper  # type: ignore

    if isinstance(value, bytes):
        return {"data": value}
    if isinstance(value, Binary):
        return {"data": value.data}
    if isinstance(value, (list, dict)):
        value = traverse_schema(value, _replace_binary)
    return {"data": yaml.dump(value, Dumper=SafeDumper)}


@register("text/yaml", aliases=("text/x-yaml", "text/vnd.yaml", "text/yml", "application/yaml", "application/x-yaml"))
class YAMLSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> dict[str, Any]:
        return _to_yaml(value)

    def as_werkzeug(self, context: SerializerContext, value: Any) -> dict[str, Any]:
        return _to_yaml(value)


@register("application/xml", aliases=("text/xml",))
class XMLSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> dict[str, Any]:
        return _to_xml(value, context.get_raw_payload_schema(), context.get_resolved_payload_schema())

    def as_werkzeug(self, context: SerializerContext, value: Any) -> dict[str, Any]:
        return _to_xml(value, context.get_raw_payload_schema(), context.get_resolved_payload_schema())


def _should_coerce_to_bytes(item: Any) -> bool:
    """Whether the item should be converted to bytes."""
    # These types are OK in forms, others should be coerced to bytes
    return isinstance(item, Binary) or not isinstance(item, (bytes, str, int))


def _prepare_form_data(data: dict[str, Any]) -> dict[str, Any]:
    """Make the generated data suitable for sending as multipart.

    If the schema is loose, Schemathesis can generate data that can't be sent as multipart. In these cases,
    we convert it to bytes and send it as-is, ignoring any conversion errors.

    NOTE. This behavior might change in the future.
    """
    for name, value in data.items():
        if isinstance(value, list):
            data[name] = [_to_bytes(item) if _should_coerce_to_bytes(item) else item for item in value]
        elif _should_coerce_to_bytes(value):
            data[name] = _to_bytes(value)
    return data


def _to_bytes(value: Any) -> bytes:
    """Convert the input value to bytes and ignore any conversion errors."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, Binary):
        return value.data
    return str(value).encode(errors="ignore")


def choose_boundary() -> str:
    """Random boundary name."""
    return binascii.hexlify(os.urandom(16)).decode("ascii")


def _encode_multipart(value: Any, boundary: str) -> bytes:
    """Encode any value as multipart.

    NOTE. It doesn't aim to be 100% correct multipart payload, but rather a way to send data which is not intended to
    be used as multipart, in cases when the API schema dictates so.
    """
    # For such cases we stringify the value and wrap it to a randomly-generated boundary
    body = BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    body.write(str(value).encode())
    body.write(f"--{boundary}--\r\n".encode("latin-1"))
    return body.getvalue()


@register("multipart/form-data", aliases=("multipart/mixed",))
class MultipartSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> dict[str, Any]:
        if isinstance(value, bytes):
            return {"data": value}
        if isinstance(value, dict):
            value = fast_deepcopy(value)
            multipart = _prepare_form_data(value)
            files, data = context.case.operation.prepare_multipart(multipart)
            return {"files": files, "data": data}
        # Uncommon schema. For example - `{"type": "string"}`
        boundary = choose_boundary()
        raw_data = _encode_multipart(value, boundary)
        content_type = f"multipart/form-data; boundary={boundary}"
        return {"data": raw_data, "headers": {"Content-Type": content_type}}

    def as_werkzeug(self, context: SerializerContext, value: Any) -> dict[str, Any]:
        return {"data": value}


@register("application/x-www-form-urlencoded")
class URLEncodedFormSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> dict[str, Any]:
        return {"data": value}

    def as_werkzeug(self, context: SerializerContext, value: Any) -> dict[str, Any]:
        return {"data": value}


@register("text/plain")
class TextSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> dict[str, Any]:
        if isinstance(value, bytes):
            return {"data": value}
        return {"data": str(value).encode("utf8")}

    def as_werkzeug(self, context: SerializerContext, value: Any) -> dict[str, Any]:
        if isinstance(value, bytes):
            return {"data": value}
        return {"data": str(value)}


@register("application/octet-stream")
class OctetStreamSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> dict[str, Any]:
        return {"data": _to_bytes(value)}

    def as_werkzeug(self, context: SerializerContext, value: Any) -> dict[str, Any]:
        return {"data": _to_bytes(value)}


def get_matching_media_types(media_type: str) -> Generator[str, None, None]:
    """Get all registered media types matching the given media type."""
    if media_type == "*/*":
        # Shortcut to avoid comparing all values
        yield from iter(SERIALIZERS)
    else:
        main, sub = parse_content_type(media_type)
        if main == "application" and (sub == "json" or sub.endswith("+json")):
            yield media_type
        else:
            for registered_media_type in SERIALIZERS:
                target_main, target_sub = parse_content_type(registered_media_type)
                if main in ("*", target_main) and sub in ("*", target_sub):
                    yield registered_media_type


def get_first_matching_media_type(media_type: str) -> str | None:
    return next(get_matching_media_types(media_type), None)


def get(media_type: str) -> type[Serializer] | None:
    """Get an appropriate serializer for the given media type."""
    if is_json_media_type(media_type):
        media_type = "application/json"
    if is_plain_text_media_type(media_type):
        media_type = "text/plain"
    if is_xml_media_type(media_type):
        media_type = "application/xml"
    return SERIALIZERS.get(media_type)
