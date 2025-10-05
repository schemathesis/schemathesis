from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ItemsView, Iterator, Mapping, cast

from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.compat import RefResolutionError, RefResolver
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.jsonschema.bundler import bundle
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.specs.openapi import types
from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter
from schemathesis.specs.openapi.adapter.references import maybe_resolve
from schemathesis.specs.openapi.converter import to_json_schema
from schemathesis.specs.openapi.utils import expand_status_code

if TYPE_CHECKING:
    from jsonschema.protocols import Validator


@dataclass
class OpenApiResponse:
    """OpenAPI response definition."""

    status_code: str
    definition: Mapping[str, Any]
    resolver: RefResolver
    scope: str
    adapter: SpecificationAdapter

    __slots__ = ("status_code", "definition", "resolver", "scope", "adapter", "_schema", "_validator", "_headers")

    def __post_init__(self) -> None:
        self._schema: JsonSchema | None | NotSet = NOT_SET
        self._validator: Validator | NotSet = NOT_SET
        self._headers: OpenApiResponseHeaders | NotSet = NOT_SET

    @property
    def schema(self) -> JsonSchema | None:
        """The response body schema extracted from its definition.

        Returns `None` if the response has no schema.
        """
        if self._schema is NOT_SET:
            self._schema = self.adapter.extract_response_schema(
                self.definition, self.resolver, self.scope, self.adapter.nullable_keyword
            )
        assert not isinstance(self._schema, NotSet)
        return self._schema

    def get_raw_schema(self) -> JsonSchema | None:
        """Raw and unresolved response schema."""
        return self.adapter.extract_raw_response_schema(self.definition)

    @property
    def validator(self) -> Validator | None:
        """JSON Schema validator for this response."""
        from jsonschema import Draft202012Validator

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

    @property
    def headers(self) -> OpenApiResponseHeaders:
        """A collection of header definitions for this response."""
        if self._headers is NOT_SET:
            headers = self.definition.get("headers", {})
            self._headers = OpenApiResponseHeaders(
                dict(_iter_resolved_headers(headers, self.resolver, self.scope, self.adapter))
            )
        assert not isinstance(self._headers, NotSet)
        return self._headers

    def iter_examples(self) -> Iterator[tuple[str, object]]:
        """Iterate over examples of this response."""
        return self.adapter.iter_response_examples(self.definition, self.status_code)

    def iter_links(self) -> Iterator[tuple[str, Mapping[str, Any]]]:
        links = self.definition.get(self.adapter.links_keyword)
        if links is None:
            return
        for name, link in links.items():
            _, link = maybe_resolve(link, self.resolver, self.scope)
            yield name, link


@dataclass
class OpenApiResponses:
    """Collection of OpenAPI response definitions."""

    _inner: dict[str, OpenApiResponse]
    resolver: RefResolver
    scope: str
    adapter: SpecificationAdapter

    __slots__ = ("_inner", "resolver", "scope", "adapter")

    @classmethod
    def from_definition(
        cls, definition: types.v3.Responses, resolver: RefResolver, scope: str, adapter: SpecificationAdapter
    ) -> OpenApiResponses:
        """Build new collection of responses from their raw definition."""
        # TODO: Add also `v2` type
        return cls(
            dict(_iter_resolved_responses(definition=definition, resolver=resolver, scope=scope, adapter=adapter)),
            resolver=resolver,
            scope=scope,
            adapter=adapter,
        )

    def items(self) -> ItemsView[str, OpenApiResponse]:
        return self._inner.items()

    def get(self, key: str) -> OpenApiResponse | None:
        return self._inner.get(key)

    def add(self, status_code: str, definition: dict[str, Any]) -> OpenApiResponse:
        instance = OpenApiResponse(
            status_code=status_code,
            definition=definition,
            resolver=self.resolver,
            scope=self.scope,
            adapter=self.adapter,
        )
        self._inner[status_code] = instance
        return instance

    @property
    def status_codes(self) -> tuple[str, ...]:
        """All defined status codes."""
        # Defined as a tuple, so it can be used in a cache key
        return tuple(self._inner)

    def find_by_status_code(self, status_code: int) -> OpenApiResponse | None:
        """Find the most specific response definition matching the given HTTP status code."""
        responses = self._inner
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

    def iter_successful_responses(self) -> Iterator[OpenApiResponse]:
        """Iterate over all response definitions for successful responses."""
        for response in self._inner.values():
            if response.status_code.startswith("2"):
                yield response

    def iter_examples(self) -> Iterator[tuple[str, object]]:
        """Iterate over all examples for all responses."""
        for response in self.iter_successful_responses():
            yield from response.iter_examples()


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
    schema = extract_raw_response_schema_v2(response)
    if schema is not None:
        return _prepare_schema(schema, resolver, scope, nullable_keyword)
    return None


def extract_raw_response_schema_v2(response: Mapping[str, Any]) -> JsonSchema | None:
    return response.get("schema")


