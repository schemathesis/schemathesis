from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urldefrag, urljoin

from schemathesis.config import OutputConfig
from schemathesis.core.failures import Failure, Severity
from schemathesis.core.jsonschema.bundler import unbundle, unbundle_path
from schemathesis.core.output import truncate_json, truncate_text
from schemathesis.core.transforms import iter_decoded_pointer_segments, resolve_path

if TYPE_CHECKING:
    from collections.abc import Iterable

    import jsonschema_rs


class UndefinedStatusCode(Failure):
    """Response has a status code that is not defined in the schema."""

    code = "ST103"
    checks = ("status_code_conformance",)
    kind_title = "Undocumented status code"

    __slots__ = ("status_code", "defined_status_codes", "allowed_status_codes")

    def __init__(
        self,
        *,
        operation: str,
        status_code: int,
        defined_status_codes: list[str],
        allowed_status_codes: list[int],
        message: str,
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.MEDIUM)
        self.status_code = status_code
        self.defined_status_codes = defined_status_codes
        self.allowed_status_codes = allowed_status_codes

    @property
    def _unique_key(self) -> str:
        return str(self.status_code)


class MissingHeaders(Failure):
    """Some required headers are missing."""

    code = "ST107"
    checks = ("response_headers_conformance",)
    kind_title = "Missing required response headers"

    __slots__ = ("missing_headers",)

    def __init__(
        self,
        *,
        operation: str,
        missing_headers: list[str],
        message: str,
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.MEDIUM)
        self.missing_headers = missing_headers

    @property
    def _unique_key(self) -> str:
        return ",".join(self.missing_headers)


