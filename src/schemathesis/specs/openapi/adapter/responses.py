from __future__ import annotations

from collections.abc import ItemsView, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from schemathesis.core import NOT_SET, NotSet, media_types
from schemathesis.core.compat import RefResolutionError, RefResolver
from schemathesis.core.errors import InvalidSchema, MalformedMediaType
from schemathesis.core.jsonschema.bundler import Bundle, bundle
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.specs.openapi import types
from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter
from schemathesis.specs.openapi.adapter.references import maybe_resolve
from schemathesis.specs.openapi.converter import to_json_schema
from schemathesis.specs.openapi.utils import expand_status_code

if TYPE_CHECKING:
    from jsonschema.protocols import Validator

# Cache key when no Content-Type header is present
_NO_MEDIA_TYPE = ""


@dataclass
class ResolvedSchema:
    """Schema and media type resolved for a specific response."""

    __slots__ = ("schema", "media_type", "name_to_uri")

    schema: JsonSchema | None
    media_type: str | None
    name_to_uri: dict[str, str]


@dataclass
class CachedValidation:
    """Cached schema and validator for a media type."""

    __slots__ = ("schema", "validator", "name_to_uri")

    schema: JsonSchema | None
    validator: Validator | None
    name_to_uri: dict[str, str]


@dataclass
class OpenApiResponse:
    """OpenAPI response definition."""

    status_code: str
    definition: Mapping[str, Any]
    resolver: RefResolver
    scope: str
    adapter: SpecificationAdapter

    __slots__ = (
        "status_code",
        "definition",
        "resolver",
        "scope",
        "adapter",
        "_validation_cache",
        "_headers",
        "_default_media_type",
    )

    def __post_init__(self) -> None:
        self._validation_cache: dict[str, CachedValidation] = {}
        self._headers: OpenApiResponseHeaders | NotSet = NOT_SET
        self._default_media_type = self._detect_default_media_type()

    def _get_cache_key(self, media_type: str | None) -> str:
        """Convert media type to cache key, using sentinel for None."""
        return media_type if media_type is not None else _NO_MEDIA_TYPE

    def _detect_default_media_type(self) -> str | None:
        return self.adapter.get_default_response_media_type(self.definition)

    def get_schema(self, media_type: str | None = None) -> ResolvedSchema:
        """Return the schema for the given media type (or the default one).

        Schema may be None if the media type has no schema defined.
        """
        resolved_media_type = self.adapter.resolve_response_media_type(self.definition, media_type)
        cache_key = self._get_cache_key(resolved_media_type)

        if cache_key not in self._validation_cache:
            bundled = self.adapter.extract_schema_for_media_type(
                self.definition, resolved_media_type, self.resolver, self.scope, self.adapter.nullable_keyword
            )
            # Create cache entry with schema but no validator yet (lazy validator creation)
            if bundled is not None:
                self._validation_cache[cache_key] = CachedValidation(
                    schema=bundled.schema, validator=None, name_to_uri=bundled.name_to_uri
                )
            else:
                self._validation_cache[cache_key] = CachedValidation(schema=None, validator=None, name_to_uri={})

        cached = self._validation_cache[cache_key]
        return ResolvedSchema(schema=cached.schema, media_type=resolved_media_type, name_to_uri=cached.name_to_uri)

    def _build_validator(self, schema: JsonSchema) -> Validator | None:
        from jsonschema import Draft202012Validator

        self.adapter.jsonschema_validator_cls.check_schema(schema)
        return self.adapter.jsonschema_validator_cls(
            schema,
            # Use a recent JSON Schema format checker to get most of formats checked for older drafts as well
            format_checker=Draft202012Validator.FORMAT_CHECKER,
            resolver=RefResolver.from_schema(schema),
        )

    def get_validator_for_schema(self, resolved_media_type: str | None, schema: JsonSchema | None) -> Validator | None:
        """Get or build validator for a schema corresponding to a specific media type.

        This method is primarily used by the validation logic in schemas.py.
        Validators are cached per media type.
        """
        if schema is None:
            return None

        cache_key = self._get_cache_key(resolved_media_type)

        # Ensure cache entry exists (should already exist from get_schema call)
        if cache_key not in self._validation_cache:
            self._validation_cache[cache_key] = CachedValidation(schema=schema, validator=None, name_to_uri={})

        cached = self._validation_cache[cache_key]

        # Build validator lazily on first access
        if cached.validator is None and cached.schema is not None:
            cached.validator = self._build_validator(cached.schema)

        return cached.validator

    def get_raw_schema(self) -> JsonSchema | None:
        """Raw and unresolved response schema.

        For OpenAPI 3.x with multiple content types, returns the schema for the first/default media type.
        Used primarily for stateful testing where we analyze schema structure rather than validate responses.

        TODO: Extend stateful testing to support multiple content types properly.
        """
        return self.adapter.extract_raw_response_schema(self.definition)

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
        cls,
        definition: types.v3.Responses | types.v2.Responses,
        resolver: RefResolver,
        scope: str,
        adapter: SpecificationAdapter,
    ) -> OpenApiResponses:
        """Build new collection of responses from their raw definition."""
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
    definition: types.v3.Responses | types.v2.Responses,
    resolver: RefResolver,
    scope: str,
    adapter: SpecificationAdapter,
) -> Iterator[tuple[str, OpenApiResponse]]:
    """Iterate and resolve response definitions."""
    for key, response in definition.items():
        status_code = str(key)
        new_scope, resolved = maybe_resolve(response, resolver, scope)
        # Resolve one more level to support slightly malformed schemas with nested $ref chains
        # Some real-world schemas use: response -> $ref to definition -> $ref to actual schema
        # While technically not spec-compliant, this pattern is common enough to warrant leniency
        new_scope, resolved = maybe_resolve(resolved, resolver, new_scope)
        yield (
            status_code,
            OpenApiResponse(
                status_code=status_code, definition=resolved, resolver=resolver, scope=new_scope, adapter=adapter
            ),
        )


