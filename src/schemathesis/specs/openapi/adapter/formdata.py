from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from schemathesis.core import media_types
from schemathesis.specs.openapi.adapter.parameters import COMBINED_FORM_DATA_MARKER

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


def prepare_multipart_v2(
    operation: APIOperation, form_data: Dict[str, Any]
) -> Tuple[Optional[List[Tuple[str, Any]]], Optional[Dict[str, Any]]]:
    files: List[Tuple[str, Any]] = []
    data: Dict[str, Any] = {}
    is_multipart = "multipart/form-data" in operation.schema.get_request_payload_content_types(operation)

    known_fields: Dict[str, Dict[str, Any]] = {}
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
    operation: APIOperation, form_data: Dict[str, Any]
) -> Tuple[Optional[List[Tuple[str, Any]]], Optional[Dict[str, Any]]]:
    files: List[Tuple[str, Any]] = []
    schema: Dict[str, Any] = {}
    for body in operation.body:
        main, sub = media_types.parse(body.media_type)
        if main in ("*", "multipart") and sub in ("*", "form-data", "mixed"):
            schema = body.definition.get("schema", {}) or {}
            break

    for name, value in form_data.items():
        property_schema = schema.get("properties", {}).get(name)
        if property_schema:
            if isinstance(value, list):
                files.extend((name, item) for item in value)
            elif property_schema.get("format") in ("binary", "base64"):
                files.append((name, value))
            else:
                files.append((name, (None, value)))
        elif isinstance(value, list):
            files.extend((name, item) for item in value)
        else:
            files.append((name, (None, value)))
    return files or None, None
