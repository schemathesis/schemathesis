from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from schemathesis.core.errors import InvalidSchema, RefResolutionError
from schemathesis.core.jsonschema.bundler import BundleError
from schemathesis.core.jsonschema.resolver import Resolver
from schemathesis.core.jsonschema.types import JsonSchema, get_type
from schemathesis.core.transforms import decode_pointer, encode_pointer
from schemathesis.specs.openapi.adapter.parameters import resource_name_from_ref
from schemathesis.specs.openapi.adapter.references import maybe_resolve_with_resolver
from schemathesis.specs.openapi.stateful.dependencies import naming
from schemathesis.specs.openapi.stateful.dependencies.models import (
    CanonicalizationCache,
    Cardinality,
    DefinitionSource,
    NestedFKCache,
    OperationMap,
    ResourceDefinition,
    ResourceMap,
    extend_pointer,
    extract_fk_fields,
    extract_nested_fk_fields,
)
from schemathesis.specs.openapi.stateful.dependencies.naming import from_path
from schemathesis.specs.openapi.stateful.dependencies.schemas import (
    ROOT_POINTER,
    canonicalize,
    try_unwrap_composition,
    unwrap_schema,
)

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.adapter.responses import OpenApiResponse
    from schemathesis.specs.openapi.schemas import OpenApiSchema


@dataclass(slots=True)
class ExtractedResource:
    """How a resource was extracted from a response."""

    resource: ResourceDefinition
    # Where in response body (JSON pointer)
    pointer: str
    # Is this a single resource or an array?
    cardinality: Cardinality
    # True when response is a primitive value (string/number) that IS the identifier
    is_primitive_identifier: bool = False
    # True when the response is a map keyed by identifier (`{<id>: <object>, ...}`)
    extract_object_keys: bool = False
    # Fields this response declares for the resource; `None` when it declares none
    response_fields: frozenset[str] | None = None
    # Envelope field identifying the wrapped resource (`/bookingid` next to `/booking`)
    identifier_pointer: str | None = None


def extract_resources_from_responses(
    *, operation: APIOperation, state: InferenceState
) -> Iterator[tuple[OpenApiResponse, ExtractedResource]]:
    """Extract resource definitions from operation's successful responses.

    Processes each 2xx response, unwrapping pagination wrappers,
    handling `allOf` / `oneOf` / `anyOf` composition, and determining cardinality.
    Updates the global resource registry as resources are discovered.
    """
    for response in operation.responses.iter_successful_responses():
        try:
            extracted = list(
                iter_resources_from_response(
                    method=operation.method,
                    path=operation.path,
                    response=response,
                    state=state,
                )
            )
        except (RefResolutionError, InvalidSchema):
            # An unusable `$ref` hides only this response's resources
            continue
        for item in extracted:
            yield response, item


ResponseResourceCache: TypeAlias = dict[str, list[tuple["OpenApiResponse", ExtractedResource]]]


@dataclass(slots=True)
class InferenceState:
    """What the per-operation scan shares across every operation of one schema."""

    resolver: Resolver
    # Backs the body-FK gate so `<word>_name` fields without a real target don't spawn ghosts.
    candidate_resource_names: frozenset[str]
    resources: ResourceMap
    # Resources upgraded to a better definition, so existing input slots pick up their fields.
    updated_resources: set[str]
    # Same schemas are often canonicalized multiple times.
    canonicalization_cache: CanonicalizationCache
    # Per-operation response resources, materialized once and replayed by input + output extraction.
    response_resource_cache: ResponseResourceCache
    nested_fk_cache: NestedFKCache

    @classmethod
    def for_schema(cls, schema: OpenApiSchema) -> InferenceState:
        return cls(
            resolver=schema.root_resolver,
            candidate_resource_names=naming.collect_candidate_resource_names(schema.raw_schema),
            resources={},
            updated_resources=set(),
            canonicalization_cache={},
            response_resource_cache={},
            nested_fk_cache={},
        )


