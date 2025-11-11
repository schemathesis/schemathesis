from __future__ import annotations

from typing import TYPE_CHECKING, Any

from schemathesis.core import media_types
from schemathesis.specs.openapi.adapter.parameters import COMBINED_FORM_DATA_MARKER

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


def prepare_multipart_v2(
    operation: APIOperation, form_data: dict[str, Any], selected_content_types: dict[str, str] | None = None
) -> tuple[list[tuple[str, Any]] | None, dict[str, Any] | None]:
    files: list[tuple[str, Any]] = []
    data: dict[str, Any] = {}
    is_multipart = "multipart/form-data" in operation.schema.get_request_payload_content_types(operation)

    known_fields: dict[str, dict[str, Any]] = {}
    for parameter in operation.body:
        if COMBINED_FORM_DATA_MARKER in parameter.definition:
            known_fields.update(parameter.definition["schema"].get("properties", {}))

    def add_file(name: str, value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                files.append((name, (None, item)))
        else:
            files.append((name, value))

    for name, value in form_data.items():
        parameter_schema = known_fields.get(name)
        if parameter_schema:
            if parameter_schema.get("type") == "file" or is_multipart:
                add_file(name, value)
            else:
                data[name] = value
        else:
            add_file(name, value)
    return files or None, data or None


def prepare_multipart_v3(
    operation: APIOperation, form_data: dict[str, Any], selected_content_types: dict[str, str] | None = None
) -> tuple[list[tuple[str, Any]] | None, dict[str, Any] | None]:
    files: list[tuple[str, Any]] = []
    schema: dict[str, Any] = {}
    body_param = None
    for body in operation.body:
        main, sub = media_types.parse(body.media_type)
        if main in ("*", "multipart") and sub in ("*", "form-data", "mixed"):
            schema = body.definition.get("schema", {}) or {}
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

        if property_schema:
            if isinstance(value, list):
                if content_type:
                    files.extend((name, (None, item, content_type)) for item in value)
                else:
                    files.extend((name, item) for item in value)
            elif property_schema.get("format") in ("binary", "base64"):
                if content_type:
                    files.append((name, (None, value, content_type)))
                else:
                    files.append((name, value))
            else:
                if content_type:
                    files.append((name, (None, value, content_type)))
                else:
                    files.append((name, (None, value)))
        elif isinstance(value, list):
            if content_type:
                files.extend((name, (None, item, content_type)) for item in value)
            else:
                files.extend((name, item) for item in value)
        else:
            if content_type:
                files.append((name, (None, value, content_type)))
            else:
                files.append((name, (None, value)))
    return files or None, None
