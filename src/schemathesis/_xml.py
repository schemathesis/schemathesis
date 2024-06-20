"""XML serialization."""

from __future__ import annotations

from io import StringIO
from typing import Any, Dict, List, Union
from xml.etree import ElementTree

from .exceptions import UnboundPrefixError
from .internal.copy import fast_deepcopy

Primitive = Union[str, int, float, bool, None]
JSON = Union[Primitive, List, Dict[str, Any]]
DEFAULT_TAG_NAME = "data"
NAMESPACE_URL = "http://example.com/schema"


def _to_xml(value: Any, raw_schema: dict[str, Any] | None, resolved_schema: dict[str, Any] | None) -> dict[str, Any]:
    """Serialize a generated Python object as an XML string.

    Schemas may contain additional information for fine-tuned XML serialization.

    :param value: Generated value
    :param raw_schema: The payload definition with not resolved references.
    :param resolved_schema: The payload definition with all references resolved.
    """
    if isinstance(value, (bytes, str)):
        return {"data": value}
    tag = _get_xml_tag(raw_schema, resolved_schema)
    buffer = StringIO()
    # Collect all namespaces to ensure that all child nodes with prefixes have proper namespaces in their parent nodes
    namespace_stack: list[str] = []
    _write_xml(buffer, value, tag, resolved_schema, namespace_stack)
    data = buffer.getvalue()
    if not is_valid_xml(data):
        from hypothesis import reject

        reject()
    return {"data": data.encode("utf8")}


_from_string = ElementTree.fromstring


def is_valid_xml(data: str) -> bool:
    try:
        _from_string(f"<root xmlns:smp='{NAMESPACE_URL}'>{data}</root>")
        return True
    except ElementTree.ParseError:
        return False


def _get_xml_tag(raw_schema: dict[str, Any] | None, resolved_schema: dict[str, Any] | None) -> str:
    # On the top level we need to detect the proper XML tag, in other cases it is known from object properties
    if (resolved_schema or {}).get("xml", {}).get("name"):
        return (resolved_schema or {})["xml"]["name"]

    # Check if the name can be derived from a reference in the raw schema
    if "$ref" in (raw_schema or {}):
        return _get_tag_name_from_reference((raw_schema or {})["$ref"])

    # Here we don't have any name for the payload schema - no reference or the `xml` property
    return DEFAULT_TAG_NAME


def _write_xml(
    buffer: StringIO, value: JSON, tag: str, schema: dict[str, Any] | None, namespace_stack: list[str]
) -> None:
    if isinstance(value, dict):
        _write_object(buffer, value, tag, schema, namespace_stack)
    elif isinstance(value, list):
        _write_array(buffer, value, tag, schema, namespace_stack)
    else:
        _write_primitive(buffer, value, tag, schema, namespace_stack)


def _validate_prefix(options: dict[str, Any], namespace_stack: list[str]) -> None:
    try:
        prefix = options["prefix"]
        if prefix not in namespace_stack:
            raise UnboundPrefixError(prefix)
    except KeyError:
        pass


def push_namespace_if_any(namespace_stack: list[str], options: dict[str, Any]) -> None:
    if "namespace" in options and "prefix" in options:
        namespace_stack.append(options["prefix"])


def pop_namespace_if_any(namespace_stack: list[str], options: dict[str, Any]) -> None:
    if "namespace" in options and "prefix" in options:
        namespace_stack.pop()


def _write_object(
    buffer: StringIO, obj: dict[str, JSON], tag: str, schema: dict[str, Any] | None, stack: list[str]
) -> None:
    options = (schema or {}).get("xml", {})
    push_namespace_if_any(stack, options)
    if "prefix" in options:
        tag = f"{options['prefix']}:{tag}"
    buffer.write(f"<{tag}")
    if "namespace" in options:
        _write_namespace(buffer, options)
    attributes = []
    children_buffer = StringIO()
    properties = (schema or {}).get("properties", {})
    for child_name, value in obj.items():
        property_schema = properties.get(child_name, {})
        child_options = property_schema.get("xml", {})
        push_namespace_if_any(stack, child_options)
        child_tag = child_options.get("name", child_name)
        if child_options.get("prefix"):
            _validate_prefix(child_options, stack)
            prefix = child_options["prefix"]
            child_tag = f"{prefix}:{child_tag}"
        if child_options.get("attribute", False):
            attributes.append(f'{child_tag}="{value}"')
            continue
        _write_xml(children_buffer, value, child_tag, property_schema, stack)
        pop_namespace_if_any(stack, child_options)

    if attributes:
        buffer.write(f" {' '.join(attributes)}")
    buffer.write(">")
    buffer.write(children_buffer.getvalue())
    buffer.write(f"</{tag}>")
    pop_namespace_if_any(stack, options)


def _write_array(buffer: StringIO, obj: list[JSON], tag: str, schema: dict[str, Any] | None, stack: list[str]) -> None:
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
    items = fast_deepcopy((schema or {}).get("items", {}))
    child_options = items.get("xml", {})
    child_tag = child_options.get("name", tag)
    if not is_namespace_specified and "namespace" in options:
        child_options.setdefault("namespace", options["namespace"])
    if "prefix" in options:
        child_options.setdefault("prefix", options["prefix"])
    items["xml"] = child_options
    _validate_prefix(child_options, stack)
    for item in obj:
        _write_xml(buffer, item, child_tag, items, stack)
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
    buffer.write(f">{obj}</{tag}>")


def _write_namespace(buffer: StringIO, options: dict[str, Any]) -> None:
    buffer.write(" xmlns")
    if "prefix" in options:
        buffer.write(f":{options['prefix']}")
    buffer.write(f'="{options["namespace"]}"')


def _get_tag_name_from_reference(reference: str) -> str:
    """Extract object name from a reference."""
    return reference.rsplit("/", maxsplit=1)[1]