def cached_resources_from_responses(
    *, operation: APIOperation, state: InferenceState
) -> list[tuple[OpenApiResponse, ExtractedResource]]:
    """Materialize an operation's response resources once; replay on repeat scans.

    Both input resolution and output extraction scan the same responses within one
    operation's analysis pass; the first call registers resources, the rest reuse the list.
    """
    cache = state.response_resource_cache
    cached = cache.get(operation.label)
    if cached is None:
        cached = list(extract_resources_from_responses(operation=operation, state=state))
        cache[operation.label] = cached
    return cached


def iter_resources_from_response(
    *,
    method: str,
    path: str,
    response: OpenApiResponse,
    state: InferenceState,
) -> Iterator[ExtractedResource]:
    resources = state.resources
    updated_resources = state.updated_resources
    canonicalization_cache = state.canonicalization_cache
    schema = response.get_raw_schema()

    if isinstance(schema, bool):
        boolean_resource = _resource_from_boolean_schema(path=path, resources=resources)
        if boolean_resource is not None:
            yield boolean_resource
        return None
    elif not isinstance(schema, dict):
        # Ignore invalid schemas
        return None

    # Handle primitive responses from POST/PUT operations as resource identifiers.
    # Example: POST /api/recipes returns just a slug string - treat this as Recipe.slug
    # Example: POST /api/users returns just an integer ID - treat this as User.id
    schema_type = schema.get("type")
    if schema_type in ("string", "integer") and method in ("post", "put"):
        primitive_resource = _resource_from_primitive_response(path=path, resources=resources)
        if primitive_resource is not None:
            yield primitive_resource
            return None

    # Handle GET collections returning arrays of primitive identifiers.
    # Example: GET /products returns ["foo", "bar"] - treat as a list of Product identifiers.
    if method == "get" and schema_type == "array":
        items = schema.get("items")
        if isinstance(items, dict) and items.get("type") in ("string", "integer"):
            primitive_array = _resource_from_primitive_array(path=path, resources=resources)
            if primitive_array is not None:
                yield primitive_array
                return None

    current_resolver = response.resolver if response.resolver is not None else state.resolver

    # Handle map-by-id GET responses: `{type: object, additionalProperties: <object>}` with no
    # explicit properties. Keys are the identifiers (Kubernetes pod-statuses, TBA team-statuses).
    if method == "get" and schema_type == "object" and not schema.get("properties"):
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict) and (additional.get("type") == "object" or "$ref" in additional):
            map_resource = _resource_from_map_by_id(path=path, resources=resources)
            if map_resource is not None:
                # The values are what the response declares, not the resource the keys identify.
                map_resource.response_fields = _declared_fields(additional, current_resolver)
                yield map_resource
                return None

    parent_ref = schema.get("$ref")
    _, resolved = maybe_resolve_with_resolver(schema, current_resolver, allow_boolean=True)

    # Sometimes data is wrapped in a wrapper field, optionally next to request metadata
    # Common patterns: {data: {...}}, {result: {...}, status: ..., time: ...}
    pointer = None
    properties = resolved.get("properties", {})
    wrapper_field = _envelope_wrapper_field(properties)
    # Next to metadata, a referenced list is a pagination wrapper (`{data: List, meta: ...}`) handled further down,
    # and an object without properties (free-form or a map) carries no fields worth unwrapping into.
    if wrapper_field is not None and (
        len(properties) == 1
        or not (
            _is_referenced_array(properties[wrapper_field], current_resolver)
            or _is_object_without_properties(properties[wrapper_field], current_resolver)
        )
    ):
        pointer = f"/{wrapper_field}"
        resolved = properties[wrapper_field]
        # A created id wrapped next to status fields: `{httpStatus: ..., response: "<id>"}`
        if method == "post" and resolved.get("type") in ("string", "integer"):
            primitive_resource = _resource_from_primitive_response(path=path, resources=resources, pointer=pointer)
            if primitive_resource is not None:
                yield primitive_resource
            return None
        if len(properties) > 1 and "$ref" in resolved:
            # The wrapped schema names the resource and may hold a nested page (`{response: Page, status}`),
            # which the steps below only see once resolved.
            parent_ref = resolved["$ref"]
            _, resolved = maybe_resolve_with_resolver(resolved, current_resolver)

    resolved = try_unwrap_composition(resolved, current_resolver)

    if "allOf" in resolved:
        if parent_ref is not None and parent_ref in canonicalization_cache:
            canonicalized = canonicalization_cache[parent_ref]
        else:
            try:
                canonicalized = canonicalize(
                    cast(dict, resolved),
                    current_resolver,
                    nullable_keyword=response.adapter.nullable_keyword,
                )
            except BundleError:
                canonicalized = resolved
            if parent_ref is not None:
                canonicalization_cache[parent_ref] = canonicalized
    else:
        canonicalized = resolved

    # Detect wrapper pattern and navigate to data
    unwrapped = unwrap_schema(schema=canonicalized, path=path, parent_ref=parent_ref, resolver=current_resolver)

    # Recover $ref lost during allOf canonicalization
    recovered_ref = None
    if unwrapped.pointer != ROOT_POINTER and "allOf" in resolved:
        recovered_ref = _recover_ref_from_allof(
            branches=resolved["allOf"],
            pointer=unwrapped.pointer,
            resolver=current_resolver,
        )

    # Extract resource and determine cardinality
    result = _extract_resource_and_cardinality(
        schema=unwrapped.schema,
        path=path,
        resources=resources,
        updated_resources=updated_resources,
        resolver=current_resolver,
        parent_ref=recovered_ref or unwrapped.ref or parent_ref,
    )

    if result is not None:
        resource, cardinality, response_fields = result
        if canonicalized is not resolved and unwrapped.pointer == ROOT_POINTER and cardinality == Cardinality.ONE:
            # Flattening keeps one `allOf` reference only; the fields of every inherited schema belong to the response
            response_fields = _declared_fields(resolved, current_resolver)
        identifier_pointer = None
        if pointer is None and unwrapped.pointer != ROOT_POINTER and cardinality == Cardinality.ONE:
            identifier_pointer = _envelope_identifier_pointer(canonicalized, resource.name)
        if pointer:
            if unwrapped.pointer != ROOT_POINTER:
                pointer += unwrapped.pointer
        else:
            pointer = unwrapped.pointer
        yield ExtractedResource(
            resource=resource,
            cardinality=cardinality,
            pointer=pointer,
            response_fields=response_fields,
            identifier_pointer=identifier_pointer,
        )
        parent_cardinality = cardinality
        # Look for sub-resources. When the unwrapped schema is an array (MANY producer),
        # descend into `items` so we examine the per-element object's properties.
        sub_search_schema: Mapping[str, Any] = unwrapped.schema
        if cardinality == Cardinality.MANY:
            items = sub_search_schema.get("items")
            if isinstance(items, dict):
                _, sub_search_schema = maybe_resolve_with_resolver(items, current_resolver)
        properties = sub_search_schema.get("properties")
        if isinstance(properties, dict):
            for field, subschema in properties.items():
                if isinstance(subschema, dict):
                    # Check for direct $ref or $ref inside array items
                    reference = subschema.get("$ref")
                    if reference is None:
                        items = subschema.get("items")
                        if isinstance(items, dict):
                            reference = items.get("$ref")
                    if isinstance(reference, str):
                        sub_result = _extract_resource_and_cardinality(
                            schema=subschema,
                            path=path,
                            resources=resources,
                            updated_resources=updated_resources,
                            resolver=current_resolver,
                            parent_ref=reference,
                        )
                        if sub_result is not None:
                            subresource, sub_cardinality, sub_fields = sub_result
                            subresource_pointer = extend_pointer(pointer, field, parent_cardinality=parent_cardinality)
                            yield ExtractedResource(
                                resource=subresource,
                                cardinality=sub_cardinality,
                                pointer=subresource_pointer,
                                response_fields=sub_fields,
                            )
                            # When the sub-schema is itself a pagination/list wrapper (e.g. Spring
                            # `CustomResponse{response: {content: [...]}}`), descend one more level
                            # so the leaf array of resource items enters the pool, not the envelope.
                            if sub_cardinality == Cardinality.ONE:
                                _, resolved_sub = maybe_resolve_with_resolver(subschema, current_resolver)
                                sub_unwrapped = unwrap_schema(
                                    schema=resolved_sub, path=path, parent_ref=reference, resolver=current_resolver
                                )
                                if sub_unwrapped.pointer != ROOT_POINTER:
                                    inner_result = _extract_resource_and_cardinality(
                                        schema=sub_unwrapped.schema,
                                        path=path,
                                        resources=resources,
                                        updated_resources=updated_resources,
                                        resolver=current_resolver,
                                        parent_ref=sub_unwrapped.ref or reference,
                                    )
                                    if inner_result is not None:
                                        inner_resource, inner_cardinality, inner_fields = inner_result
                                        yield ExtractedResource(
                                            resource=inner_resource,
                                            cardinality=inner_cardinality,
                                            pointer=subresource_pointer + sub_unwrapped.pointer,
                                            response_fields=inner_fields,
                                        )


