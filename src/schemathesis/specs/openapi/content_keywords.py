from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import jsonschema_rs

from schemathesis.core import media_types
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY

if TYPE_CHECKING:
    from collections.abc import Generator

    from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter


class ContentSchemaViolation(Exception):
    """Raised when a contentSchema validation fails.

    Carried as `__cause__` on the outer `jsonschema_rs.ValidationError` so the
    call site can distinguish content-schema failures from structural event failures
    and report them with the correct title and root schema.
    """

    def __init__(self, exc: jsonschema_rs.ValidationError, content_schema: dict[str, Any] | bool) -> None:
        self.original = exc
        self.content_schema = content_schema
        super().__init__(str(exc))


class SseValidator:
    """Cached SSE event validator that accepts a per-call payload deserializer.

    The validator is built once from the schema. Before each validation loop,
    callers inject the per-call `deserialize_payload` via `with_deserializer()`;
    the keyword instances read it via the back-reference to this object.
    """

    def __init__(self, schema: dict[str, Any] | bool, adapter: SpecificationAdapter) -> None:
        self._deserialize_payload: Callable[[str, str], Any] | None = None
        keywords = _make_keywords(adapter.jsonschema_validator_cls, schema, self)
        self._validator = adapter.jsonschema_validator_cls(schema, validate_formats=True, keywords=keywords)

    @contextmanager
    def with_deserializer(self, fn: Callable[[str, str], Any]) -> Generator[None, None, None]:
        """Temporarily inject a per-call payload deserializer for SSE content validation."""
        old = self._deserialize_payload
        self._deserialize_payload = fn
        try:
            yield
        finally:
            self._deserialize_payload = old

    def validate(self, instance: Any) -> None:
        self._validator.validate(instance)


def _make_keywords(
    validator_cls: type[jsonschema_rs.Validator],
    root_schema: dict[str, Any] | bool,
    owner: SseValidator,
) -> dict[str, type[Any]]:
    class ContentMediaTypeKeyword:
        def __init__(self, parent_schema: dict[str, Any], value: str, _schema_path: list[str | int]) -> None:
            # Validate schema-level media type value eagerly to report malformed schemas.
            media_types.parse(value)
            self.content_media_type = value
            # Store validator and its schema together; None means no contentSchema was declared.
            self._content: tuple[jsonschema_rs.Validator, dict[str, Any] | bool] | None = None
            content_schema = parent_schema.get("contentSchema")
            if content_schema is None:
                return
            resolved = _schema_with_bundle(content_schema, root_schema)
            self._content = (
                validator_cls(resolved, validate_formats=True, keywords={"contentMediaType": ContentMediaTypeKeyword}),
                resolved,
            )

        def _validate_parsed(
            self, parsed: Any, content_schema: dict[str, Any] | bool, validator: jsonschema_rs.Validator
        ) -> None:
            try:
                validator.validate(parsed)
            except jsonschema_rs.ValidationError as exc:
                raise ContentSchemaViolation(exc, content_schema) from exc

        def validate(self, instance: Any) -> None:
            if self._content is None:
                return
            assert isinstance(instance, str), f"SSE event field must be a string, got {type(instance).__name__}"
            validator, content_schema = self._content
            assert owner._deserialize_payload is not None, "Should always be set via `with_deserializer`"
            try:
                parsed = owner._deserialize_payload(self.content_media_type, instance)
            except NotImplementedError:
                # No matching deserializer: treat this media type as non-assertive.
                return
            except Exception as exc:
                raise ValueError(f"cannot deserialize payload for `{self.content_media_type}` ({exc})") from None
            self._validate_parsed(parsed, content_schema, validator)

    return {"contentMediaType": ContentMediaTypeKeyword}


def _schema_with_bundle(schema: dict[str, Any] | bool, root_schema: dict[str, Any] | bool) -> dict[str, Any] | bool:
    if not isinstance(schema, dict) or not isinstance(root_schema, dict):
        return schema
    bundled = root_schema.get(BUNDLE_STORAGE_KEY)
    if bundled is None or BUNDLE_STORAGE_KEY in schema:
        return schema
    result = dict(schema)
    result[BUNDLE_STORAGE_KEY] = bundled
    return result
