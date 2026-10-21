from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, TypeAlias

from schemathesis.core import media_types
from schemathesis.core.errors import InvalidSchema, MalformedMediaType
from schemathesis.core.jsonschema import maybe_resolve_bundled
from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY
from schemathesis.core.jsonschema.resolver import Resolver
from schemathesis.core.jsonschema.types import JsonSchema, get_type
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import format_body_key
from schemathesis.specs.openapi.adapter.parameters import resource_name_from_ref
from schemathesis.specs.openapi.converter import is_read_only
from schemathesis.specs.openapi.stateful.dependencies import naming
from schemathesis.specs.openapi.stateful.dependencies.models import (
    BodyPath,
    CanonicalizationCache,
    Cardinality,
    DeferredBindings,
    DefinitionSource,
    InputSlot,
    OperationMap,
    OperationNode,
    OutputSlot,
    ResourceDefinition,
    ResourceMap,
    infer_fk_target,
)
from schemathesis.specs.openapi.stateful.dependencies.resources import (
    ResponseResourceCache,
    allowed_values,
    cached_resources_from_responses,
    contains_value,
    declared_field_values,
    write_only_fields,
)

if TYPE_CHECKING:
    from schemathesis.specs.openapi.adapter.parameters import OpenApiBody, OpenApiParameter
    from schemathesis.specs.openapi.schemas import APIOperation, OpenApiSchema


def extract_inputs(
    *,
    operation: APIOperation,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: Resolver,
    canonicalization_cache: CanonicalizationCache,
    response_resource_cache: ResponseResourceCache,
    nested_fk_cache: NestedFKCache,
    deferred: DeferredBindings,
    candidate_resource_names: frozenset[str] = frozenset(),
) -> Iterator[InputSlot]:
    """Extract resource dependencies for an API operation from its input parameters.

    Connects each parameter (e.g., `userId`) to its resource definition (`User`),
    creating placeholder resources if not yet discovered from their schemas.

    `deferred` collects what binds only after every operation has been scanned, e.g. nested-body
    FK lookups whose target resource isn't registered yet.

    `candidate_resource_names` gates `<word>_name` body-field synthetics: only
    creates a placeholder when the inferred name is backed by a path segment or
    component schema.
    """
    known_dependencies = set()
    # Updates address the resource bound by the trailing path parameter, e.g. `PATCH /users/{userId}`.
    updated_parameter = (
        naming.trailing_path_parameter(operation.path) if operation.method.lower() in ("put", "patch") else None
    )
    updated_resource = None
    for param in operation.iter_parameters():
        input_slot = _resolve_parameter_dependency(
            parameter_name=param.name,
            parameter_schema=_parameter_schema(param),
            parameter_types=declared_parameter_types(param),
            parameter_location=param.location,
            operation=operation,
            resources=resources,
            updated_resources=updated_resources,
            resolver=resolver,
            canonicalization_cache=canonicalization_cache,
            response_resource_cache=response_resource_cache,
        )
        if input_slot is not None:
            if input_slot.resource.source >= DefinitionSource.SCHEMA_WITH_PROPERTIES:
                known_dependencies.add(input_slot.resource.name)
            if param.location == ParameterLocation.PATH and param.name == updated_parameter:
                updated_resource = input_slot.resource.name
            yield input_slot
        elif param.location == ParameterLocation.PATH:
            owner = naming.owning_resource(param.name, operation.path)
            if owner is not None:
                deferred.field_named_path_parameters.append((owner, param.name))

    json_bodies = []
    form_bodies = []
    for body in operation.body:
        try:
            media_type = media_types.parse(body.media_type)
        except MalformedMediaType:
            continue
        if media_types.is_json_parts(media_type):
            json_bodies.append(body)
        elif media_types.is_form_parts(media_type):
            form_bodies.append(body)
    # Link values fill whichever body variant is sent, so form fields matter only when there is no JSON variant.
    for body in json_bodies or form_bodies:
        yield from _resolve_body_dependencies(
            body=body,
            operation=operation,
            resources=resources,
            known_dependencies=known_dependencies,
            nested_fk_cache=nested_fk_cache,
            updated_resource=updated_resource,
            deferred=deferred,
            candidate_resource_names=candidate_resource_names,
        )


def _parameter_schema(parameter: OpenApiParameter) -> JsonSchema:
    try:
        return parameter.raw_schema
    except InvalidSchema:
        return {}


def declared_parameter_types(parameter: OpenApiParameter) -> list[str]:
    try:
        return get_type(parameter.raw_schema)
    except InvalidSchema:
        return []


def _resolve_parameter_dependency(
    *,
    parameter_name: str,
    parameter_schema: JsonSchema,
    parameter_types: list[str],
    parameter_location: ParameterLocation,
    operation: APIOperation,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: Resolver,
    canonicalization_cache: CanonicalizationCache,
    response_resource_cache: ResponseResourceCache,
) -> InputSlot | None:
    """Connect a parameter to its resource definition, creating placeholder if needed.

    Strategy:
    1. Infer resource name from parameter (`userId` -> `User`)
    2. Use existing resource if high-quality definition exists
    3. Try discovering from operation's response schemas
    4. Fall back to creating placeholder with a single field
    """
    resource_name = naming.from_parameter(parameter=parameter_name, path=operation.path)

    if resource_name is None:
        return None

    resource = resources.get(resource_name)

    # Upgrade low-quality resource definitions (e.g., from parameter inference)
    # by searching this operation's responses for actual schema
    if resource is None or resource.source < DefinitionSource.SCHEMA_WITH_PROPERTIES:
        resource = _find_resource_in_responses(
            operation=operation,
            resource_name=resource_name,
            resources=resources,
            updated_resources=updated_resources,
            resolver=resolver,
            canonicalization_cache=canonicalization_cache,
            response_resource_cache=response_resource_cache,
        )
        if resource is not None:
            resources[resource_name] = resource

    # Determine resource and its field
    is_suffix_matched = False
    if resource is None:
        # Try to find an existing resource with matching suffix
        # Example: parameter "file_name" -> inferred "File" not found -> check if "BackupFile" exists
        matched_resource, matched_field = _find_matching_resource_by_suffix(
            resource_name=resource_name,
            parameter_name=parameter_name,
            resources=resources,
        )
        if matched_resource is not None and matched_field is not None:
            resource = matched_resource
            resource_name = matched_resource.name
            field = matched_field
            is_suffix_matched = True
        else:
            # No schema found - create placeholder resource with inferred field
            #
            # Example: `DELETE /users/{userId}` with no response body -> `User` resource with "userId" field
            #
            # Later operations with schemas will upgrade this placeholder
            if resource_name in resources:
                # Resource exists but was empty - update with parameter field
                resources[resource_name].fields = [parameter_name]
                resources[resource_name].source = DefinitionSource.PARAMETER_INFERENCE
                updated_resources.add(resource_name)
                resource = resources[resource_name]
            else:
                resource = ResourceDefinition.inferred_from_parameter(
                    name=resource_name,
                    parameter_name=parameter_name,
                )
                resources[resource_name] = resource
            field = parameter_name
    else:
        field = _prefer_string_identifier(
            _match_field(parameter_name, resource_name, resource.fields),
            resource,
            parameter_name,
            parameter_schema,
            parameter_types,
        )

    return InputSlot(
        resource=resource,
        resource_field=field,
        parameter_name=parameter_name,
        parameter_location=parameter_location,
        is_suffix_matched=is_suffix_matched,
    )