ENVELOPE_WRAPPER_FIELDS = frozenset({"data", "result", "response", "payload", "content"})
# Siblings a wrapper may carry without them describing the wrapped resource. Matched by name
# only: an `id` next to `result` identifies the response itself, so it must block unwrapping.
ENVELOPE_METADATA_FIELDS = frozenset(
    {
        "status",
        "httpstatus",
        "time",
        "usage",
        "message",
        "messages",
        "success",
        "issuccess",
        "error",
        "errors",
        "warnings",
        "meta",
        "metadata",
    }
)


def _envelope_wrapper_field(properties: Mapping[str, Any]) -> str | None:
    wrappers = [name for name in properties if name.lower() in ENVELOPE_WRAPPER_FIELDS]
    if len(wrappers) != 1:
        return None
    if all(name == wrappers[0] or name.lower() in ENVELOPE_METADATA_FIELDS for name in properties):
        return wrappers[0]
    return None


def _envelope_identifier_pointer(envelope: Mapping[str, Any], resource_name: str) -> str | None:
    # `{bookingid: 1, booking: {...}}`: the identifier of the wrapped booking sits next to it
    properties = envelope["properties"]
    identifier = naming.normalize_for_matching(resource_name) + "id"
    for name in properties:
        if naming.normalize_for_matching(name) == identifier:
            return f"/{encode_pointer(name)}"
    return None


