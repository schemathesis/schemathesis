from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import jsonschema_rs

from schemathesis.core import media_types
from schemathesis.core.errors import MalformedMediaType
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY

if TYPE_CHECKING:
    from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter


def make_sse_content_validator(
    schema: dict[str, Any] | bool,
    adapter: SpecificationAdapter,
    deserialize_payload: Callable[[str, str], Any] | None = None,
) -> jsonschema_rs.Validator:
    """Build a JSON Schema validator with content-aware string decoding for SSE payloads."""
    keywords = _make_keywords(adapter.jsonschema_validator_cls, schema, deserialize_payload)
    return adapter.jsonschema_validator_cls(schema, validate_formats=True, keywords=keywords)


def _make_keywords(
    validator_cls: type[jsonschema_rs.Validator],
    root_schema: dict[str, Any] | bool,
    deserialize_payload: Callable[[str, str], Any] | None,
) -> dict[str, type[Any]]:
    keywords: dict[str, type[Any]] = {}

    class ContentMediaTypeKeyword:
        def __init__(self, parent_schema: dict[str, Any], value: Any, _schema_path: list[str | int]) -> None:
            if not isinstance(value, str):
                raise ValueError("`contentMediaType` must be a string")
            # Validate schema-level media type value eagerly to report malformed schemas.
            media_types.parse(value)
            self.content_media_type = value
            self.content_schema_validator: jsonschema_rs.Validator | None = None
            content_schema = parent_schema.get("contentSchema")
            if content_schema is None:
                return
            schema_to_validate = _schema_with_bundle(content_schema, root_schema)
            self.content_schema_validator = validator_cls(
                schema_to_validate,
                validate_formats=True,
                keywords=keywords,
            )

        def validate(self, instance: Any) -> None:
            if self.content_schema_validator is None:
                return
            if not isinstance(instance, str):
                return
            if deserialize_payload is None:
                try:
                    if not media_types.is_json(self.content_media_type):
                        return
                except MalformedMediaType:
                    return
                try:
                    parsed = json.loads(instance)
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ValueError(f"must contain valid JSON ({exc})") from None
                self.content_schema_validator.validate(parsed)
                return
            try:
                parsed = deserialize_payload(self.content_media_type, instance)
            except NotImplementedError:
                # No matching deserializer: treat this media type as non-assertive.
                return
            except Exception as exc:
                raise ValueError(f"cannot deserialize payload for `{self.content_media_type}` ({exc})") from None
            self.content_schema_validator.validate(parsed)

    keywords["contentMediaType"] = ContentMediaTypeKeyword
    return keywords


def _schema_with_bundle(schema: dict[str, Any] | bool, root_schema: dict[str, Any] | bool) -> dict[str, Any] | bool:
    if not isinstance(schema, dict):
        return schema
    if not isinstance(root_schema, dict):
        return schema
    bundled = root_schema.get(BUNDLE_STORAGE_KEY)
    if bundled is None or BUNDLE_STORAGE_KEY in schema:
        return schema
    result = dict(schema)
    result[BUNDLE_STORAGE_KEY] = bundled
    return result