def _find_resource_in_responses(
    *,
    operation: APIOperation,
    resource_name: str,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: Resolver,
    canonicalization_cache: CanonicalizationCache,
    response_resource_cache: ResponseResourceCache,
) -> ResourceDefinition | None:
    """Search operation's successful responses for a specific resource definition.

    Used when a parameter references a resource not yet discovered. Scans this
    operation's response schemas hoping to find the resource definition.
    """
    for _, extracted in cached_resources_from_responses(
        operation=operation,
        resources=resources,
        updated_resources=updated_resources,
        resolver=resolver,
        canonicalization_cache=canonicalization_cache,
        cache=response_resource_cache,
    ):
        if extracted.resource.name == resource_name:
            return extracted.resource

    return None


def _find_matching_resource_by_suffix(
    *,
    resource_name: str,
    parameter_name: str,
    resources: ResourceMap,
) -> tuple[ResourceDefinition, str] | tuple[None, None]:
    """Find a resource with matching suffix or prefix when exact match not found.

    When a parameter like "file_name" infers resource "File" but no "File" exists,
    check if a resource ending with "File" exists (e.g., "BackupFile") or starting
    with "File" (e.g., "FileSummary") and if the parameter can be matched to one
    of its fields.

    Suffix matching example: "file_name" -> "File" -> "BackupFile"
    Prefix matching example: "group_slug" -> "Group" -> "GroupSummary"

    Only considers high-quality resources (schema-defined with properties) and
    requires the parameter to match a field via find_matching_field.
    """
    return next(
        _iter_suffix_matches(resource_name=resource_name, parameter_name=parameter_name, resources=resources),
        (None, None),
    )


def _iter_suffix_matches(
    *, resource_name: str, parameter_name: str, resources: ResourceMap
) -> Iterator[tuple[ResourceDefinition, str]]:
    # Normalize for case-insensitive matching
    resource_lower = resource_name.lower()

    for candidate_name, candidate_resource in resources.items():
        # Only consider schema-defined resources
        if candidate_resource.source < DefinitionSource.SCHEMA_WITH_PROPERTIES:
            continue

        candidate_lower = candidate_name.lower()

        # Check if candidate ends with OR starts with the inferred resource name
        # Suffix: "BackupFile".endswith("file") for resource_name="File"
        # Prefix: "GroupSummary".startswith("group") for resource_name="Group"
        if not (candidate_lower.endswith(resource_lower) or candidate_lower.startswith(resource_lower)):
            continue

        # Check if parameter can be matched to a field
        matched_field = naming.find_matching_field(
            parameter=parameter_name,
            resource=candidate_name,
            fields=candidate_resource.fields,
        )
        if matched_field is not None:
            yield candidate_resource, matched_field


GENERIC_FIELD_NAMES = frozenset(
    {
        "body",
        "text",
        "content",
        "message",
        "description",
    }
)