def _is_referenced_array(schema: Mapping[str, Any], resolver: Resolver) -> bool:
    if "$ref" not in schema:
        return False
    _, resolved = maybe_resolve_with_resolver(schema, resolver)
    return resolved.get("type") == "array" or "items" in resolved


def _is_object_without_properties(schema: Mapping[str, Any], resolver: Resolver) -> bool:
    _, resolved = maybe_resolve_with_resolver(schema, resolver)
    return resolved.get("type") == "object" and not any(
        keyword in resolved for keyword in ("properties", "allOf", "anyOf", "oneOf")
    )


def _recover_ref_from_allof(*, branches: list[dict], pointer: str, resolver: Resolver) -> str | None:
    """Recover original $ref from allOf branches after canonicalization.

    Canonicalization inlines all $refs, losing resource name information.
    This searches original allOf branches to find which one defined the
    property at the given pointer.
    """
    # Parse pointer segments (e.g., "/data" -> ["data"])
    segments = [s for s in pointer.strip("/").split("/") if s]

    # Search each branch for the property
    for branch in branches:
        _, resolved_branch = maybe_resolve_with_resolver(branch, resolver)
        properties = resolved_branch.get("properties", {})

        # Check if this branch defines the target property
        if segments[-1] in properties:
            # Navigate to property in original (unresolved) branch
            original_properties = branch.get("properties", {})
            if segments[-1] in original_properties:
                prop_schema = original_properties[segments[-1]]
                # Extract $ref from property or its items
                return prop_schema.get("$ref") or prop_schema.get("items", {}).get("$ref")

    return None