class JsonSchemaError(Failure):
    """Additional information about JSON Schema validation errors."""

    code = "ST108"
    checks = ("response_schema_conformance", "response_headers_conformance")
    kind_title = "Response violates schema"

    __slots__ = (
        "schema_path",
        "instance_path",
        "instance",
        "_absolute_keyword_location",
        "_raw_message",
        "_root_schema",
        "_config",
        "_name_to_uri",
        "_context",
        "_message",
        "_validation_message",
        "_schema",
    )

    def __init__(
        self,
        *,
        operation: str,
        exc: jsonschema_rs.ValidationError,
        root_schema: dict[str, Any] | bool,
        config: OutputConfig | None = None,
        name_to_uri: dict[str, str] | None = None,
        context: str | None = None,
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.schema_path = exc.schema_path
        self.instance_path = exc.instance_path
        self.instance = exc.instance
        self._absolute_keyword_location = exc.absolute_keyword_location
        self._raw_message = exc.message
        self._root_schema = root_schema
        self._config = config
        self._name_to_uri = name_to_uri
        self._context = context
        # Most failures are duplicates that nobody displays, so the message is rendered on first access.
        self._message: str | None = None
        self._validation_message = ""
        self._schema: dict[str, Any] = {}
        self.title = title if title is not None else self.kind_title
        self.case_id = case_id
        self.severity = Severity.HIGH

    @property
    def message(self) -> str:
        if self._message is None:
            self._message = self._render_message()
        return self._message

    @message.setter
    def message(self, value: str) -> None:
        self._message = value

    @property
    def validation_message(self) -> str:
        _ = self.message
        return self._validation_message

    @property
    def schema(self) -> dict[str, Any]:
        _ = self.message
        return self._schema

    @property
    def _unique_key(self) -> str:
        return "/".join(map(str, self.schema_path))

    @classmethod
    def from_exception(
        cls,
        *,
        title: str | None = None,
        operation: str,
        exc: jsonschema_rs.ValidationError,
        root_schema: dict[str, Any] | bool,
        config: OutputConfig | None = None,
        name_to_uri: dict[str, str] | None = None,
        context: str | None = None,
    ) -> JsonSchemaError:
        return cls(
            title=title,
            operation=operation,
            exc=exc,
            root_schema=root_schema,
            config=config,
            name_to_uri=name_to_uri,
            context=context,
        )

    def _render_message(self) -> str:
        path = self.schema_path
        # Past a reference into a schema with its own `$id`, the validator reports paths relative to that schema.
        if self._absolute_keyword_location is not None:
            uri, fragment = urldefrag(self._absolute_keyword_location)
            resource = _find_resource(self._root_schema, uri, "")
            if resource is not None:
                path = [*resource, *iter_decoded_pointer_segments(unquote(fragment))]
        if path:
            schema = resolve_path(self._root_schema, path[:-1])
            if not isinstance(schema, (dict, bool)):
                # A boolean branch of `allOf` / `anyOf` has the keyword's list of schemas as its parent.
                schema = resolve_path(self._root_schema, path)
        else:
            schema = self._root_schema

        # Read-only and write-only properties are rewritten to a schema nothing satisfies, and the raw
        # validator message spells that rewrite rather than naming what it rejects.
        if schema == {"not": {}} and self.schema_path and self.schema_path[-1] == "not":
            name = self.instance_path[-1] if self.instance_path else None
            validation_message = (
                f'Property "{name}" is not allowed' if isinstance(name, str) else "Value is not allowed"
            )
        else:
            validation_message = _shorten_instance(self._raw_message, self.instance, self._config or OutputConfig())

        value = textwrap.indent(
            truncate_json(self.instance, config=self._config or OutputConfig(), max_lines=20), prefix="    "
        )
        prefix = f"{validation_message}\n\n{self._context}" if self._context else validation_message
        self._validation_message = validation_message
        # A schema named by a relative `$id` cannot be located, so only the value is shown.
        if not isinstance(schema, (dict, bool)):
            return f"{prefix}\n\nValue:\n\n{value}"

        # Reorder schema to prioritize the failing keyword in the output
        if isinstance(schema, dict) and self.schema_path:
            failing_keyword = self.schema_path[-1]
            if isinstance(failing_keyword, str) and failing_keyword in schema:
                schema = {
                    failing_keyword: schema[failing_keyword],
                    **{k: v for k, v in schema.items() if k != failing_keyword},
                }

        # Restore original $ref paths for display if mapping is available
        if self._name_to_uri:
            schema = unbundle(schema, self._name_to_uri)

        schema_str = textwrap.indent(
            truncate_json(schema, config=self._config or OutputConfig(), max_lines=20), prefix="    "
        )
        if len(path) > 1:
            schema_title = "Schema at "
            for segment in unbundle_path(path[:-1], self._name_to_uri) if self._name_to_uri else path[:-1]:
                schema_title += f"/{segment}"
        else:
            schema_title = "Schema"
        self._schema = schema if isinstance(schema, dict) else {}
        return f"{prefix}\n\n{schema_title}:\n\n{schema_str}\n\nValue:\n\n{value}"


def _find_resource(node: object, uri: str, base: str) -> list[str | int] | None:
    """Location of the schema whose `$id` resolves to `uri`."""
    if isinstance(node, dict):
        identifier = node.get("$id")
        # A non-string sits under a property named `$id`, not under the keyword.
        if isinstance(identifier, str):
            base = urldefrag(urljoin(base, identifier))[0]
            if base == uri:
                return []
        children: Iterable[tuple[str | int, object]] = node.items()
    elif isinstance(node, list):
        children = enumerate(node)
    else:
        return None
    for key, child in children:
        location = _find_resource(child, uri, base)
        if location is not None:
            return [key, *location]
    return None


def _shorten_instance(message: str, instance: object, config: OutputConfig) -> str:
    # The validator message spells out the whole rejected value on one line, however large.
    if not config.truncation.enabled:
        return message
    rendered = json.dumps(instance, separators=(",", ":"), ensure_ascii=False)
    if len(rendered) <= config.truncation.max_width:
        return message
    return message.replace(rendered, truncate_text(rendered, config.truncation.max_width), 1)


class MissingContentType(Failure):
    """Content type header is missing."""

    code = "ST104"
    checks = ("content_type_conformance", "response_schema_conformance")
    kind_title = "Missing Content-Type header"

    __slots__ = ("media_types",)

    def __init__(
        self,
        *,
        operation: str,
        media_types: list[str],
        message: str,
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.MEDIUM)
        self.media_types = media_types

    @property
    def _unique_key(self) -> str:
        return ""


class MalformedMediaType(Failure):
    """Media type name is malformed."""

    code = "ST106"
    checks = ("content_type_conformance",)
    kind_title = "Malformed media type"

    __slots__ = ("actual", "defined")

    def __init__(
        self,
        *,
        operation: str,
        actual: str,
        defined: str,
        message: str,
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.MEDIUM)
        self.actual = actual
        self.defined = defined

    @property
    def _unique_key(self) -> str:
        return self.actual


class ContentDeserializationError(Failure):
    """Response body cannot be deserialized as its documented media type."""

    code = "ST110"
    checks = ("response_schema_conformance",)
    kind_title = "Content deserialization error"

    __slots__ = ()

    def __init__(self, *, operation: str, message: str, case_id: str | None = None) -> None:
        super().__init__(operation=operation, message=message, case_id=case_id)

    @property
    def _unique_key(self) -> str:
        # One finding per operation, whatever the parse error.
        return ""


class DiscriminatorMismatch(Failure):
    """Response discriminator value matches no schema the discriminator maps to."""

    code = "ST111"
    checks = ("response_schema_conformance",)
    kind_title = "Unknown discriminator value"

    __slots__ = ()

    def __init__(self, *, operation: str, message: str, case_id: str | None = None) -> None:
        super().__init__(operation=operation, message=message, case_id=case_id)

    @property
    def _unique_key(self) -> str:
        # One finding per operation, whatever the value.
        return ""


class UndefinedContentType(Failure):
    """Response has Content-Type that is not documented in the schema."""

    code = "ST105"
    checks = ("content_type_conformance",)
    kind_title = "Undocumented Content-Type"

    __slots__ = ("content_type", "defined_content_types")

    def __init__(
        self,
        *,
        operation: str,
        content_type: str,
        defined_content_types: list[str],
        message: str,
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.MEDIUM)
        self.content_type = content_type
        self.defined_content_types = defined_content_types

    @property
    def _unique_key(self) -> str:
        return self.content_type


class UseAfterFree(Failure):
    """Resource was used after a successful DELETE operation on it."""

    code = "ST301"
    checks = ("use_after_free",)
    kind_title = "Deleted resource still available"

    __slots__ = ("free", "usage", "deleted_case_id")

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        free: str,
        usage: str,
        deleted_case_id: str | None = None,
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.CRITICAL)
        self.free = free
        self.usage = usage
        self.deleted_case_id = deleted_case_id

    @property
    def _unique_key(self) -> str:
        return ""

    def related_case_ids(self) -> tuple[str, ...]:
        if self.deleted_case_id is None:
            return ()
        return (self.deleted_case_id,)