def _flatten_composition(schema: dict[str, Any], bundled: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Merge ``properties`` and ``required`` across ``allOf``/``oneOf``/``anyOf`` branches.

    First-seen property definition wins; required is a union across branches.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    seen: set[str] = set()

    def merge(node: dict[str, Any]) -> None:
        reference = node.get("$ref")
        if isinstance(reference, str) and reference not in seen:
            seen.add(reference)
            target = bundled.get(reference.rsplit("/", 1)[-1])
            if isinstance(target, dict):
                merge(target)
        node_properties = node.get("properties")
        if isinstance(node_properties, dict):
            for name, subschema in node_properties.items():
                properties.setdefault(name, subschema)
        node_required = node.get("required")
        if isinstance(node_required, list):
            required.extend(node_required)
        for key in ("allOf", "oneOf", "anyOf"):
            branches = node.get(key)
            if isinstance(branches, list):
                for branch in branches:
                    if isinstance(branch, dict):
                        merge(branch)

    merge(schema)
    return properties, required


def assigns_identifier(operation: APIOperation, resource_name: str) -> bool:
    """Whether the request body declares a `readOnly` identifier of `resource_name`, which the server sets."""
    for body in operation.body:
        schema = body.raw_schema
        if not isinstance(schema, dict):
            continue
        bundled = schema.get(BUNDLE_STORAGE_KEY, {})
        properties, _ = _flatten_composition(maybe_resolve_bundled(schema), bundled)
        for name, subschema in properties.items():
            lower = name.lower()
            if (
                (lower == "id" or lower.endswith(("_id", "-id")) or name.endswith(("Id", "ID")))
                and naming.from_parameter(name, operation.path, body_field=True) == resource_name
                and is_read_only(subschema, bundled)
            ):
                return True
    return False


_SCALAR_TYPES = frozenset({"string", "integer", "number"})


def _is_scalar(schema: JsonSchema) -> bool:
    return isinstance(schema, dict) and bool(_SCALAR_TYPES & set(get_type(schema)))


def _resolve_body_dependencies(
    *,
    body: OpenApiBody,
    operation: APIOperation,
    resources: ResourceMap,
    known_dependencies: set[str],
    nested_fk_cache: NestedFKCache,
    updated_resource: str | None = None,
    deferred: DeferredBindings,
    candidate_resource_names: frozenset[str] = frozenset(),
) -> Iterator[InputSlot]:
    schema = body.raw_schema
    if not isinstance(schema, dict):
        return

    resolved = maybe_resolve_bundled(schema)
    bundled = schema.get(BUNDLE_STORAGE_KEY, {})

    # For `items`, we'll inject an array with extracted resource
    items = resolved.get("items")
    if isinstance(items, dict):
        resource_name = body.resource_name or naming.from_path(operation.path)

        if "$ref" in items:
            schema_key = items["$ref"].split("/")[-1]
            original_ref = body.name_to_uri[schema_key]
            resource_name = resource_name_from_ref(original_ref)
        if resource_name is not None:
            resource = resources.get(resource_name)
            if resource is None:
                resource = ResourceDefinition.inferred_from_parameter(name=resource_name, parameter_name=None)
                resources[resource_name] = resource
                field = None
            else:
                field = None
            yield InputSlot(
                resource=resource,
                resource_field=field,
                parameter_name=0,
                parameter_location=ParameterLocation.BODY,
            )

    # Inspect each property that could be a part of some other resource.
    # Flatten composition keywords first so bodies whose top level is allOf/oneOf/anyOf still surface their fields.
    properties, required = _flatten_composition(resolved, bundled)
    path = operation.path
    for property_name, subschema in properties.items():
        # Generation never sends `readOnly` fields, so a link must not either.
        if is_read_only(subschema, bundled):
            continue
        deferred.writable_body_fields[property_name] = subschema
        if property_name in required and _is_scalar(subschema):
            deferred.body_fields.append((property_name, subschema))
        resource_name = naming.from_parameter(property_name, path, body_field=True)
        # `_name` body fields are usually attributes; only invent a resource when its
        # name is backed by a path segment or component schema. Otherwise fall through
        # to the known-dependencies path so the field can still bind to a parent resource.
        gated = (
            resource_name is not None
            and resource_name not in resources
            and property_name.lower().endswith("_name")
            and resource_name not in candidate_resource_names
        )
        if resource_name is not None and not gated:
            resource = resources.get(resource_name)
            if resource is None:
                resource = ResourceDefinition.inferred_from_parameter(
                    name=resource_name,
                    parameter_name=property_name,
                )
                resources[resource_name] = resource
                field = property_name
            else:
                field = (
                    naming.find_matching_field(
                        parameter=property_name,
                        resource=resource_name,
                        fields=resource.fields,
                    )
                    or "id"
                )
            yield InputSlot(
                resource=resource,
                resource_field=field,
                parameter_name=property_name,
                parameter_location=ParameterLocation.BODY,
            )
            continue

        if resource_name is None and _is_scalar(subschema):
            # A field carrying no identifier suffix may still name the collection listing its
            # accepted values (`country` <- `GET /countries`). The producer is confirmed later.
            deferred.named_scalars.append((naming.to_pascal_case(property_name), property_name, subschema))

        # Skip generic property names & optional fields (at least for now)
        if property_name in GENERIC_FIELD_NAMES or property_name not in required:
            continue

        # Find candidate resources among known dependencies that actually have this field
        candidates = [
            resources[dep] for dep in known_dependencies if dep in resources and property_name in resources[dep].fields
        ]

        if len(candidates) > 1:
            # An update writes the resource its path addresses, whatever its parents carry
            candidates = [candidate for candidate in candidates if candidate.name == updated_resource]
        # Skip ambiguous cases when multiple resources have same field name
        if len(candidates) != 1:
            continue

        resource = candidates[0]
        # Ensure the target field supports the same type
        if not resource.types[property_name] & set(get_type(subschema)):
            continue
        # A name labels the resource being written: only an update may keep its current one.
        if naming.key_kind(property_name) == naming.KeyKind.NAME and resource.name != updated_resource:
            continue

        yield InputSlot(
            resource=resource,
            resource_field=property_name,
            parameter_name=property_name,
            parameter_location=ParameterLocation.BODY,
        )

    # Recursively find nested FK fields in request body
    scan = NestedFKScan(
        bundled=bundled, name_to_uri=body.name_to_uri, by_reference=nested_fk_cache, by_schema={}, flattened={}
    )
    yield from _extract_nested_body_fk_fields(resolved, resources, scan, deferred=deferred.nested_fks)


# FK fields behind a bundled reference, by the reference's original URI and the remaining depth.
NestedFKCache: TypeAlias = dict[tuple[str, int], list[tuple[BodyPath, str, str]]]


@dataclass(slots=True)
class NestedFKScan:
    """Scan of one request body for nested FK fields."""

    bundled: dict[str, Any]
    name_to_uri: dict[str, str]
    # Bodies bundle the same references separately, yet find the same FK fields behind them.
    by_reference: NestedFKCache
    # Bodies reuse the same schemas at many places; each is scanned once per remaining depth.
    by_schema: dict[tuple[int, int], list[tuple[BodyPath, str, str]]]
    # Flattened writable properties, once whatever the remaining depth.
    flattened: dict[int, dict[str, Any]]


def _extract_nested_body_fk_fields(
    schema: dict[str, Any],
    resources: ResourceMap,
    scan: NestedFKScan,
    deferred: list[tuple[str, str, BodyPath]],
) -> Iterator[InputSlot]:
    """Recursively extract FK fields from nested request body schemas.

    Scans nested objects and array items to find FK fields like:
    - {shipping: {warehouse_id: "..."}} -> InputSlot for warehouse_id
    - {items: [{product_id: "..."}]} -> InputSlot for product_id

    When the FK target resource isn't yet registered, the lookup is appended to
    `deferred` so the caller can replay it once every operation has been scanned.
    """
    items = schema.get("items")
    children: list[tuple[str | int, Any]]
    if isinstance(items, dict):
        # Items of a top-level array body carry FK fields like nested array items: `[{"groupId": ...}]`.
        children = [(0, items)]
    else:
        properties, _ = _flatten_composition(schema, scan.bundled)
        children = [*properties.items()]
    for property_name, subschema in children:
        if not isinstance(subschema, dict) or is_read_only(subschema, scan.bundled):
            continue
        for suffix, target_resource_name, target_field in _container_fk_fields(subschema, scan, 5):
            nested_path = (property_name, *suffix)
            resource = resources.get(target_resource_name)
            if resource is not None:
                yield InputSlot(
                    resource=resource,
                    resource_field=target_field,
                    parameter_name=format_body_key(nested_path),
                    parameter_location=ParameterLocation.BODY,
                    body_path=nested_path,
                )
            else:
                deferred.append((target_resource_name, target_field, nested_path))


def _container_fk_fields(schema: dict[str, Any], scan: NestedFKScan, max_depth: int) -> list[tuple[BodyPath, str, str]]:
    """FK fields under `schema` as `(path suffix, target resource, target field)`."""
    key = (id(schema), max_depth)
    cached = scan.by_schema.get(key)
    if cached is not None:
        return cached
    fields: list[tuple[BodyPath, str, str]] = []
    scan.by_schema[key] = fields
    reference = schema.get("$ref")
    if isinstance(reference, str):
        bundled_name = reference.rsplit("/", 1)[-1]
        target = scan.bundled.get(bundled_name)
        uri = scan.name_to_uri.get(bundled_name)
        # A bare reference finds the same FK fields in every body that bundles its target.
        if len(schema) == 1 and uri is not None and isinstance(target, dict):
            shared = scan.by_reference.setdefault((uri, max_depth), fields)
            if shared is not fields:
                scan.by_schema[key] = shared
                return shared
        if isinstance(target, dict) and target.get("type") == "array":
            # Each followed reference spends depth, so an array whose items point back at it terminates.
            if max_depth <= 1:
                return fields
            schema = target
            max_depth -= 1
    if schema.get("type") == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            fields.extend(
                ((0, *suffix), resource, field)
                for suffix, resource, field in _container_fk_fields(items, scan, max_depth)
            )
        return fields

    # Composition branches may carry the actual object shape, so flatten before treating as object.
    properties = scan.flattened.get(id(schema))
    if properties is None:
        properties = scan.flattened[id(schema)] = {
            name: subschema
            for name, subschema in _flatten_composition(schema, scan.bundled)[0].items()
            if isinstance(subschema, dict) and not is_read_only(subschema, scan.bundled)
        }
    for name in properties:
        fk_info = infer_fk_target(name)
        if fk_info is None:
            continue
        target_resource_name, target_field, _ = fk_info
        fields.append(((name,), target_resource_name, target_field))

    if max_depth > 1:
        for name, subschema in properties.items():
            if isinstance(subschema, dict):
                fields.extend(
                    ((name, *suffix), resource, field)
                    for suffix, resource, field in _container_fk_fields(subschema, scan, max_depth - 1)
                )
    return fields


def rebind_inherited_suffix_matches(operations: OperationMap, resources: ResourceMap) -> None:
    """Re-resolve suffix matches that hold only through a field inherited via `allOf`.

    A field inherited from a shared base schema (`id` from a generic `Resource`) says little about
    which entity a prefix-named resource is: `ReservationTransaction.id` does not identify a reservation.
    Once every operation is known, the resource the parameter names wins if the API refers to it,
    and otherwise a field named after the parameter itself (`ReservationDetailProperties.reservationId`).
    """
    produced = _produced_resource_names(operations)
    for operation in operations.values():
        for input_slot in operation.inputs:
            parameter_name = input_slot.parameter_name
            if (
                not input_slot.is_suffix_matched
                or not isinstance(parameter_name, str)
                or input_slot.resource_field not in input_slot.resource.inherited_fields
            ):
                continue
            resource_name = naming.from_parameter(parameter=parameter_name, path=operation.path)
            if resource_name is None or resource_name.lower() == input_slot.resource.name.lower():
                continue
            named = resources.get(resource_name)
            if named is not None and named.source != DefinitionSource.PARAMETER_INFERENCE and resource_name in produced:
                input_slot.resource = named
                input_slot.resource_field = _match_field(parameter_name, resource_name, named.fields)
                input_slot.is_suffix_matched = False
                continue
            parameter_normalized = naming.normalize_for_matching(parameter_name)
            for candidate, field in _iter_suffix_matches(
                resource_name=resource_name, parameter_name=parameter_name, resources=resources
            ):
                if naming.normalize_for_matching(field) == parameter_normalized:
                    input_slot.resource = candidate
                    input_slot.resource_field = field
                    break


def _match_field(parameter_name: str, resource_name: str, fields: list[str]) -> str:
    # Match parameter to resource field (`userId` -> `id`, `Id` -> `ChannelId`, etc.)
    matched = naming.find_matching_field(parameter=parameter_name, resource=resource_name, fields=fields)
    if matched is not None:
        return matched
    if "id" in fields:
        # Conventional fallback: `<resource>Id` parameters point at the resource's `id` field.
        return "id"
    # Without an `id` field, use the parameter name so peer captures reach this slot.
    return parameter_name


def _prefer_string_identifier(
    field: str,
    resource: ResourceDefinition,
    parameter_name: str,
    parameter_schema: JsonSchema,
    parameter_types: list[str],
) -> str:
    # A string parameter carries the resource's string identifier (`uuid`), not a numeric one (`id`), unless its
    # name matches the field exactly or its `pattern` / `format` rules a UUID out.
    if (
        parameter_types != ["string"]
        or naming.normalize_for_matching(parameter_name) == naming.normalize_for_matching(field)
        or not isinstance(parameter_schema, dict)
        or "pattern" in parameter_schema
        or parameter_schema.get("format") not in (None, "uuid")
        or naming.normalize_for_matching(field) not in naming.ID_FIELD_NAMES
        or "string" in resource.types.get(field, {"string"})
    ):
        return field
    for identifier in naming.ID_FIELD_NAMES:
        for candidate in resource.fields:
            if naming.normalize_for_matching(candidate) == identifier and "string" in resource.types.get(candidate, ()):
                return candidate
    return field


def update_input_field_bindings(resource_names: set[str], operations: OperationMap) -> None:
    """Update input slots field bindings after resource definition was upgraded.

    When a resource's fields change (e.g., `User` upgraded from `["userId"]` to `["id", "email"]`),
    existing input slots may reference stale field names. This re-evaluates field matching
    for all operations using these resources.

    Example:
        `DELETE /users/{userId}` created `InputSlot(resource_field="userId")`
        `POST /users` revealed actual fields `["id", "email"]`
        This updates DELETE's `InputSlot` to use `resource_field="id"`

    """
    # Re-evaluate field matching for all operations referencing these resources
    for operation in operations.values():
        for input_slot in operation.inputs:
            # Skip inputs not using these resources
            if input_slot.resource.name not in resource_names or isinstance(input_slot.parameter_name, int):
                continue

            # Re-match parameter to upgraded resource fields
            new_field = naming.find_matching_field(
                parameter=input_slot.parameter_name,
                resource=input_slot.resource.name,
                fields=input_slot.resource.fields,
            )
            if new_field is not None:
                input_slot.resource_field = new_field


def merge_related_resources(operations: OperationMap, resources: ResourceMap) -> None:
    """Merge parameter-inferred resources with schema-defined resources from related operations."""
    candidates = find_producer_consumer_candidates(operations)
    returned_by: dict[tuple[str, str], set[str]] = {}
    for label, node in operations.items():
        base_path = _extract_base_path(node.path)
        for output in node.outputs:
            returned_by.setdefault((base_path, output.resource.name), set()).add(label)

    for producer_name, consumer_name in candidates:
        producer = operations[producer_name]
        consumer = operations[consumer_name]

        # Try to upgrade each input slot
        for input_slot in consumer.inputs:
            # A fieldless resource its own collection returns is real (a polymorphic listing), not a path-named stub.
            if input_slot.resource.source == DefinitionSource.SCHEMA_WITHOUT_PROPERTIES and returned_by.get(
                (_extract_base_path(consumer.path), input_slot.resource.name), set()
            ) - {consumer_name}:
                continue
            result = try_merge_input_resource(input_slot, producer.outputs, resources)

            if result is not None:
                new_resource_name, new_field_name = result
                # Update input slot to use the better resource definition
                input_slot.resource = resources[new_resource_name]
                input_slot.resource_field = new_field_name


def rebind_body_fields_to_unique_producers(
    operations: OperationMap,
    deferred: dict[str, DeferredBindings],
) -> None:
    """Bind required scalar body fields to the sole response output that declares them."""
    candidates: dict[str, list[tuple[OperationNode, OutputSlot]]] = {}
    produced_resources = set()
    for operation in operations.values():
        for output in operation.outputs:
            produced_resources.add(id(output.resource))
            if output.resource.source < DefinitionSource.SCHEMA_WITH_PROPERTIES:
                continue
            declared = output.response_fields
            if declared is None:
                declared = frozenset(output.resource.fields)
            for field in declared:
                candidates.setdefault(field, []).append((operation, output))

    for label, bindings in deferred.items():
        fields = bindings.body_fields
        consumer = operations[label]
        slots = {
            slot.parameter_name: slot
            for slot in consumer.inputs
            if slot.parameter_location == ParameterLocation.BODY and isinstance(slot.parameter_name, str)
        }
        matched_outputs: dict[int, tuple[OutputSlot, set[str]]] = {}
        for field, field_schema in fields:
            slot = slots.get(field)
            if (
                slot is None
                or slot.resource.source != DefinitionSource.PARAMETER_INFERENCE
                or id(slot.resource) in produced_resources
            ):
                continue
            matches = [
                (producer, output)
                for producer, output in candidates.get(field, ())
                if output.resource.types.get(field, set()) & set(get_type(field_schema))
            ]
            if (
                len(matches) != 1
                or matches[0][0] is consumer
                or any(slot.parameter_name == field for slot in matches[0][0].inputs)
            ):
                continue
            output = matches[0][1]
            inferred_resource_name = slot.resource.name
            slot.resource = output.resource
            slot.resource_field = field
            matched_output = matched_outputs.setdefault(id(output), (output, set()))
            matched_output[1].add(inferred_resource_name)

        if len(matched_outputs) != 1:
            continue
        output, inferred_resource_names = next(iter(matched_outputs.values()))
        declared = output.response_fields
        if declared is None:
            declared = frozenset(output.resource.fields)
        for field, field_schema in fields:
            if (
                field not in slots
                and field in declared
                and any(_has_resource_prefix(field, resource_name) for resource_name in inferred_resource_names)
                and output.resource.types.get(field, set()) & set(get_type(field_schema))
            ):
                consumer.inputs.append(
                    InputSlot(
                        resource=output.resource,
                        resource_field=field,
                        parameter_name=field,
                        parameter_location=ParameterLocation.BODY,
                    )
                )


def bind_echoed_body_fields(
    schema: OpenApiSchema, operations: OperationMap, deferred: dict[str, DeferredBindings]
) -> None:
    """Bind update body fields to what the item `GET` returns but creation cannot set, e.g. a `version` token."""
    labels = {(node.method.lower(), node.path): label for label, node in operations.items()}
    for label, bindings in deferred.items():
        fields = bindings.writable_body_fields
        update = operations[label]
        if update.method.lower() not in ("put", "patch") or naming.trailing_path_parameter(update.path) is None:
            continue
        reader_label = labels.get(("get", update.path), "")
        reader = operations.get(reader_label)
        if reader is None:
            continue
        # Only creation tells client-set fields apart from server-set ones; client-set fields keep generated values.
        creation = deferred.get(labels.get(("post", naming.collection_path(update.path)), ""))
        if creation is None or not creation.writable_body_fields:
            continue
        settable = creation.writable_body_fields
        bound = {slot.parameter_name for slot in update.inputs if slot.parameter_location == ParameterLocation.BODY}
        for output in reader.outputs:
            # Only a single object that declares its fields can promise a value to send back.
            if output.cardinality != Cardinality.ONE or output.response_fields is None:
                continue
            for field, field_schema in fields.items():
                if (
                    field in bound
                    or field in settable
                    or not isinstance(field_schema, dict)
                    or "type" not in field_schema
                    or not _is_scalar(field_schema)
                    or field not in output.response_fields
                    or not output.resource.types.get(field, set()) & set(get_type(field_schema))
                    or not _accepts_read_values(schema, reader_label, output, field, field_schema)
                ):
                    continue
                update.inputs.append(
                    InputSlot(
                        resource=output.resource,
                        resource_field=field,
                        parameter_name=field,
                        parameter_location=ParameterLocation.BODY,
                        echoes_response=True,
                    )
                )
                bound.add(field)


def _accepts_read_values(
    schema: OpenApiSchema, reader_label: str, output: OutputSlot, field: str, field_schema: dict[str, Any]
) -> bool:
    # A read `state` may hold values an update rejects, e.g. `running` when only `success` or `failed` can be set.
    accepted = allowed_values(field_schema, None)
    if accepted is None:
        return True
    reader = schema.find_operation_by_label(reader_label)
    assert reader is not None
    response = reader.responses.get(output.status_code)
    # The output was extracted from this response.
    assert response is not None
    read = declared_field_values(response, output.pointer, field, schema.root_resolver)
    return read is None or all(contains_value(accepted, value) for value in read)


def _has_resource_prefix(field: str, resource_name: str) -> bool:
    if len(field) <= len(resource_name) or field[: len(resource_name)].lower() != resource_name.lower():
        return False
    boundary = field[len(resource_name)]
    return boundary in "_-" or boundary.isupper()


def rebind_orphan_synthetics(operations: OperationMap, resources: ResourceMap) -> None:
    """Rebind body and query slots from producer-less synthetics to a same-operation parent.

    `<word>_id` fields produce a synthetic `<Word>` resource; when nothing else in
    the spec backs that name (no path, no schema, no producer) but the operation's
    own response describes a parent resource carrying the same field, the slot is
    really a self-FK (`spouse_id` on `POST /contacts`, `?sequence_id=` on `GET /events`).
    """
    producer_resources = _produced_resource_names(operations)
    for operation in operations.values():
        parent_name = naming.from_path(operation.path)
        if parent_name is None:
            continue
        parent = resources.get(parent_name)
        if parent is None or parent.source < DefinitionSource.SCHEMA_WITH_PROPERTIES:
            continue
        for input_slot in operation.inputs:
            if input_slot.parameter_location not in (ParameterLocation.BODY, ParameterLocation.QUERY):
                continue
            if input_slot.resource.source != DefinitionSource.PARAMETER_INFERENCE:
                continue
            if input_slot.resource.name in producer_resources:
                continue
            if not isinstance(input_slot.parameter_name, str):
                continue
            # Require an exact field-name match so we only rebind genuine self-FKs
            # (`spouse_id` on `POST /contacts` when Contact has a `spouse_id` field) and
            # leave ambiguous cases (`clientId` on `POST /applications`) alone.
            if input_slot.parameter_name in parent.fields:
                input_slot.resource = parent
                input_slot.resource_field = input_slot.parameter_name


def _produced_resource_names(operations: OperationMap) -> set[str]:
    return {output.resource.name for operation in operations.values() for output in operation.outputs}


def _producer_modules(operations: OperationMap) -> dict[str, set[str]]:
    producer_modules: dict[str, set[str]] = {}
    for operation in operations.values():
        module = _module_of(operation.path)
        if not module:
            continue
        for output in operation.outputs:
            producer_modules.setdefault(output.resource.name, set()).add(module)
    return producer_modules


def disambiguate_module_variants(operations: OperationMap, resources: ResourceMap) -> None:
    """Swap to a same-module sibling for spec-suffixed duplicates (`Group` / `Group1`).

    Without this, every consumer in the second module binds to the first module's
    variant via the path-derived lookup.
    """
    producer_modules = _producer_modules(operations)

    by_stem: dict[str, set[str]] = {}
    for resource_name in producer_modules:
        by_stem.setdefault(_strip_trailing_digits(resource_name), set()).add(resource_name)

    for operation in operations.values():
        consumer_module = _module_of(operation.path)
        if not consumer_module:
            continue
        for input_slot in operation.inputs:
            modules = producer_modules.get(input_slot.resource.name)
            if not modules or consumer_module in modules:
                continue
            family = by_stem.get(_strip_trailing_digits(input_slot.resource.name), set())
            siblings = [
                name
                for name in family
                if name != input_slot.resource.name and consumer_module in producer_modules[name]
            ]
            if len(siblings) != 1:
                continue
            new_resource = resources[siblings[0]]
            new_field = (
                naming.find_matching_field(
                    parameter=input_slot.parameter_name if isinstance(input_slot.parameter_name, str) else "",
                    resource=new_resource.name,
                    fields=new_resource.fields,
                )
                or input_slot.resource_field
            )
            input_slot.resource = new_resource
            input_slot.resource_field = new_field


def disambiguate_path_suffix_matches(operations: OperationMap, resources: ResourceMap) -> None:
    """Swap a path slot to a same-module resource that also matches the path-derived name.

    When `_resolve_parameter_dependency` falls back to suffix-matching, it picks the
    first registered candidate — leaving `/bookings/resources/{id}` bound to
    `KeyDateResource` instead of `ResourceItem`. This pass corrects only when the
    consumer's path-derived name picks both the current and a same-module sibling.
    """
    producer_modules = _producer_modules(operations)

    for operation in operations.values():
        consumer_module = _module_of(operation.path)
        if not consumer_module:
            continue
        own_output_names = {output.resource.name for output in operation.outputs}
        # Limit to operations where the rebinding parameter is the leaf — otherwise
        # `/images/{imageId}/regionproposals` would rebind {imageId} to the response
        # type instead of Image.
        leaf_param = naming.trailing_path_parameter(operation.path)
        for input_slot in operation.inputs:
            if input_slot.parameter_location != ParameterLocation.PATH:
                continue
            if not isinstance(input_slot.parameter_name, str):
                continue
            if input_slot.parameter_name != leaf_param:
                continue
            modules = producer_modules.get(input_slot.resource.name)
            if not modules or consumer_module in modules:
                continue
            # Anchor the lookup on the segment that actually owns this path parameter.
            path_name = naming.from_path(operation.path, parameter_name=input_slot.parameter_name)
            if not path_name:
                continue
            path_name_lower = path_name.lower()
            current_lower = input_slot.resource.name.lower()
            if not (current_lower.endswith(path_name_lower) or current_lower.startswith(path_name_lower)):
                continue
            # Prefer a sibling that the operation's own response describes — that's the
            # resource the path actually identifies.
            siblings = [
                name
                for name in producer_modules
                if name != input_slot.resource.name
                and consumer_module in producer_modules[name]
                and name in own_output_names
                and (name.lower().endswith(path_name_lower) or name.lower().startswith(path_name_lower))
            ]
            if len(siblings) != 1:
                continue
            new_resource = resources[siblings[0]]
            # Require an exact field-name match; loose synonym matches (`applicationId` -> `id`)
            # would happily rebind cross-references like `ApplicationInfo` -> `ApplicationEvent`.
            if input_slot.parameter_name not in new_resource.fields:
                continue
            input_slot.resource = new_resource
            input_slot.resource_field = input_slot.parameter_name


def bind_item_parameters_to_collections(
    operations: OperationMap, path_parameter_types: dict[str, dict[str, list[str]]]
) -> None:
    """Bind `/things/{id}` path parameters to the resource `GET /things` lists when nothing else can supply the id."""
    listed: dict[str, dict[str, ResourceDefinition]] = {}
    # Collection names each resource is listed under; a shape listed by `/groups` and `/domains` alike is generic.
    listed_under: dict[str, set[str]] = {}
    # Collection paths each resource is listed under; `/apis/{api}/comments` and `/domains/{domain}/comments` differ
    listed_at: dict[str, set[str]] = {}
    producers: dict[str, list[tuple[OperationNode, OutputSlot]]] = {}
    # `(resource, field)` pairs that `*_id` fields of produced resources point to
    fed: set[tuple[str, str]] = set()
    for operation in operations.values():
        for output in operation.outputs:
            producers.setdefault(output.resource.name, []).append((operation, output))
            fed.update((fk.target_resource, fk.target_field) for fk in output.resource.fk_fields)
            fed.update((fk.target_resource, fk.target_field) for fk in output.resource.nested_fk_fields)
            if operation.method == "get" and output.cardinality == Cardinality.MANY:
                collection = operation.path.rstrip("/")
                listed.setdefault(collection, {})[output.resource.name] = output.resource
                listed_under.setdefault(output.resource.name, set()).add(collection.rsplit("/", 1)[-1])
                listed_at.setdefault(output.resource.name, set()).add(re.sub(r"\{[^}]*\}", "{}", collection))

    listed_identifiers: list[tuple[OperationNode, InputSlot]] = []
    for operation in operations.values():
        segments = operation.path.split("/")
        for input_slot in operation.inputs:
            parameter_name = input_slot.parameter_name
            if input_slot.parameter_location != ParameterLocation.PATH or not isinstance(parameter_name, str):
                continue
            placeholder = "{" + parameter_name + "}"
            if placeholder not in segments:
                continue
            index = segments.index(placeholder)
            if index < 2 or segments[index - 1].startswith("{"):
                continue
            # `{warehouse}` under `/warehouses` names a listed warehouse, so a slot nothing can fill takes its identifier
            # `/apis/{api}/{version}` keys one resource by two segments, which no single listed field matches
            names_collection = (
                (
                    input_slot.resource.name not in producers
                    or input_slot.resource_field not in input_slot.resource.fields
                )
                and not (index + 1 < len(segments) and segments[index + 1].startswith("{"))
                and naming.normalize_for_matching(parameter_name)
                == naming.normalize_for_matching(naming.to_singular(segments[index - 1]))
            )
            candidates = [
                (resource, field)
                for resource in listed.get("/".join(segments[:index]), {}).values()
                if len(listed_under[resource.name]) == 1
                and (
                    field := _listed_field(
                        resource,
                        parameter_name,
                        names_collection=names_collection and len(listed_at[resource.name]) == 1,
                        parameter_types=path_parameter_types.get(
                            f"{operation.method.upper()} {operation.path}", {}
                        ).get(parameter_name, []),
                    )
                )
                is not None
            ]
            if len(candidates) != 1 or candidates[0][0].name == input_slot.resource.name:
                continue
            candidate, field = candidates[0]
            if field != parameter_name:
                # The current slot stays: foreign keys of other responses may still fill it
                listed_identifiers.append(
                    (
                        operation,
                        InputSlot(
                            resource=candidate,
                            resource_field=field,
                            parameter_name=parameter_name,
                            parameter_location=ParameterLocation.PATH,
                            is_listed_identifier=True,
                        ),
                    )
                )
                continue
            current = input_slot.resource.name
            if input_slot.resource_field is not None and (current, input_slot.resource_field) in fed:
                continue
            # Only a binding whose every producer needs this very value is replaced; others may get values elsewhere.
            current_producers = producers.get(current, [])
            if not all(
                any(
                    slot.parameter_name == parameter_name and slot.parameter_location == ParameterLocation.PATH
                    for slot in producer.inputs
                )
                for producer, _ in current_producers
            ):
                continue
            input_slot.resource = candidate
            input_slot.resource_field = field
            input_slot.is_suffix_matched = False
            # `POST /things/{id}` still creates what the listing lists
            for producer, output in current_producers:
                if output.path_parameter == parameter_name and not any(
                    existing.resource is candidate and existing.path_parameter == parameter_name
                    for existing in producer.outputs
                ):
                    producer.outputs.append(replace(output, resource=candidate))

    for operation, slot in listed_identifiers:
        operation.inputs.append(slot)


def _listed_field(
    resource: ResourceDefinition, parameter_name: str, *, names_collection: bool, parameter_types: list[str]
) -> str | None:
    if parameter_name in resource.fields:
        return parameter_name
    if names_collection:
        for identifier in ("id", "slug", "key", "name"):
            if identifier in resource.fields and _is_segment_identifier(resource, identifier, parameter_types):
                return identifier
    return None


def _is_segment_identifier(resource: ResourceDefinition, identifier: str, parameter_types: list[str]) -> bool:
    # The value must fit the parameter, and full resource paths are not segments: an ARM resource's `id` (next to
    # `name` and `type`) and an AIP resource's `name` (next to `displayName`) spell the whole path
    accepted = set(parameter_types or ["string"])
    if "integer" in accepted:
        accepted.add("number")
    if not accepted & resource.types.get(identifier, accepted):
        return False
    if identifier == "id":
        return not {"name", "type"} <= set(resource.fields)
    if identifier == "name":
        return "displayName" not in resource.fields
    return True


def align_created_identifiers(operations: OperationMap) -> None:
    """Give a bare id created by `POST /things` the resource `/things/{id}` takes, so the two link up."""
    item_resources: dict[str, dict[str, ResourceDefinition]] = {}
    for operation in operations.values():
        segments = operation.path.rstrip("/").split("/")
        if not segments[-1].startswith("{"):
            continue
        parameter_name = segments[-1][1:-1]
        for input_slot in operation.inputs:
            if input_slot.parameter_location == ParameterLocation.PATH and input_slot.parameter_name == parameter_name:
                item_resources.setdefault("/".join(segments[:-1]), {})[input_slot.resource.name] = input_slot.resource
    for operation in operations.values():
        if operation.method not in ("post", "put"):
            continue
        targets = item_resources.get(operation.path.rstrip("/"), {})
        if len(targets) != 1:
            continue
        (target,) = targets.values()
        operation.outputs += [
            replace(output, resource=target)
            for output in operation.outputs
            if output.is_primitive_identifier
            and output.cardinality == Cardinality.ONE
            and output.resource is not target
        ]


def bind_created_item_keys(
    schema: OpenApiSchema, operations: OperationMap, deferred: dict[str, DeferredBindings]
) -> None:
    """Link the key a client sends to `POST /things` to what `/things/{key}` takes, e.g. a chosen `title`."""
    item_slots: dict[str, dict[str, InputSlot]] = {}
    for operation in operations.values():
        parameter_name = naming.trailing_path_parameter(operation.path)
        for input_slot in operation.inputs:
            if (
                input_slot.parameter_location == ParameterLocation.PATH
                and input_slot.parameter_name == parameter_name
                and input_slot.resource_field is not None
            ):
                collection = naming.collection_path(operation.path)
                item_slots.setdefault(collection, {})[input_slot.resource.name] = input_slot
    for label, bindings in deferred.items():
        fields = bindings.writable_body_fields
        create = operations[label]
        slots = item_slots.get(create.path.rstrip("/"), {})
        if create.method.lower() != "post" or len(slots) != 1:
            continue
        (item_slot,) = slots.values()
        key = item_slot.parameter_name
        assert isinstance(key, str)
        if (
            key not in fields
            or not _is_scalar(fields[key])
            or any(
                slot.parameter_location == ParameterLocation.BODY and slot.parameter_name == key
                for slot in create.inputs
            )
        ):
            continue
        # A server-issued id is the identity, not the key the client sent.
        if any(
            naming.key_kind(field) == naming.KeyKind.IDENTIFIER
            for output in create.outputs
            for field in output.response_fields or ()
        ):
            continue
        declared = schema.find_operation_by_label(label)
        assert declared is not None
        status_code = declared.responses.first_successful_status_code()
        if status_code is None:
            continue
        # An output, not an input: a create needs a fresh key, never one taken from existing objects.
        create.outputs.append(
            OutputSlot(
                resource=item_slot.resource,
                pointer="",
                cardinality=Cardinality.ONE,
                status_code=status_code,
                body_field=key,
            )
        )


def bind_id_fields_to_returned_resources(operations: OperationMap) -> None:
    """Bind a placeholder `fromAirportId` to the resource responses return under `fromAirport`, by its `id`."""
    returned: dict[str, dict[str, ResourceDefinition]] = {}
    for operation in operations.values():
        for output in operation.outputs:
            segment = output.pointer.rstrip("/").rsplit("/", 1)[-1]
            if segment and not segment.startswith("*"):
                returned.setdefault(segment, {})[output.resource.name] = output.resource
    for operation in operations.values():
        for input_slot in operation.inputs:
            parameter_name = input_slot.parameter_name
            if input_slot.resource.source != DefinitionSource.PARAMETER_INFERENCE or not isinstance(
                parameter_name, str
            ):
                continue
            match = re.fullmatch(r"(.+?)_?(?:Id|ID|id)", parameter_name)
            if match is None:
                continue
            candidates = list(returned.get(match.group(1), {}).values())
            if len(candidates) != 1 or "id" not in candidates[0].fields:
                continue
            # The prefix must name the resource, or one generic association schema would take every `*Id` field.
            stem = naming.normalize_for_matching(candidates[0].name).removesuffix("response")
            if not naming.normalize_for_matching(match.group(1)).endswith(stem):
                continue
            input_slot.resource = candidates[0]
            input_slot.resource_field = "id"
            input_slot.is_suffix_matched = False


def bind_parent_keys_to_item_fields(schema: OpenApiSchema, operations: OperationMap) -> None:
    """Fill `{warehouseId}` of `/warehouses/{warehouseId}/shipments/{id}` from `Shipment.warehouseId` if unproduced."""
    produced = _produced_resource_names(operations)
    for label, operation in operations.items():
        item_parameter = naming.trailing_path_parameter(operation.path)
        if item_parameter is None:
            continue
        item = next(
            (
                input_slot.resource
                for input_slot in operation.inputs
                if input_slot.parameter_location == ParameterLocation.PATH
                and input_slot.parameter_name == item_parameter
            ),
            None,
        )
        if item is None or item.source < DefinitionSource.SCHEMA_WITH_PROPERTIES:
            continue
        declared = schema.find_operation_by_label(label)
        assert declared is not None
        path_parameters = {
            parameter.name: parameter
            for parameter in declared.iter_parameters()
            if parameter.location == ParameterLocation.PATH
        }
        # A `writeOnly` field is never in the item a response returns.
        unreadable = {
            field
            for response in declared.responses.iter_successful_responses()
            for field in write_only_fields(response, schema.root_resolver)
        }
        for input_slot in operation.inputs:
            parameter_name = input_slot.parameter_name
            if (
                input_slot.parameter_location == ParameterLocation.PATH
                and parameter_name != item_parameter
                and isinstance(parameter_name, str)
                and parameter_name in item.fields
                and input_slot.resource.source == DefinitionSource.PARAMETER_INFERENCE
                and input_slot.resource.name not in produced
                and parameter_name in path_parameters
                and parameter_name not in unreadable
                # A path segment takes one scalar of the declared type, not a list or a nested object.
                and _fits_path_parameter(item.types.get(parameter_name, set()), path_parameters[parameter_name])
            ):
                input_slot.resource = item
                input_slot.resource_field = parameter_name
                input_slot.is_parent_key = True


def _fits_path_parameter(field_types: set[str], parameter: OpenApiParameter) -> bool:
    if not field_types & _SCALAR_TYPES:
        return False
    declared_types = declared_parameter_types(parameter)
    if field_types & set(declared_types):
        return True
    # A plain string segment carries a number as is; a `format` or `pattern` (`uuid`) rules numbers out.
    parameter_schema = _parameter_schema(parameter)
    return (
        declared_types == ["string"]
        and isinstance(parameter_schema, dict)
        and "format" not in parameter_schema
        and "pattern" not in parameter_schema
    )


def _module_of(path: str) -> str:
    stripped = naming.strip_version_prefix(path).lstrip("/")
    head, _, _ = stripped.partition("/")
    return head


def _strip_trailing_digits(name: str) -> str:
    end = len(name)
    while end > 0 and name[end - 1].isdigit():
        end -= 1
    return name[:end] or name


def try_merge_input_resource(
    input_slot: InputSlot,
    producer_outputs: list[OutputSlot],
    resources: ResourceMap,
) -> tuple[str, str] | None:
    """Try to upgrade an input's resource to a producer's resource."""
    consumer_resource = input_slot.resource

    # Only upgrade resources without declared fields (low confidence) or suffix-matched inputs
    # Suffix-matched inputs may have matched a less specific resource (e.g., "Blog post" instead of "Blog post public")
    if consumer_resource.source >= DefinitionSource.SCHEMA_WITH_PROPERTIES and not input_slot.is_suffix_matched:
        return None

    # Try each producer output
    for output in producer_outputs:
        producer_resource = resources[output.resource.name]

        # Only merge to schema-defined resources (high confidence)
        if producer_resource.source != DefinitionSource.SCHEMA_WITH_PROPERTIES:
            continue

        # Try to match the input parameter to producer's fields
        param_name = input_slot.parameter_name
        # A nested body field (`recipient/clientId`) already names its own target, not the producer's resource.
        if not isinstance(param_name, str) or "/" in param_name:
            continue

        for resource_name in (input_slot.resource.name, producer_resource.name):
            matched_field = naming.find_matching_field(
                parameter=param_name,
                resource=resource_name,
                fields=producer_resource.fields,
            )

            if matched_field is not None:
                return producer_resource.name, matched_field

    return None


def find_producer_consumer_candidates(operations: OperationMap) -> list[tuple[str, str]]:
    """Find operation pairs that might produce/consume the same resource via REST patterns."""
    candidates = []

    # Group by base path to reduce comparisons
    paths: dict[str, list[str]] = {}
    for name, node in operations.items():
        base = _extract_base_path(node.path)
        paths.setdefault(base, []).append(name)

    # Within each path group, find POST/PUT -> GET/DELETE/PATCH patterns
    for names in paths.values():
        for producer_name in names:
            producer = operations[producer_name]
            # Producer must create/update and return data
            if producer.method not in ("post", "put") or not producer.outputs:
                continue

            for consumer_name in names:
                consumer = operations[consumer_name]
                # Consumer must have path parameters
                if not consumer.inputs:
                    continue
                # Paths must be related (collection + item pattern)
                if _is_collection_item_pattern(producer.path, consumer.path):
                    candidates.append((producer_name, consumer_name))

    return candidates


def _extract_base_path(path: str) -> str:
    """Extract collection path: /blog/posts/{id} -> /blog/posts."""
    parts = [p for p in path.split("/") if not p.startswith("{")]
    return "/".join(parts).rstrip("/")


def _is_collection_item_pattern(collection_path: str, item_path: str) -> bool:
    """Check if paths follow REST collection/item pattern."""
    # /blog/posts + /blog/posts/{postId}
    normalized_collection = collection_path.rstrip("/")
    normalized_item = item_path.rstrip("/")

    # Must start with collection path
    if not normalized_item.startswith(normalized_collection + "/"):
        return False

    # Extract the segment after collection path
    remainder = normalized_item[len(normalized_collection) + 1 :]

    # Must be a single path parameter: {paramName} with no slashes
    return (
        remainder.startswith("{")
        and remainder.endswith("}")
        and len(remainder) > 2  # Not empty {}
        and "/" not in remainder
    )