def _resource_from_boolean_schema(*, path: str, resources: ResourceMap) -> ExtractedResource | None:
    name = from_path(path)
    if name is None:
        return None
    resource = resources.get(name)
    if resource is None:
        resource = ResourceDefinition.without_properties(name)
        resources[name] = resource
    # Do not update existing resource as if it is inferred, it will have at least one field
    return ExtractedResource(resource=resource, cardinality=Cardinality.ONE, pointer=ROOT_POINTER)


def _resource_from_primitive_response(
    *, path: str, resources: ResourceMap, pointer: str = ROOT_POINTER
) -> ExtractedResource | None:
    """Handle POST/PUT returning a bare primitive identifier (e.g., slug string or integer ID)."""
    name = from_path(path)
    if name is None:
        return None

    resource = resources.get(name)
    if resource is None:
        resource = ResourceDefinition.inferred_from_parameter(name=name, parameter_name="id")
        resources[name] = resource
    elif not resource.fields:
        resource.fields = ["id"]

    return ExtractedResource(
        resource=resource,
        cardinality=Cardinality.ONE,
        pointer=pointer,
        is_primitive_identifier=True,
    )


def _resource_from_map_by_id(*, path: str, resources: ResourceMap) -> ExtractedResource | None:
    """Handle a `{<id>: <object>}` map response by capturing each key as a resource id.

    The resource is derived from the parent path segment, since the last segment names the
    sub-collection (`statuses`, `metrics`, ...) rather than the resource type. For example,
    `GET /teams/statuses` -> `/teams` -> `Team`.
    """
    segments = [s for s in path.split("/") if s]
    if len(segments) < 2:
        name = from_path(path)
    else:
        parent_path = "/" + "/".join(segments[:-1])
        name = from_path(parent_path) or from_path(path)
    if name is None:
        return None

    resource = resources.get(name)
    if resource is None:
        resource = ResourceDefinition.inferred_from_parameter(name=name, parameter_name="id")
        resources[name] = resource

    return ExtractedResource(
        resource=resource,
        cardinality=Cardinality.MANY,
        pointer=ROOT_POINTER,
        extract_object_keys=True,
    )


def _resource_from_primitive_array(*, path: str, resources: ResourceMap) -> ExtractedResource | None:
    """Handle GET returning a bare array of primitive identifiers (e.g., ["foo", "bar"])."""
    name = from_path(path)
    if name is None:
        return None

    resource = resources.get(name)
    if resource is None:
        resource = ResourceDefinition.inferred_from_parameter(name=name, parameter_name="id")
        resources[name] = resource
    elif not resource.fields:
        resource.fields = ["id"]

    return ExtractedResource(
        resource=resource,
        cardinality=Cardinality.MANY,
        pointer=ROOT_POINTER,
        is_primitive_identifier=True,
    )


