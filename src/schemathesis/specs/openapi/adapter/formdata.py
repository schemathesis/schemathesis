from __future__ import annotations

from typing import TYPE_CHECKING, Any

from schemathesis.core import media_types
from schemathesis.core.file_samples import file_extension
from schemathesis.transport.serialization import Binary

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


def prepare_multipart_v2(
    operation: APIOperation, form_data: dict[str, Any], selected_content_types: dict[str, str] | None = None
) -> tuple[list[tuple[str, Any]] | None, dict[str, Any] | None]:
    files: list[tuple[str, Any]] = []
    data: dict[str, Any] = {}
    selected = selected_content_types or {}
    is_multipart = "multipart/form-data" in operation.schema.get_request_payload_content_types(operation)

    known_fields: dict[str, dict[str, Any]] = {}
    for parameter in operation.body:
        schema = parameter.definition.get("schema")
        if isinstance(schema, dict):
            known_fields.update(schema.get("properties", {}))

    def add_part(name: str, value: Any, is_file: bool) -> None:
        content_type = selected.get(name)
        for item in value if isinstance(value, list) else [value]:
            # Only file fields carry a filename; servers read parts without one as plain form fields.
            filename = _filename(name, item) if is_file else None
            if content_type:
                files.append((name, (filename, item, content_type)))
            else:
                files.append((name, (filename, item)))

    for name, value in form_data.items():
        parameter_schema = known_fields.get(name)
        is_file = isinstance(parameter_schema, dict) and (
            parameter_schema.get("type") == "file" or _is_file_part(parameter_schema)
        )
        if is_file or is_multipart or parameter_schema is None:
            add_part(name, value, is_file)
        else:
            data[name] = value
    return files or None, data or None


def _is_file_part(property_schema: object) -> bool:
    """Whether a form property is sent as a file, carrying a filename in its `Content-Disposition`."""
    if not isinstance(property_schema, dict):
        return False
    # OpenAPI 3.1 spells `format: binary` as `contentMediaType: application/octet-stream` and `format: byte` as
    # `contentEncoding: base64`. Any other media type annotates string content and stays a plain form field.
    return (
        property_schema.get("format") in ("binary", "base64")
        or property_schema.get("contentMediaType") == "application/octet-stream"
        or property_schema.get("contentEncoding") == "base64"
    )


def _filename(name: str, value: object) -> str:
    """Field name plus the extension of the file format `value` carries, so servers can pick a decoder."""
    data = value.data if isinstance(value, Binary) else value
    extension = file_extension(data) if isinstance(data, bytes) else None
    return f"{name}.{extension}" if extension else name


def prepare_multipart_v3(
    operation: APIOperation, form_data: dict[str, Any], selected_content_types: dict[str, str] | None = None
) -> tuple[list[tuple[str, Any]] | None, dict[str, Any] | None]:
    files: list[tuple[str, Any]] = []
    schema: dict[str, Any] = {}
    body_param = None
    for body in operation.body:
        main, sub = media_types.parse(body.media_type)
        if main in ("*", "multipart") and sub in ("*", "form-data", "mixed"):
            schema_node = body.definition.get("schema")
            schema = schema_node if isinstance(schema_node, dict) else {}
            body_param = body
            break

    for name, value in form_data.items():
        property_schema = schema.get("properties", {}).get(name)
        # Use the selected content type if available, otherwise check encoding definition
        content_type = None
        if selected_content_types and name in selected_content_types:
            content_type = selected_content_types[name]
        elif body_param:
            content_type = body_param.get_property_content_type(name)

        if isinstance(property_schema, dict):
            if isinstance(value, list):
                if _is_file_part(property_schema.get("items")):
                    declared = body_param.get_property_filename(name) if body_param else None
                    for item in value:
                        filename = declared or _filename(name, item)
                        if content_type:
                            files.append((name, (filename, item, content_type)))
                        else:
                            files.append((name, (filename, item)))
                elif content_type:
                    files.extend((name, (None, item, content_type)) for item in value)
                else:
                    files.extend((name, (None, item)) for item in value)
            elif _is_file_part(property_schema):
                filename = (body_param.get_property_filename(name) if body_param else None) or _filename(name, value)
                if content_type:
                    files.append((name, (filename, value, content_type)))
                else:
                    files.append((name, (filename, value)))
            elif content_type:
                files.append((name, (None, value, content_type)))
            else:
                files.append((name, (None, value)))
        elif isinstance(value, list):
            if content_type:
                files.extend((name, (None, item, content_type)) for item in value)
            else:
                files.extend((name, (None, item)) for item in value)
        elif content_type:
            files.append((name, (None, value, content_type)))
        else:
            files.append((name, (None, value)))
    return files or None, None
