from __future__ import annotations

import re
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any

import jsonschema_rs

from schemathesis.core import deserialization
from schemathesis.core.errors import InvalidSchema, MalformedMediaType, SchemaLocation
from schemathesis.core.failures import Failure, FailureGroup, MalformedJson
from schemathesis.core.jsonschema.bundler import REFERENCE_TO_BUNDLE_PREFIX
from schemathesis.core.transport import Response
from schemathesis.openapi.checks import JsonSchemaError, MissingContentType
from schemathesis.specs.openapi.content_keywords import ContentSchemaViolation

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.adapter.responses import ResolvedSchema
    from schemathesis.specs.openapi.schemas import OpenApiSchema


_SURROGATE_ESCAPE_RE = re.compile(r"\\u[Dd][89a-fA-F][0-9a-fA-F]{2}")


class ResponseValidator:
    """Validates HTTP responses against an operation's documented schema."""

    __slots__ = ("schema",)

    def __init__(self, schema: OpenApiSchema) -> None:
        self.schema = schema

    def validate(
        self,
        operation: APIOperation,
        response: Response,
        *,
        case: Case | None = None,
    ) -> bool | None:
        __tracebackhide__ = True
        schema = self.schema
        definition = operation.responses.find_by_status_code(response.status_code)
        if definition is None:
            return None

        documented_media_types = schema.get_content_types(operation, response)

        failures: list[Failure] = []

        content_types = response.headers.get("content-type")
        resolved_content_type = content_types[0] if content_types else None

        resolved = definition.get_schema(resolved_content_type)
        if resolved.schema is None:
            return None

        sse_validator = None
        validator = None
        try:
            sse_validator = definition.get_sse_validator(resolved.media_type, resolved.schema)
        except (MalformedMediaType, ValueError) as exc:
            raise InvalidSchema(
                f"Invalid response schema for SSE content validation:\n\n  {exc}",
                path=operation.path,
                method=operation.method,
            ) from exc
        except jsonschema_rs.ValidationError as exc:
            raise InvalidSchema.from_jsonschema_error(
                exc,
                path=operation.path,
                method=operation.method,
                config=schema.config.output,
                location=SchemaLocation.response_schema(schema.specification.version),
            ) from exc
        if sse_validator is None:
            try:
                validator = definition.get_validator(resolved.media_type, resolved.schema)
            except jsonschema_rs.ValidationError as exc:
                raise InvalidSchema.from_jsonschema_error(
                    exc,
                    path=operation.path,
                    method=operation.method,
                    config=schema.config.output,
                    location=SchemaLocation.response_schema(schema.specification.version),
                ) from exc
            if validator is None:
                return None

        if resolved_content_type is None:
            formatted_content_types = [f"\n- `{content_type}`" for content_type in documented_media_types]
            message = f"The following media types are documented in the schema:{''.join(formatted_content_types)}"
            failures.append(
                MissingContentType(operation=operation.label, message=message, media_types=documented_media_types)
            )
            content_type = resolved.media_type or "application/json"
        else:
            content_type = resolved_content_type

        context = deserialization.DeserializationContext(operation=operation, case=case)

        try:
            data = deserialization.deserialize_response(response, content_type, context=context)
        except JSONDecodeError as exc:
            failures.append(MalformedJson.from_exception(operation=operation.label, exc=exc))
            _maybe_raise_one_or_more(failures)
            return None
        except NotImplementedError:
            # Expected for media types without a deserializer (images, binary formats).
            return None
        except Exception as exc:
            failures.append(
                Failure(
                    operation=operation.label,
                    title="Content deserialization error",
                    message=f"Failed to deserialize response content:\n\n  {exc}",
                )
            )
            _maybe_raise_one_or_more(failures)
            return None

        if sse_validator is not None:

            def deserialize_embedded_payload(content_media_type: str, payload: str) -> Any:
                embedded_response = Response(
                    status_code=response.status_code,
                    headers={"content-type": [content_media_type]},
                    content=payload.encode("utf-8"),
                    request=response.request,
                    elapsed=response.elapsed,
                    verify=response.verify,
                    message=response.message,
                    http_version=response.http_version,
                    encoding="utf-8",
                )
                return deserialization.deserialize_response(embedded_response, content_media_type, context=context)

            with sse_validator.with_deserializer(deserialize_embedded_payload):
                for idx, event_data in enumerate(data):
                    try:
                        sse_validator.validate(event_data)
                    except jsonschema_rs.ValidationError as exc:
                        cause = exc.__cause__
                        if isinstance(cause, ContentSchemaViolation):
                            failure = JsonSchemaError.from_exception(
                                title="SSE event payload violates content schema",
                                operation=operation.label,
                                exc=cause.original,
                                root_schema=cause.content_schema,
                                config=operation.schema.config.output,
                                name_to_uri=resolved.name_to_uri,
                            )
                        else:
                            failure = JsonSchemaError.from_exception(
                                title="SSE event violates schema",
                                operation=operation.label,
                                exc=exc,
                                root_schema=resolved.schema,
                                config=operation.schema.config.output,
                                name_to_uri=resolved.name_to_uri,
                            )
                        failure.message = f"Event #{idx}: {failure.message}"
                        if failure not in failures:
                            failures.append(failure)
        elif validator is not None:
            try:
                for err in validator.iter_errors(data):
                    failure = JsonSchemaError.from_exception(
                        operation=operation.label,
                        exc=err,
                        root_schema=resolved.schema,
                        config=operation.schema.config.output,
                        name_to_uri=resolved.name_to_uri,
                    )
                    if failure not in failures:
                        failures.append(failure)
            except ValueError as exc:
                # jsonschema_rs raises ValueError for lone Unicode surrogate characters (e.g. \uDCF3);
                # not valid JSON per RFC 8259 even though Python's json.loads accepts them.
                doc = response.content.decode("latin-1")
                position, lineno, colno = _find_surrogate_location(doc)
                failures.append(
                    MalformedJson(
                        operation=operation.label,
                        validation_message=str(exc),
                        document=doc,
                        position=position,
                        lineno=lineno,
                        colno=colno,
                        message=f"Response contains invalid JSON (lone Unicode surrogate characters are not valid per RFC 8259):\n\n  {exc}",
                    )
                )
        discriminator_failure = _check_discriminator(operation, data, resolved)
        if discriminator_failure is not None:
            failures.append(discriminator_failure)
        _maybe_raise_one_or_more(failures)
        return None