def _extract_resource_and_cardinality(
    *,
    schema: Mapping[str, Any],
    path: str,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: Resolver,
    parent_ref: str | None = None,
) -> tuple[ResourceDefinition, Cardinality, frozenset[str] | None] | None:
    """Extract resource from schema and determine cardinality, with the fields the schema declares."""
    # Check if it's an array
    if schema.get("type") == "array" or "items" in schema:
        items = schema.get("items")
        if not isinstance(items, dict):
            return None

        # Resolve items if it's a $ref
        _, resolved_items = maybe_resolve_with_resolver(items, resolver)

        # Extract resource from items
        resource = _extract_resource_from_schema(
            schema=resolved_items,
            path=path,
            resources=resources,
            updated_resources=updated_resources,
            resolver=resolver,
            # Prefer items $ref for name
            parent_ref=items.get("$ref") or parent_ref,
        )

        if resource is None:
            return None

        return resource, Cardinality.MANY, _declared_fields(resolved_items, resolver)

    # Single object
    resource = _extract_resource_from_schema(
        schema=schema,
        path=path,
        resources=resources,
        updated_resources=updated_resources,
        resolver=resolver,
        parent_ref=parent_ref,
    )

    if resource is None:
        return None

    return resource, Cardinality.ONE, _declared_fields(schema, resolver)


def _extract_resource_from_schema(
    *,
    schema: Mapping[str, Any],
    path: str,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: Resolver,
    parent_ref: str | None = None,
) -> ResourceDefinition | None:
    """Extract resource definition from a schema."""
    resource_name: str | None = None

    ref = schema.get("$ref")
    if ref is not None:
        resource_name = resource_name_from_ref(ref)
    elif parent_ref is not None:
        resource_name = resource_name_from_ref(parent_ref)
    else:
        resource_name = naming.from_path(path)

    if resource_name is None:
        return None

    resource = resources.get(resource_name)

    if resource is None or resource.source < DefinitionSource.SCHEMA_WITH_PROPERTIES:
        schema_resolver, resolved = maybe_resolve_with_resolver(schema, resolver)

        if "type" in resolved and resolved["type"] != "object" and "properties" not in resolved:
            # Skip strings, etc
            return None

        properties = _collect_properties(resolved, schema_resolver)
        if properties:
            fields = sorted(properties)
            types = {}
            for field, (field_resolver, subschema) in properties.items():
                resolved_subschema: Mapping[str, Any] | bool = subschema
                if isinstance(subschema, dict):
                    _, resolved_subschema = maybe_resolve_with_resolver(subschema, field_resolver)
                types[field] = set(get_type(cast(dict, resolved_subschema)))
            own = resolved.get("properties")
            inherited_fields = frozenset(properties) - frozenset(own if isinstance(own, dict) else ())
            source = DefinitionSource.SCHEMA_WITH_PROPERTIES
            # Pre-compute FK fields for efficient link generation
            fk_fields = extract_fk_fields(fields)
            # Extract nested FK fields from the schema, including the ones under inherited properties
            nested_fk_fields = [
                nested
                for field, (field_resolver, subschema) in properties.items()
                for nested in extract_nested_fk_fields({"properties": {field: subschema}}, field_resolver)
            ]
        else:
            fields = []
            types = {}
            inherited_fields = frozenset()
            source = DefinitionSource.SCHEMA_WITHOUT_PROPERTIES
            fk_fields = []
            nested_fk_fields = []
        if resource is not None:
            if resource.source < source:
                resource.source = source
                resource.fields = fields
                resource.types = types
                resource.fk_fields = fk_fields
                resource.nested_fk_fields = nested_fk_fields
                resource.inherited_fields = inherited_fields
                updated_resources.add(resource_name)
        else:
            resource = ResourceDefinition(
                name=resource_name,
                fields=fields,
                types=types,
                source=source,
                fk_fields=fk_fields,
                nested_fk_fields=nested_fk_fields,
                inherited_fields=inherited_fields,
            )
            resources[resource_name] = resource

    return resource


def remove_unused_resources(operations: OperationMap, resources: ResourceMap) -> None:
    """Remove resources that aren't referenced by any operation."""
    # Collect all resource names currently in use
    used_resources: set[str] = set()
    for operation in operations.values():
        used_resources.update(input_slot.resource.name for input_slot in operation.inputs)
        used_resources.update(output_slot.resource.name for output_slot in operation.outputs)

    unused = set(resources.keys()) - used_resources
    for resource_name in unused:
        del resources[resource_name]


