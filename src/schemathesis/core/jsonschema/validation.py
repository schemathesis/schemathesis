from __future__ import annotations

from typing import TYPE_CHECKING, Any

import jsonschema_rs

from schemathesis.core.jsonschema.types import JsonSchema

if TYPE_CHECKING:
    from jsonschema import Validator as JsonSchemaValidator


class Validator:
    """Validator using Rust implementation with Python fallback."""

    __slots__ = ("schema", "validator_cls", "_rust", "_python")

    def __init__(self, schema: JsonSchema, validator_cls: type[JsonSchemaValidator]) -> None:
        import jsonschema

        self.schema = schema
        self.validator_cls = validator_cls
        self._rust = None
        self._python = None

        try:
            if validator_cls is jsonschema.Draft202012Validator:
                self._rust = jsonschema_rs.Draft202012Validator(schema, validate_formats=True)
            else:
                self._rust = jsonschema_rs.Draft4Validator(schema, validate_formats=True)
        except Exception:
            pass

    def is_valid(self, instance: Any) -> bool:
        if self._rust is not None:
            try:
                return self._rust.is_valid(instance)
            except Exception:
                pass

        if self._python is None:
            self._python = self.validator_cls(self.schema)

        return self._python.is_valid(instance)  # type: ignore[attr-defined]