def extract_response_schema_v2(
    response: Mapping[str, Any], resolver: RefResolver, scope: str, nullable_keyword: str
) -> Bundle | None:
    """Extract and prepare response schema for Swagger 2.0."""
    schema = extract_raw_response_schema_v2(response)
    if schema is not None:
        return _prepare_schema(schema, resolver, scope, nullable_keyword)
    return None


def extract_raw_response_schema_v2(response: Mapping[str, Any]) -> JsonSchema | None:
    """Extract raw schema from Swagger 2.0 response (schema is at top level)."""
    return response.get("schema")


def extract_response_schema_v3(
    response: Mapping[str, Any], resolver: RefResolver, scope: str, nullable_keyword: str
) -> Bundle | None:
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


def _prepare_schema(schema: JsonSchema, resolver: RefResolver, scope: str, nullable_keyword: str) -> Bundle:
    bundled = _bundle_in_scope(schema, resolver, scope)
    # Do not clone the schema, as bundling already does it
    converted = to_json_schema(
        bundled.schema, nullable_keyword, is_response_schema=True, update_quantifiers=False, clone=False
    )
    return Bundle(schema=converted, name_to_uri=bundled.name_to_uri)


def prepare_response_media_type_schema(
    schema: JsonSchema, resolver: RefResolver, scope: str, nullable_keyword: str
) -> Bundle:
    """Prepare schema for a specific media type entry."""
    return _prepare_schema(schema, resolver, scope, nullable_keyword)


def get_default_response_media_type_v2(response: Mapping[str, Any]) -> str | None:
    """Swagger 2.0 has no default media type in response definition."""
    return None


def resolve_response_media_type_v2(response: Mapping[str, Any], media_type: str | None) -> str | None:
    """Swagger 2.0 has no media type resolution - all handled via produces."""
    return None


def extract_schema_for_media_type_v2(
    response: Mapping[str, Any], media_type: str | None, resolver: RefResolver, scope: str, nullable_keyword: str
) -> Bundle | None:
    """Swagger 2.0 has single schema regardless of media type."""
    return extract_response_schema_v2(response, resolver, scope, nullable_keyword)


