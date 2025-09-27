from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Mapping, TypeVar

from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.compat import RefResolutionError, RefResolver
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.jsonschema.bundler import bundle
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.specs.openapi import types
from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter
from schemathesis.specs.openapi.adapter.references import maybe_resolve
from schemathesis.specs.openapi.converter import to_json_schema_recursive
from schemathesis.specs.openapi.utils import expand_status_code


@dataclass
class OpenApiResponse:
    """OpenAPI response definition."""

    status_code: str
    definition: Mapping[str, Any]
    resolver: RefResolver
    scope: str
    adapter: SpecificationAdapter

    __slots__ = ("status_code", "definition", "resolver", "scope", "adapter", "_schema", "_validator")

    def __post_init__(self) -> None:
        self._schema: JsonSchema | None | NotSet = NOT_SET
        self._validator: Validator | NotSet = NOT_SET

    @property
    def schema(self) -> JsonSchema | None:
        """The response body schema extracted from the definition.

        Returns `None` if the response has no schema.
        """
        if self._schema is NOT_SET:
            self._schema = self.adapter.extract_response_schema(
                self.definition, self.resolver, self.scope, self.adapter.nullable_keyword
            )
        assert not isinstance(self._schema, NotSet)
        return self._schema

    @property
    def validator(self) -> Validator | None:
        """JSON Schema validator for this response."""
        schema = self.schema
        if schema is None:
            return None
        if self._validator is NOT_SET:
            self.adapter.jsonschema_validator_cls.check_schema(schema)
            self._validator = self.adapter.jsonschema_validator_cls(
                schema,
                # Use a recent JSON Schema format checker to get most of formats checked for older drafts as well
                format_checker=Draft202012Validator.FORMAT_CHECKER,
                resolver=RefResolver.from_schema(schema),
            )
        assert not isinstance(self._validator, NotSet)
        return self._validator


@dataclass
class OpenApiResponses:
    """Collection of OpenAPI response definitions."""

    _inner: dict[str, OpenApiResponse]

    __slots__ = ("_inner",)

    @classmethod
    def from_definition(
        cls, definition: types.v3.Responses, resolver: RefResolver, scope: str, adapter: SpecificationAdapter
    ) -> OpenApiResponses:
        """Build new collection of responses from their raw definition."""
        # TODO: Add also `v2` type
        return OpenApiResponses(
            dict(_iter_resolved_responses(definition=definition, resolver=resolver, scope=scope, adapter=adapter))
        )

    def find_by_status_code(self, status_code: int) -> OpenApiResponse | None:
        """Find the most specific response definition matching the given HTTP status code."""
        return _find_by_status_code(self._inner, status_code)


def _iter_resolved_responses(
    definition: types.v3.Responses, resolver: RefResolver, scope: str, adapter: SpecificationAdapter
) -> Iterator[tuple[str, OpenApiResponse]]:
    for key, response in definition.items():
        status_code = str(key)
        scope, resolved = maybe_resolve(response, resolver, scope)
        yield (
            status_code,
            OpenApiResponse(
                status_code=status_code, definition=resolved, resolver=resolver, scope=scope, adapter=adapter
            ),
        )


def extract_response_schema_v2(
    response: Mapping[str, Any], resolver: RefResolver, scope: str, nullable_keyword: str
) -> JsonSchema | None:
    schema = response.get("schema")
    if schema is not None:
        return _prepare_schema(schema, resolver, scope, nullable_keyword)
    return None


def extract_response_schema_v3(
    response: Mapping[str, Any], resolver: RefResolver, scope: str, nullable_keyword: str
) -> JsonSchema | None:
    options = iter(response.get("content", {}).values())
    media_type = next(options, None)
    # "schema" is an optional key in the `MediaType` object
    if media_type and "schema" in media_type:
        return _prepare_schema(media_type["schema"], resolver, scope, nullable_keyword)
    return None


def _prepare_schema(schema: JsonSchema, resolver: RefResolver, scope: str, nullable_keyword: str) -> JsonSchema:
    schema = _bundle_in_scope(schema, resolver, scope)
    # Do not clone the schema, as bundling already does it
    return to_json_schema_recursive(
        schema, nullable_keyword, is_response_schema=True, update_quantifiers=False, clone=False
    )


def _bundle_in_scope(schema: JsonSchema, resolver: RefResolver, scope: str) -> JsonSchema:
    resolver.push_scope(scope)
    try:
        return bundle(schema, resolver, inline_recursive=False)
    except RefResolutionError as exc:
        raise InvalidSchema.from_reference_resolution_error(exc, None, None) from None
    finally:
        resolver.pop_scope()


T = TypeVar("T")


def _find_by_status_code(responses: dict[str, T], status_code: int) -> T | None:
    # Full match has the highest priority
    full_match = responses.get(str(status_code))
    if full_match is not None:
        return full_match
    # Then, ones with wildcards
    keys = sorted(responses, key=lambda k: k.count("X"))
    for key in keys:
        if key == "default":
            continue
        status_codes = expand_status_code(key)
        if status_code in status_codes:
            return responses[key]
    # The default response has the lowest priority
    return responses.get("default")
