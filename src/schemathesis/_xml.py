"""XML serialization."""
from copy import deepcopy
from io import StringIO
from typing import Any, Dict, List, Union

from .exceptions import UnboundPrefixError

Primitive = Union[str, int, float, bool, None]
JSON = Union[Primitive, List, Dict[str, Any]]


def _to_xml(value: Any, raw_schema: Dict[str, Any], resolved_schema: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize a generated Python object as an XML string.

    Schemas may contain additional information for fine-tuned XML serialization.

    :param value: Generated value
    :param raw_schema: The payload definition with not resolved references.
    :param resolved_schema: The payload definition with all references resolved.
    """
    if isinstance(value, bytes):
        return {"data": value}
    # TODO. Do not serialize data that leads to not valid XML - reject it
    tag = _get_xml_tag(raw_schema, resolved_schema)
    buffer = StringIO()
    # Collect all namespaces to ensure that all child nodes with prefixes have proper namespaces in their parent nodes
    namespace_stack: List[str] = []
    _write_xml(buffer, value, tag, resolved_schema, namespace_stack)
    return {"data": buffer.getvalue().encode("utf8")}


def _get_xml_tag(raw_schema: Dict[str, Any], resolved_schema: Dict[str, Any]) -> str:
    # On the top level we need to detect the proper XML tag, in other cases it is known from object properties
    if resolved_schema.get("xml", {}).get("name"):
        return resolved_schema["xml"]["name"]
    if "$ref" in raw_schema:
        return _get_tag_name_from_reference(raw_schema["$ref"])
    # TODO. what in this case?
    # Here we don't have any name for the payload schema - no reference or the `xml` property
    return "data"


def _write_xml(buffer: StringIO, value: JSON, tag: str, schema: Dict[str, Any], namespace_stack: List[str]) -> None:
    if isinstance(value, dict):
        _write_object(buffer, value, tag, schema, namespace_stack)
    elif isinstance(value, list):
        _write_array(buffer, value, tag, schema, namespace_stack)
    else:
        _write_primitive(buffer, value, tag, schema, namespace_stack)


def _validate_prefix(options: Dict[str, Any], namespace_stack: List[str]) -> None:
    try:
        prefix = options["prefix"]
        if prefix not in namespace_stack:
            raise UnboundPrefixError(prefix)
    except KeyError:
        pass


def _write_object(
    buffer: StringIO, obj: Dict[str, JSON], tag: str, schema: Dict[str, Any], namespace_stack: List[str]
) -> None:
    xml_options = schema.get("xml", {})
    if "namespace" in xml_options and "prefix" in xml_options:
        namespace_stack.append(xml_options["prefix"])
    if "prefix" in xml_options:
        tag = f"{xml_options['prefix']}:{tag}"
    buffer.write(f"<{tag}")
    if "namespace" in xml_options:
        _write_namespace(buffer, xml_options)
    attributes = []
    children_buffed = StringIO()
    properties = schema.get("properties", {})
    for child_name, value in obj.items():
        property_schema = properties.get(child_name, {})
        child_xml_options = property_schema.get("xml", {})
        if "namespace" in child_xml_options and "prefix" in child_xml_options:
            namespace_stack.append(child_xml_options["prefix"])
        child_tag = child_xml_options.get("name", child_name)
        if child_xml_options.get("prefix"):
            _validate_prefix(child_xml_options, namespace_stack)
            prefix = child_xml_options["prefix"]
            child_tag = f"{prefix}:{child_tag}"
        if child_xml_options.get("attribute", False):
            attributes.append(f'{child_tag}="{value}"')
            continue
        _write_xml(children_buffed, value, child_tag, property_schema, namespace_stack)
        if "namespace" in child_xml_options and "prefix" in child_xml_options:
            namespace_stack.pop()

    if attributes:
        buffer.write(f" {' '.join(attributes)}")
    buffer.write(">")
    buffer.write(children_buffed.getvalue())
    buffer.write(f"</{tag}>")
    if "namespace" in xml_options and "prefix" in xml_options:
        namespace_stack.pop()


def _write_array(
    buffer: StringIO, obj: List[JSON], tag: str, schema: Dict[str, Any], namespace_stack: List[str]
) -> None:
    xml_options = schema.get("xml", {})
    if "namespace" in xml_options and "prefix" in xml_options:
        namespace_stack.append(xml_options["prefix"])
    if xml_options.get("prefix"):
        tag = f"{xml_options['prefix']}:{tag}"
    wrapped = xml_options.get("wrapped", False)
    is_namespace_specified = False
    if wrapped:
        buffer.write(f"<{tag}")
        if "namespace" in xml_options:
            is_namespace_specified = True
            _write_namespace(buffer, xml_options)
        buffer.write(">")
    # In Open API `items` value should be an object and not an array
    items = deepcopy(schema.get("items", {}))
    child_xml_options = items.get("xml", {})
    child_tag = child_xml_options.get("name", tag)
    if not is_namespace_specified and "namespace" in xml_options:
        child_xml_options.setdefault("namespace", xml_options["namespace"])
    if "prefix" in xml_options:
        child_xml_options.setdefault("prefix", xml_options["prefix"])
    items["xml"] = child_xml_options
    _validate_prefix(child_xml_options, namespace_stack)
    for item in obj:
        _write_xml(buffer, item, child_tag, items, namespace_stack)
    if wrapped:
        buffer.write(f"</{tag}>")
    if "namespace" in xml_options and "prefix" in xml_options:
        namespace_stack.pop()


def _write_primitive(
    buffer: StringIO, obj: Primitive, tag: str, schema: Dict[str, Any], namespace_stack: List[str]
) -> None:
    xml_options = schema.get("xml", {})
    # There is no need for modifying the namespace stack, as we know that this function is terminal - it do not recurse
    # and this element don't have any children. Therefore checking the prefix is enough
    _validate_prefix(xml_options, namespace_stack)
    buffer.write(f"<{tag}")
    if "namespace" in xml_options:
        _write_namespace(buffer, xml_options)
    buffer.write(f">{obj}</{tag}>")


def _write_namespace(buffer: StringIO, options: Dict[str, Any]) -> None:
    buffer.write(" xmlns")
    if "prefix" in options:
        buffer.write(f":{options['prefix']}")
    buffer.write(f'="{options["namespace"]}"')


def _get_tag_name_from_reference(reference: str) -> str:
    """Extract object name from a reference."""
    # TODO. is it possible to have a list of 1 element here?
    return reference.rsplit("/", maxsplit=1)[1]