class EnsureResourceAvailability(Failure):
    """Resource is not available immediately after creation."""

    code = "ST302"
    checks = ("ensure_resource_availability",)
    kind_title = "Created resource not found"

    __slots__ = ("created_with", "not_available_with")

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        created_with: str,
        not_available_with: str,
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.MEDIUM)
        self.created_with = created_with
        self.not_available_with = not_available_with

    @property
    def _unique_key(self) -> str:
        return ""


class ObjectLevelAuthorizationViolation(Failure):
    """One identity received an object that another identity created."""

    code = "ST402"
    checks = ("object_level_authorization",)
    kind_title = "Object-level authorization bypass"

    __slots__ = ("owner", "peer", "owner_case_id")
    # Rerunning as the peer alone does not recreate the owner's object.
    reproducible_by_identity = False

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        owner: str,
        peer: str,
        owner_case_id: str,
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.CRITICAL)
        self.owner = owner
        self.peer = peer
        self.owner_case_id = owner_case_id

    @property
    def _unique_key(self) -> str:
        return ""

    def related_case_ids(self) -> tuple[str, ...]:
        return (self.owner_case_id,)

    def identity_roles(self) -> dict[str, str]:
        return {self.owner: "owner", self.peer: "peer"}


class RejectedPositiveData(Failure):
    """Response with positive data was rejected."""

    code = "ST202"
    checks = ("positive_data_acceptance",)
    kind_title = "Schema-valid request rejected"

    __slots__ = ("status_code", "allowed_statuses")

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        status_code: int,
        allowed_statuses: list[str],
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.MEDIUM)
        self.status_code = status_code
        self.allowed_statuses = allowed_statuses

    @property
    def _unique_key(self) -> str:
        return str(self.status_code)


class MissingHeaderNotRejected(Failure):
    """API did not reject request without required header."""

    code = "ST203"
    checks = ("missing_required_header",)
    kind_title = "Missing required header accepted"

    __slots__ = ("header_name", "status_code", "expected_statuses")

    def __init__(
        self,
        *,
        operation: str,
        header_name: str,
        status_code: int,
        expected_statuses: list[int],
        message: str,
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.MEDIUM)
        self.header_name = header_name
        self.status_code = status_code
        self.expected_statuses = expected_statuses

    @property
    def _unique_key(self) -> str:
        return self.header_name


class UnsupportedMethodResponse(Failure):
    """API response for unsupported HTTP method is incorrect."""

    code = "ST204"
    checks = ("unsupported_method",)
    kind_title = "Wrong response to undeclared method"

    __slots__ = ("method", "status_code", "allow_header_present", "failure_reason")

    def __init__(
        self,
        *,
        operation: str,
        method: str,
        status_code: int,
        allow_header_present: bool | None = None,
        failure_reason: str,  # "wrong_status" or "missing_allow_header"
        message: str,
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.MEDIUM)
        self.method = method
        self.status_code = status_code
        self.allow_header_present = allow_header_present
        self.failure_reason = failure_reason

    @property
    def _unique_key(self) -> str:
        return self.failure_reason


class AllowHeaderMismatch(Failure):
    """`Allow` header does not list the same methods as the schema."""

    code = "ST205"
    checks = ("allow_header_conformance",)
    kind_title = "Allow header mismatch"

    __slots__ = ("allow_header", "missing_methods", "undocumented_methods")

    def __init__(
        self,
        *,
        operation: str,
        allow_header: str,
        missing_methods: list[str],
        undocumented_methods: list[str],
        message: str,
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.MEDIUM)
        self.allow_header = allow_header
        self.missing_methods = missing_methods
        self.undocumented_methods = undocumented_methods

    @property
    def _unique_key(self) -> str:
        return f"{','.join(self.missing_methods)}|{','.join(self.undocumented_methods)}"
