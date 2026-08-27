from __future__ import annotations

import string
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from schemathesis.core.media_types import is_form_parts, is_xml_parts
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.validation import has_invalid_characters, is_latin_1_encodable
from schemathesis.openapi.generation.filters import is_invalid_path_parameter


def jsonify(value: Any) -> Any:
    # Builds a new value: the input may be a spec-declared example that every other case reuses.
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, dict):
        return {key: jsonify(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonify(item) for item in value]
    return value


def quote_path_parameter(value: Any) -> str:
    if isinstance(value, str):
        if value == ".":
            return "%2E"
        elif value == "..":
            return "%2E%2E"
        else:
            # Percent-encode for path segments (space -> "%20"); "+" is literal in a path, not a space.
            return quote(value, safe="")
    if isinstance(value, list):
        return ",".join(map(str, value))
    return str(value)


# Characters `ensure_valid_headers_schema` keeps; a pattern requiring anything else is unsatisfiable for headers.
HEADER_ALLOWED_CHARS = string.ascii_letters + string.digits


def _not_schema(schema: dict[str, Any]) -> dict[str, Any]:
    not_schema = schema.get("not", {})
    if isinstance(not_schema, dict):
        return not_schema.copy()
    return {}


def ensure_valid_path_parameter_schema(schema: dict[str, Any]) -> dict[str, Any]:
    # Path parameters should have at least 1 character length and don't contain any characters with special treatment
    # on the transport level.
    not_ = _not_schema(schema)
    not_["pattern"] = r"[/{}]"
    min_length = max(schema.get("minLength", 0), 1)
    return {**schema, "minLength": min_length, "not": not_}


def ensure_valid_headers_schema(schema: dict[str, Any]) -> dict[str, Any]:
    # Reject any character that is not A-Z, a-z, or 0-9 for simplicity
    not_ = _not_schema(schema)
    not_["pattern"] = r"[^A-Za-z0-9]"
    return {**schema, "not": not_}


@dataclass(frozen=True, slots=True)
class WireSemantics:
    """How values travel for one parameter location and media type."""

    location: ParameterLocation
    media_type: tuple[str, str] | None
    is_required: bool

    def representable(self, value: Any) -> bool:
        """Whether this location can carry the value at all."""
        if self.location in ("header", "cookie") and isinstance(value, str):
            return not value or (is_latin_1_encodable(value) and not has_invalid_characters("A", value))
        elif self.location == "path":
            return not is_invalid_path_parameter(value)
        return True

    def leads_to_negative_test_case(self, value: Any) -> bool:
        if self.location == "query" and isinstance(value, list) and not self.is_required:
            # Some values will not be serialized into the query string
            # Optional parameters should be present
            return any(item not in [{}, []] for item in value)
        return True

    def form_body(self) -> bool:
        return self.location == ParameterLocation.BODY and is_form_parts(self.media_type)

    def xml_body(self) -> bool:
        return self.location == ParameterLocation.BODY and self.media_type is not None and is_xml_parts(self.media_type)

    def required_form_body(self) -> bool:
        # `{}` serializes to no content, which violates a required form body.
        return self.is_required and is_form_parts(self.media_type)

    def xml_string_needs_non_empty(self, schema: dict[str, Any]) -> bool:
        # An empty XML element round-trips as None on common parsers, so a positive string case needs >= 1 character.
        if not self.xml_body():
            return False
        if schema.get("minLength") not in (None, 0):
            return False
        max_length = schema.get("maxLength")
        if max_length is not None and max_length < 1:
            return False
        return "enum" not in schema and "const" not in schema

    def url_part(self) -> bool:
        """Whether values travel inside the URL, where every rendering collapses to text."""
        return self.location in (ParameterLocation.PATH, ParameterLocation.QUERY)

    def urlencoded_body(self) -> bool:
        return self.location == ParameterLocation.BODY and self.media_type == ("application", "x-www-form-urlencoded")

    def rendered(self, value: Any) -> Any:
        """The value as this location transmits it."""
        if self.location == ParameterLocation.PATH:
            return quote_path_parameter(jsonify(value))
        if self.location == ParameterLocation.QUERY:
            return jsonify(value)
        return value

    def observed(self, value: Any) -> str:
        """What the server parses back once the value travels as text."""
        # An XML null serializes to an empty element, which reads back as "".
        if self.media_type is not None and is_xml_parts(self.media_type) and value is None:
            return ""
        return str(value)

    def serializes_to_string(self) -> bool:
        if self.location in ("query", "path", "header", "cookie"):
            return True
        if self.location == "body" and self.media_type is not None:
            if is_form_parts(self.media_type):
                return True
            if is_xml_parts(self.media_type):
                return True
        return False

    def can_be_negated(self, schema: dict[str, Any]) -> bool:
        # Path, query, header, and cookie parameters will be stringified anyway
        # If there are no constraints, then anything will match the original schema after serialization
        if self.serializes_to_string():
            cleaned = {
                k: v
                for k, v in schema.items()
                if not k.startswith("x-") and k not in ["description", "example", "examples"]
            }
            return cleaned not in [{}, {"type": "string"}]
        return True