def get_default_response_media_type_v3(response: Mapping[str, Any]) -> str | None:
    """Get first/default media type from OpenAPI 3.x response content."""
    content = response.get("content")
    if isinstance(content, dict) and content:
        return next(iter(content.keys()))
    return None


def resolve_response_media_type_v3(response: Mapping[str, Any], media_type: str | None) -> str | None:
    """Resolve actual media type to schema definition for OpenAPI 3.x.

    Resolution order:
      1. None -> first/default media type
      2. Exact match (e.g., "application/json")
      3. Wildcard match (e.g., "application/*" matches "application/xml")
      4. Fallback to first/default media type (for unmatched or malformed Content-Types)
    """
    content = response.get("content")
    if not isinstance(content, dict) or not content:
        return None

    default = next(iter(content.keys()))

    if media_type is None:
        return default

    # Strip parameters (e.g., "; charset=utf-8")
    sanitized = media_type.split(";", 1)[0].strip()

    # Exact match
    if sanitized in content:
        return sanitized

    # Wildcard matching
    try:
        received = media_types.parse(sanitized)
    except MalformedMediaType:
        return default

    for candidate in content:
        try:
            expected = media_types.parse(candidate)
        except MalformedMediaType:
            continue
        if media_types.matches_parts(expected, received):
            return candidate

    return default


def extract_schema_for_media_type_v3(
    response: Mapping[str, Any], media_type: str | None, resolver: RefResolver, scope: str, nullable_keyword: str
) -> Bundle | None:
    """Extract schema for specific media type from OpenAPI 3.x response."""
    content = response.get("content")
    if media_type is None or not isinstance(content, dict) or not content:
        # Fall back to old behavior
        return extract_response_schema_v3(response, resolver, scope, nullable_keyword)

    media_type_object = content.get(media_type)
    if not isinstance(media_type_object, dict):
        return None

    schema = media_type_object.get("schema")
    if schema is None:
        return None

    return prepare_response_media_type_schema(schema, resolver, scope, nullable_keyword)


def _bundle_in_scope(schema: JsonSchema, resolver: RefResolver, scope: str) -> Bundle:
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

    __slots__ = ("name", "definition", "resolver", "scope", "adapter", "_bundle", "_validator")

    def __post_init__(self) -> None:
        self._bundle: Bundle | NotSet = NOT_SET
        self._validator: Validator | NotSet = NOT_SET

    @property
    def is_required(self) -> bool:
        return self.definition.get(self.adapter.header_required_keyword, False)

    def _get_bundle(self) -> Bundle:
        """Get the bundled schema for this header."""
        if self._bundle is NOT_SET:
            self._bundle = self.adapter.extract_header_schema(
                self.definition, self.resolver, self.scope, self.adapter.nullable_keyword
            )
        assert not isinstance(self._bundle, NotSet)
        return self._bundle

    @property
    def schema(self) -> JsonSchema:
        """The header schema extracted from its definition."""
        return self._get_bundle().schema

    @property
    def name_to_uri(self) -> dict[str, str]:
        """Mapping from bundled schema names to original URIs."""
        return self._get_bundle().name_to_uri

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
    definition: types.v3.Headers | types.v2.Headers,
    resolver: RefResolver,
    scope: str,
    adapter: SpecificationAdapter,
) -> Iterator[tuple[str, OpenApiResponseHeader]]:
    """Iterate and resolve header definitions."""
    for name, header in definition.items():
        new_scope, resolved = maybe_resolve(header, resolver, scope)
        yield (
            name,
            OpenApiResponseHeader(name=name, definition=resolved, resolver=resolver, scope=new_scope, adapter=adapter),
        )


def extract_header_schema_v2(
    header: Mapping[str, Any], resolver: RefResolver, scope: str, nullable_keyword: str
) -> Bundle:
    return _prepare_schema(cast(dict, header), resolver, scope, nullable_keyword)


def extract_header_schema_v3(
    header: Mapping[str, Any], resolver: RefResolver, scope: str, nullable_keyword: str
) -> Bundle:
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