Properties: TypeAlias = dict[str, tuple[Resolver, JsonSchema]]
# Properties behind a reference, with every reference consulted while collecting them.
PropertiesCache: TypeAlias = dict[tuple[str, str], tuple[Properties, frozenset[str]]]


def _collect_properties(schema: Mapping[str, Any], resolver: Resolver) -> Properties:
    return _collect_inherited_properties(schema, resolver, frozenset(), {})[0]


def _collect_inherited_properties(
    schema: Mapping[str, Any], resolver: Resolver, seen: frozenset[str], cache: PropertiesCache
) -> tuple[Properties, frozenset[str]]:
    # Properties inherited through `allOf` belong to the resource as much as its own ones.
    properties: Properties = {}
    own = schema.get("properties")
    if isinstance(own, dict):
        properties.update((name, (resolver, subschema)) for name, subschema in own.items())
    branches = schema.get("allOf")
    if not isinstance(branches, list):
        return properties, frozenset()
    consulted: set[str] = set()
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        reference = branch.get("$ref")
        if reference is None:
            branch_properties, branch_consulted = _collect_inherited_properties(branch, resolver, seen, cache)
        else:
            if not isinstance(reference, str):
                continue
            consulted.add(reference)
            if reference in seen:
                continue
            key = (resolver.base_uri, reference)
            cached = cache.get(key)
            # Reusable only when no reference it consulted is on the current path, as the cycle guard would differ
            if cached is not None and seen.isdisjoint(cached[1]):
                branch_properties, branch_consulted = cached
            else:
                try:
                    branch_resolver, resolved = maybe_resolve_with_resolver(branch, resolver)
                except (RefResolutionError, InvalidSchema):
                    continue
                branch_properties, branch_consulted = _collect_inherited_properties(
                    resolved, branch_resolver, seen | {reference}, cache
                )
                if seen.isdisjoint(branch_consulted):
                    cache[key] = (branch_properties, branch_consulted)
        consulted.update(branch_consulted)
        for name, entry in branch_properties.items():
            properties.setdefault(name, entry)
    return properties, frozenset(consulted)


def write_only_fields(response: OpenApiResponse, resolver: Resolver) -> set[str]:
    """Top-level fields the response declares `writeOnly`, which therefore never appear in it."""
    schema = response.get_raw_schema()
    if not isinstance(schema, dict):
        return set()
    current_resolver = response.resolver if response.resolver is not None else resolver
    try:
        schema_resolver, resolved = maybe_resolve_with_resolver(schema, current_resolver)
        fields = set()
        for name, (field_resolver, subschema) in _collect_properties(resolved, schema_resolver).items():
            if not isinstance(subschema, dict):
                continue
            _, field = maybe_resolve_with_resolver(subschema, field_resolver)
            branches = field.get("allOf")
            if any(
                isinstance(node, dict) and node.get("writeOnly") is True
                for node in (subschema, field, *(branches if isinstance(branches, list) else ()))
            ):
                fields.add(name)
    except (RefResolutionError, InvalidSchema):
        return set()
    return fields


def declared_field_values(response: OpenApiResponse, pointer: str, field: str, resolver: Resolver) -> list[Any] | None:
    """Values the response allows for `field` of the object at `pointer`; `None` when it does not list them."""
    schema = response.get_raw_schema()
    # Outputs come from the response body, so the schema is an object.
    assert isinstance(schema, dict)
    current_resolver = response.resolver if response.resolver is not None else resolver
    current: Any = schema
    # References up to the field were resolved when the output was extracted; the field's own are resolved below.
    for name in [*(decode_pointer(part) for part in pointer.split("/") if part), field]:
        current_resolver, current = maybe_resolve_with_resolver(current, current_resolver)
        entry = _collect_properties(current, current_resolver).get(name)
        # A field declared without a schema (`true`) may hold anything.
        if entry is None or not isinstance(entry[1], dict):
            return None
        current_resolver, current = entry
    return allowed_values(current, current_resolver)