def _find_surrogate_location(doc: str) -> tuple[int, int, int]:
    """Return (position, lineno, colno) of the first lone surrogate escape in a JSON document."""
    match = _SURROGATE_ESCAPE_RE.search(doc)
    if match is None:
        return 0, 1, 1
    pos = match.start()
    text_before = doc[:pos]
    lineno = text_before.count("\n") + 1
    colno = pos - text_before.rfind("\n")
    return pos, lineno, colno


def _check_discriminator(
    operation: APIOperation,
    data: object,
    resolved: ResolvedSchema,
) -> Failure | None:
    """Return a failure if the discriminator property value is not in the known schema mapping."""
    if not isinstance(data, dict):
        return None
    schema = resolved.schema
    if not isinstance(schema, dict):
        return None
    discriminator = schema.get("discriminator")
    if not isinstance(discriminator, dict):
        return None
    property_name = discriminator.get("propertyName")
    if not property_name:
        return None
    value = data.get(property_name)
    if not isinstance(value, str):
        return None

    known_values: set[str] = set()
    explicit_mapping = discriminator.get("mapping")
    if isinstance(explicit_mapping, dict):
        known_values.update(explicit_mapping.keys())
    for keyword in ("anyOf", "oneOf"):
        for item in schema.get(keyword) or []:
            if not isinstance(item, dict):
                continue
            ref = item.get("$ref", "")
            if not isinstance(ref, str) or not ref.startswith(f"{REFERENCE_TO_BUNDLE_PREFIX}/"):
                continue
            bundled_name = ref[len(REFERENCE_TO_BUNDLE_PREFIX) + 1 :]
            original_uri = resolved.name_to_uri.get(bundled_name)
            if original_uri and "#" in original_uri:
                fragment = original_uri.split("#", 1)[1]
                schema_name = fragment.rstrip("/").rsplit("/", 1)[-1]
                if schema_name:
                    known_values.add(schema_name)

    if not known_values or value in known_values:
        return None

    known = ", ".join(f"'{v}'" for v in sorted(known_values))
    return Failure(
        operation=operation.label,
        title="Discriminator value not in schema mapping",
        message=(
            f"Response contains discriminator property '{property_name}' with value {value!r},\n"
            f"which does not match any of the known schema values: {known}"
        ),
    )


def _maybe_raise_one_or_more(failures: list[Failure]) -> None:
    if not failures:
        return
    if len(failures) == 1:
        raise failures[0] from None
    raise FailureGroup(failures) from None
