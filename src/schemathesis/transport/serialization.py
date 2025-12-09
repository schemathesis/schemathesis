from __future__ import annotations

import re
from io import StringIO
from typing import TYPE_CHECKING, Any
from unicodedata import normalize

from schemathesis.core.errors import UnboundPrefix
from schemathesis.core.transforms import transform

if TYPE_CHECKING:
    from schemathesis.core.compat import RefResolver
    from schemathesis.generation.case import Case


class Binary(str):
    """A wrapper around `bytes` to resolve OpenAPI and JSON Schema `format` discrepancies.

    Treat `bytes` as a valid type, allowing generation of bytes for OpenAPI `format` values like `binary` or `file`
    that JSON Schema expects to be strings.
    """

    __slots__ = ("data",)

    def __new__(cls, data: bytes) -> Binary:
        return super().__new__(cls)

    def __init__(self, data: bytes) -> None:
        self.data = data

    def __hash__(self) -> int:
        return hash(self.data)

    def __repr__(self) -> str:
        return f"Binary(data={self.data!r})"


def serialize_json(value: Any) -> dict[str, Any]:
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


def _replace_binary(value: dict) -> dict:
    return {key: value.data if isinstance(value, Binary) else value for key, value in value.items()}


def serialize_binary(value: Any) -> bytes:
    """Convert the input value to bytes and ignore any conversion errors."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, Binary):
        return value.data
    return str(value).encode(errors="ignore")


def serialize_yaml(value: Any) -> dict[str, Any]:
    import yaml

    try:
        from yaml import CSafeDumper as SafeDumper
    except ImportError:
        from yaml import SafeDumper  # type: ignore[assignment]

    if isinstance(value, bytes):
        return {"data": value}
    if isinstance(value, Binary):
        return {"data": value.data}
    if isinstance(value, (list, dict)):
        value = transform(value, _replace_binary)
    return {"data": yaml.dump(value, Dumper=SafeDumper)}


Primitive = str | int | float | bool | None
JSON = Primitive | list | dict[str, Any]
DEFAULT_TAG_NAME = "data"
NAMESPACE_URL = "http://example.com/schema"


def serialize_xml(case: Case, value: Any) -> dict[str, Any]:
    media_type = case.media_type

    assert media_type is not None

    schema = None
    resource_name = None

    for body in case.operation.get_bodies_for_media_type(media_type):
        schema = body.optimized_schema
        resource_name = body.resource_name
        break
    assert schema is not None, (case.operation.body, media_type)

    return _serialize_xml(value, schema, resource_name=resource_name)


def _serialize_xml(value: Any, schema: dict[str, Any], resource_name: str | None) -> dict[str, Any]:
    """Serialize a generated Python object as an XML string.

    Schemas may contain additional information for fine-tuned XML serialization.
    """
    from schemathesis.core.compat import RefResolver

    if isinstance(value, (bytes, str)):
        return {"data": value}
    resolver = RefResolver.from_schema(schema)
    if "$ref" in schema:
        _, schema = resolver.resolve(schema["$ref"])
    tag = _get_xml_tag(schema, resource_name)
    buffer = StringIO()
    # Collect all namespaces to ensure that all child nodes with prefixes have proper namespaces in their parent nodes
    namespace_stack: list[str] = []
    _write_xml(buffer, value, tag, schema, namespace_stack, resolver)
    data = buffer.getvalue()
    return {"data": data.encode("utf8")}


def _get_xml_tag(schema: dict[str, Any] | None, resource_name: str | None) -> str:
    # On the top level we need to detect the proper XML tag, in other cases it is known from object properties
    if (schema or {}).get("xml", {}).get("name"):
        return (schema or {})["xml"]["name"]
    if resource_name is not None:
        return resource_name

    # Here we don't have any name for the payload schema - no reference or the `xml` property
    return DEFAULT_TAG_NAME


def _write_xml(
    buffer: StringIO,
    value: JSON,
    tag: str,
    schema: dict[str, Any] | None,
    namespace_stack: list[str],
    resolver: RefResolver,
) -> None:
    if isinstance(value, dict):
        _write_object(buffer, value, tag, schema, namespace_stack, resolver)
    elif isinstance(value, list):
        _write_array(buffer, value, tag, schema, namespace_stack, resolver)
    else:
        _write_primitive(buffer, value, tag, schema, namespace_stack)


def _validate_prefix(options: dict[str, Any], namespace_stack: list[str]) -> None:
    try:
        prefix = options["prefix"]
        if prefix not in namespace_stack:
            raise UnboundPrefix(prefix)
    except KeyError:
        pass


def push_namespace_if_any(namespace_stack: list[str], options: dict[str, Any]) -> None:
    if "namespace" in options and "prefix" in options:
        namespace_stack.append(options["prefix"])


def pop_namespace_if_any(namespace_stack: list[str], options: dict[str, Any]) -> None:
    if "namespace" in options and "prefix" in options:
        namespace_stack.pop()


def _write_object(
    buffer: StringIO,
    obj: dict[str, JSON],
    tag: str,
    schema: dict[str, Any] | None,
    stack: list[str],
    resolver: RefResolver,
) -> None:
    options = (schema or {}).get("xml", {})
    push_namespace_if_any(stack, options)
    tag = _sanitize_xml_name(tag)
    if "prefix" in options:
        tag = f"{options['prefix']}:{tag}"
    buffer.write(f"<{tag}")
    if "namespace" in options:
        _write_namespace(buffer, options)

    attribute_namespaces = {}
    attributes = {}
    children_buffer = StringIO()
    properties = (schema or {}).get("properties", {})
    for child_name, value in obj.items():
        property_schema = properties.get(child_name, {})
        if "$ref" in property_schema:
            _, property_schema = resolver.resolve(property_schema["$ref"])
        child_options = property_schema.get("xml", {})
        push_namespace_if_any(stack, child_options)
        child_tag = child_options.get("name", child_name)

        if child_options.get("attribute", False):
            if child_options.get("prefix") and child_options.get("namespace"):
                _validate_prefix(child_options, stack)
                prefix = child_options["prefix"]
                attr_name = f"{prefix}:{_sanitize_xml_name(child_tag)}"
                # Store namespace declaration
                attribute_namespaces[prefix] = child_options["namespace"]
            else:
                attr_name = _sanitize_xml_name(child_tag)

            if attr_name not in attributes:  # Only keep first occurrence
                attributes[attr_name] = f'{attr_name}="{_escape_xml(value)}"'
            continue

        child_tag = _sanitize_xml_name(child_tag)
        if child_options.get("prefix"):
            _validate_prefix(child_options, stack)
            prefix = child_options["prefix"]
            child_tag = f"{prefix}:{child_tag}"
        _write_xml(children_buffer, value, child_tag, property_schema, stack, resolver)
        pop_namespace_if_any(stack, child_options)

    # Write namespace declarations for attributes
    for prefix, namespace in attribute_namespaces.items():
        buffer.write(f' xmlns:{prefix}="{namespace}"')

    if attributes:
        buffer.write(f" {' '.join(attributes.values())}")
    buffer.write(">")
    buffer.write(children_buffer.getvalue())
    buffer.write(f"</{tag}>")
    pop_namespace_if_any(stack, options)


def _write_array(
    buffer: StringIO, obj: list[JSON], tag: str, schema: dict[str, Any] | None, stack: list[str], resolver: RefResolver
) -> None:
    options = (schema or {}).get("xml", {})
    push_namespace_if_any(stack, options)
    if options.get("prefix"):
        tag = f"{options['prefix']}:{tag}"
    wrapped = options.get("wrapped", False)
    is_namespace_specified = False
    if wrapped:
        buffer.write(f"<{tag}")
        if "namespace" in options:
            is_namespace_specified = True
            _write_namespace(buffer, options)
        buffer.write(">")
    # In Open API `items` value should be an object and not an array
    if schema:
        items = dict(schema.get("items", {}))
    else:
        items = {}
    if "$ref" in items:
        _, items = resolver.resolve(items["$ref"])
    child_options = items.get("xml", {})
    child_tag = child_options.get("name", tag)
    if not is_namespace_specified and "namespace" in options:
        child_options.setdefault("namespace", options["namespace"])
    if "prefix" in options:
        child_options.setdefault("prefix", options["prefix"])
    items["xml"] = child_options
    _validate_prefix(child_options, stack)
    for item in obj:
        _write_xml(buffer, item, child_tag, items, stack, resolver)
    if wrapped:
        buffer.write(f"</{tag}>")
    pop_namespace_if_any(stack, options)


def _write_primitive(
    buffer: StringIO, obj: Primitive, tag: str, schema: dict[str, Any] | None, namespace_stack: list[str]
) -> None:
    xml_options = (schema or {}).get("xml", {})
    # There is no need for modifying the namespace stack, as we know that this function is terminal - it do not recurse
    # and this element don't have any children. Therefore, checking the prefix is enough
    _validate_prefix(xml_options, namespace_stack)
    buffer.write(f"<{tag}")
    if "namespace" in xml_options:
        _write_namespace(buffer, xml_options)
    buffer.write(f">{_escape_xml(obj)}</{tag}>")


def _write_namespace(buffer: StringIO, options: dict[str, Any]) -> None:
    buffer.write(" xmlns")
    if "prefix" in options:
        buffer.write(f":{options['prefix']}")
    buffer.write(f'="{options["namespace"]}"')


def _escape_xml(value: JSON) -> str:
    """Escape special characters in XML content."""
    if isinstance(value, (int, float, bool)):
        return str(value)
    if value is None:
        return ""

    # Filter out invalid XML characters
    cleaned = "".join(
        char
        for char in str(value)
        if (
            char in "\t\n\r"
            or 0x20 <= ord(char) <= 0xD7FF
            or 0xE000 <= ord(char) <= 0xFFFD
            or 0x10000 <= ord(char) <= 0x10FFFF
        )
    )

    replacements = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&apos;",
    }
    return "".join(replacements.get(c, c) for c in cleaned)


def _sanitize_xml_name(name: str) -> str:
    """Sanitize a string to be a valid XML element name."""
    if not name:
        return "element"

    name = normalize("NFKC", str(name))

    name = name.replace(":", "_")
    sanitized = re.sub(r"[^a-zA-Z0-9_\-.]", "_", name)

    if not sanitized[0].isalpha() and sanitized[0] != "_":
        sanitized = "x_" + sanitized

    return sanitized