def allowed_values(schema: Mapping[str, Any], resolver: Resolver | None) -> list[Any] | None:
    """Values `enum` or `const` limit `schema` to that its `type` allows; `None` when it lists none.

    Without a resolver, references are skipped.
    """
    values: list[Any] | None = None
    types: set[str] | None = None
    nullable = False
    pending: list[tuple[Resolver | None, Mapping[str, Any]]] = [(resolver, schema)]
    # `allOf` chains may be recursive.
    seen: set[int] = set()
    while pending:
        current_resolver, node = pending.pop()
        if "$ref" in node:
            if current_resolver is None:
                continue
            try:
                current_resolver, node = maybe_resolve_with_resolver(node, current_resolver)
            except (RefResolutionError, InvalidSchema):
                continue
        if id(node) in seen:
            continue
        seen.add(id(node))
        listed = [node["const"]] if "const" in node else node.get("enum")
        if isinstance(listed, list):
            values = listed if values is None else [value for value in values if contains_value(listed, value)]
        declared = node.get("type")
        if isinstance(declared, (str, list)):
            node_types = {declared} if isinstance(declared, str) else set(declared)
            types = node_types if types is None else _intersect_types(types, node_types)
        if node.get("nullable") is True or node.get("x-nullable") is True:
            nullable = True
        branches = node.get("allOf")
        if isinstance(branches, list):
            pending.extend((current_resolver, branch) for branch in branches if isinstance(branch, dict))
    if values is None:
        return None
    # `nullable` adds `null` to the type; `enum` still decides whether `null` is allowed.
    if nullable and types is not None:
        types = types | {"null"}
    # A listed value the declared `type` rules out never appears in a valid instance.
    if types is not None:
        values = [
            value
            for value in values
            if _json_type(value) in types or (_json_type(value) == "integer" and "number" in types)
        ]
    return values


def _intersect_types(left: set[str], right: set[str]) -> set[str]:
    common = left & right
    # `integer` is a subset of `number`.
    if ("integer" in left and "number" in right) or ("number" in left and "integer" in right):
        common.add("integer")
    return common


_JSON_TYPES = {
    type(None): "null",
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    list: "array",
    dict: "object",
}


def _json_type(value: Any) -> str:
    # JSON Schema counts whole floats (`1.0`) as integers.
    if isinstance(value, float) and value.is_integer():
        return "integer"
    # YAML may also load dates and similar values, which no JSON type matches.
    return _JSON_TYPES.get(type(value), "object")


def contains_value(values: list[Any], value: Any) -> bool:
    # JSON Schema equality: `1` equals `1.0`, but a boolean is never a number.
    return any(candidate == value and isinstance(candidate, bool) == isinstance(value, bool) for candidate in values)


def _declared_fields(schema: Mapping[str, Any], resolver: Resolver) -> frozenset[str] | None:
    """Fields declared by an object schema, its `allOf` parents, and any of its `oneOf` / `anyOf` alternatives."""
    schema_resolver, resolved = maybe_resolve_with_resolver(schema, resolver)
    if "object" not in get_type(cast(dict, resolved)) and "properties" not in resolved:
        # Binary or scalar bodies carry no fields at all
        return frozenset()
    fields = set(_collect_properties(resolved, schema_resolver))
    for keyword in ("oneOf", "anyOf"):
        alternatives = resolved.get(keyword)
        if not isinstance(alternatives, list):
            continue
        for alternative in alternatives:
            if not isinstance(alternative, dict):
                continue
            try:
                alternative_resolver, alternative = maybe_resolve_with_resolver(alternative, schema_resolver)
            except (RefResolutionError, InvalidSchema):
                continue
            fields.update(_collect_properties(alternative, alternative_resolver))
    return frozenset(fields) or None