def extract_response_schema_v3(
    response: Mapping[str, Any], resolver: RefResolver, scope: str, nullable_keyword: str
) -> JsonSchema | None:
    schema = extract_raw_response_schema_v3(response)
    if schema is not None:
        return _prepare_schema(schema, resolver, scope, nullable_keyword)
    return None


def extract_raw_response_schema_v3(response: Mapping[str, Any]) -> JsonSchema | None:
    options = iter(response.get("content", {}).values())
    media_type = next(options, None)
    # "schema" is an optional key in the `MediaType` object
    if media_type is not None:
        return media_type.get("schema")
    return None


def _prepare_schema(schema: JsonSchema, resolver: RefResolver, scope: str, nullable_keyword: str) -> JsonSchema:
    schema = _bundle_in_scope(schema, resolver, scope)
    # Do not clone the schema, as bundling already does it
    return to_json_schema(schema, nullable_keyword, is_response_schema=True, update_quantifiers=False, clone=False)


def _bundle_in_scope(schema: JsonSchema, resolver: RefResolver, scope: str) -> JsonSchema:
    resolver.push_scope(scope)
    try:
        return bundle(schema, resolver, inline_recursive=False)
    except RefResolutionError as exc:
        raise InvalidSchema.from_reference_resolution_error(exc, None, None) from None
    finally:
        resolver.pop_scope()


@dataclass
class OpenApiResponseHeaders:
    """Collection of OpenAPI response header definitions."""

    _inner: dict[str, OpenApiResponseHeader]

    __slots__ = ("_inner",)

    def __bool__(self) -> bool:
        return bool(self._inner)

    def items(self) -> ItemsView[str, OpenApiResponseHeader]:
        return self._inner.items()


@dataclass
class OpenApiResponseHeader:
    """OpenAPI response header definition."""

    name: str
    definition: Mapping[str, Any]
    resolver: RefResolver
    scope: str
    adapter: SpecificationAdapter

    __slots__ = ("name", "definition", "resolver", "scope", "adapter", "_schema", "_validator")

    def __post_init__(self) -> None:
        self._schema: JsonSchema | NotSet = NOT_SET
        self._validator: Validator | NotSet = NOT_SET

    @property
    def is_required(self) -> bool:
        return self.definition.get(self.adapter.header_required_keyword, False)

    @property
    def schema(self) -> JsonSchema:
        """The header schema extracted from its definition."""
        if self._schema is NOT_SET:
            self._schema = self.adapter.extract_header_schema(
                self.definition, self.resolver, self.scope, self.adapter.nullable_keyword
            )
        assert not isinstance(self._schema, NotSet)
        return self._schema

    @property
    def validator(self) -> Validator:
        """JSON Schema validator for this header."""
        from jsonschema import Draft202012Validator

        schema = self.schema
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


def _iter_resolved_headers(
    definition: types.v3.Headers, resolver: RefResolver, scope: str, adapter: SpecificationAdapter
) -> Iterator[tuple[str, OpenApiResponseHeader]]:
    for name, header in definition.items():
        scope, resolved = maybe_resolve(header, resolver, scope)
        yield (
            name,
            OpenApiResponseHeader(name=name, definition=resolved, resolver=resolver, scope=scope, adapter=adapter),
        )


def extract_header_schema_v2(
    header: Mapping[str, Any], resolver: RefResolver, scope: str, nullable_keyword: str
) -> JsonSchema:
    return _prepare_schema(cast(dict, header), resolver, scope, nullable_keyword)


def extract_header_schema_v3(
    header: Mapping[str, Any], resolver: RefResolver, scope: str, nullable_keyword: str
) -> JsonSchema:
    schema = header.get("schema", {})
    return _prepare_schema(schema, resolver, scope, nullable_keyword)


def iter_response_examples_v2(response: Mapping[str, Any], status_code: str) -> Iterator[tuple[str, object]]:
    # In Swagger 2.0, examples are directly in the response under "examples"
    examples = response.get("examples", {})
    return iter(examples.items())


def iter_response_examples_v3(response: Mapping[str, Any], status_code: str) -> Iterator[tuple[str, object]]:
    for media_type, definition in response.get("content", {}).items():
        # Try to get a more descriptive example name from the `$ref` value
        schema_ref = definition.get("schema", {}).get("$ref")
        if schema_ref:
            name = schema_ref.split("/")[-1]
        else:
            name = f"{status_code}/{media_type}"

        for examples_container_keyword, example_keyword in (
            ("examples", "example"),
            ("x-examples", "x-example"),
        ):
            examples = definition.get(examples_container_keyword, {})
            if isinstance(examples, dict):
                for example in examples.values():
                    if "value" in example:
                        yield name, example["value"]
            elif isinstance(examples, list):
                for example in examples:
                    yield name, example
            if example_keyword in definition:
                yield name, definition[example_keyword]
